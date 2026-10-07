from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import __version__
from .config import Settings, get_settings
from .db import Database
from .manager import MeasurementManager
from .schemas import MarkerRequest, MeasurementBulkExportRequest, MeasurementPatchRequest, MeasurementStartRequest
from .schemas import MeasurementPreviewRequest
from .schemas import BatteryReportRequest, BatteryMultiReportRequest
from .live_stream import LiveSubscription
from .storage.raw_store import RawStore
from .storage.event_reader import RawDataLimitError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.prepare()

    db = Database(settings.database_url)
    db.create_all()
    raw_store = RawStore(settings.data_dir)
    manager = MeasurementManager(settings, db, raw_store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = db
        app.state.raw_store = raw_store
        app.state.manager = manager
        manager.start_scheduler()
        yield
        manager.shutdown()

    app = FastAPI(
        title="PowerLab",
        version=__version__,
        description="100 kS/s power profiling and scheduled measurement management for Nordic PPK2.",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def mgr() -> MeasurementManager:
        return app.state.manager

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/version")
    async def version():
        return {"version": __version__}

    @app.get("/api/system")
    async def system_status():
        return mgr().system_status()

    @app.get("/api/devices")
    def devices():
        return {"ppk2": mgr().devices_status()}

    @app.websocket("/ws/devices")
    async def devices_ws(websocket: WebSocket):
        await websocket.accept()
        incoming = asyncio.create_task(websocket.receive_text())
        previous = None
        scan_failed = False
        try:
            while True:
                try:
                    devices = {"ppk2": await asyncio.to_thread(mgr().devices_status)}
                    scan_failed = False
                except Exception:
                    if not scan_failed:
                        logger.exception("USB device scan failed; retrying")
                    scan_failed = True
                else:
                    if devices != previous:
                        await websocket.send_json(devices)
                        previous = devices
                # Notice disconnections even when the inventory stays unchanged.
                done, _ = await asyncio.wait({incoming}, timeout=1.0)
                if done:
                    incoming.result()
                    incoming = asyncio.create_task(websocket.receive_text())
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            incoming.cancel()
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await incoming

    @app.get("/api/live")
    async def live():
        return mgr().live_overview()

    @app.websocket("/ws/live")
    async def live_ws(websocket: WebSocket):
        await measurement_live_ws(websocket, "*", 1500)

    @app.websocket("/ws/live/{measurement_id}")
    async def measurement_live_ws(websocket: WebSocket, measurement_id: str,
                                  max_points: int = Query(default=1500, ge=500, le=4000)):
        await websocket.accept()
        subscription = mgr().subscribe_live(measurement_id)

        async def send_updates():
            next_send = 0.0
            loop = asyncio.get_running_loop()
            while True:
                await subscription.wait()
                # Pace only after acquisition signals new data; never poll.
                delay = next_send - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if measurement_id == "*":
                    frame = await asyncio.to_thread(mgr().live_overview)
                else:
                    frame = await asyncio.to_thread(mgr().live_frame, measurement_id, subscription, max_points)
                await websocket.send_json(frame)
                next_send = loop.time() + 0.25

        sender = asyncio.create_task(send_updates())
        incoming = asyncio.create_task(websocket.receive_text())
        try:
            while True:
                done, _ = await asyncio.wait({sender, incoming}, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                incoming = asyncio.create_task(websocket.receive_text())
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            mgr().unsubscribe_live(measurement_id, subscription)
            for task in (sender, incoming):
                task.cancel()
            for task in (sender, incoming):
                with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await task

    @app.post("/api/measurement-previews")
    def start_preview(payload: MeasurementPreviewRequest):
        try:
            session = mgr().start_preview(payload)
            return {"preview_id": session.id}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/measurement-previews/{preview_id}")
    def update_preview(preview_id: str, payload: MeasurementPreviewRequest):
        try:
            session = mgr().get_preview(preview_id)
            session.reconfigure(payload)
            return {"preview_id": preview_id, "revision": session.engine.revision}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Vorschau nicht gefunden.") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/api/measurement-previews/{preview_id}", status_code=204)
    def stop_preview(preview_id: str):
        try:
            mgr().stop_preview(preview_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/measurement-previews/{preview_id}/pause")
    def pause_preview(preview_id: str):
        try:
            session = mgr().get_preview(preview_id)
            session.pause()
            return {"preview_id": preview_id, "paused": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Vorschau nicht gefunden.") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.websocket("/ws/measurement-previews/{preview_id}")
    async def preview_ws(websocket: WebSocket, preview_id: str):
        await websocket.accept()
        try:
            session = mgr().get_preview(preview_id)
        except KeyError:
            await websocket.close(code=1008, reason="Vorschau nicht gefunden")
            return
        subscription = LiveSubscription()
        with session.lock:
            duplicate = session.connected
            if not duplicate:
                session.connected = True
                session.subscription = subscription
        if duplicate:
            subscription.close()
            await websocket.close(code=1008, reason="Vorschau bereits verbunden")
            return
        subscription.notify()

        async def send_updates():
            while True:
                await subscription.wait()
                frame = await asyncio.to_thread(session.snapshot)
                spectral = frame['spectrogram']
                reset = subscription.epoch != frame['revision']
                frames = spectral['frames']
                if frames and subscription.spectral_cursor < frames[0]['end_sample'] - round(spectral['hop_s'] * frame['sample_rate_hz']):
                    reset = True
                spectral['reset'] = reset
                spectral['history_s'] = 60
                spectral['frames'] = frames if reset else [f for f in frames if f['end_sample'] > subscription.spectral_cursor]
                subscription.spectral_cursor = frames[-1]['end_sample'] if frames else -1
                subscription.epoch = frame['revision']
                await websocket.send_json(frame)
                if not frame['running'] and not frame['paused']:
                    return
                await asyncio.sleep(.25)

        sender = asyncio.create_task(send_updates())
        incoming = asyncio.create_task(websocket.receive_text())
        try:
            done, _ = await asyncio.wait({sender, incoming}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            subscription.close()
            session.subscription = None
            session.stop_event.set()
            for task in (sender, incoming):
                task.cancel()
            for task in (sender, incoming):
                with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await task
            cleanup = asyncio.create_task(asyncio.to_thread(mgr().stop_preview, preview_id))
            await asyncio.shield(cleanup)

    @app.post("/api/measurements")
    async def start_measurement(payload: MeasurementStartRequest):
        try:
            return mgr().start(payload)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/measurements/{measurement_id}/stop")
    def stop_measurement(measurement_id: str):
        try:
            return mgr().stop(measurement_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/measurements/{measurement_id}/marker")
    async def add_marker(measurement_id: str, payload: MarkerRequest):
        try:
            return mgr().add_marker(measurement_id, payload.label)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/api/measurements/{measurement_id}/scheduled")
    async def update_scheduled_measurement(measurement_id: str, payload: MeasurementStartRequest):
        try:
            return mgr().update_scheduled(measurement_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/measurements/{measurement_id}/cancel")
    async def cancel_scheduled_measurement(measurement_id: str):
        try:
            return mgr().cancel_scheduled(measurement_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/measurements")
    async def list_measurements(limit: int = Query(default=200, ge=1, le=2000)):
        return mgr().list_measurements(limit=limit)

    @app.post("/api/measurements/bulk/export")
    def export_measurements(payload: MeasurementBulkExportRequest):
        try:
            path = mgr().export_measurements(payload.measurement_ids, payload.kind)
            return FileResponse(
                path, filename="powerlab_measurements.zip", media_type="application/zip",
                background=BackgroundTask(path.unlink, missing_ok=True),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/measurements/{measurement_id}")
    async def get_measurement(measurement_id: str):
        try:
            return mgr().get_measurement(measurement_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc

    @app.patch("/api/measurements/{measurement_id}")
    async def patch_measurement(measurement_id: str, payload: MeasurementPatchRequest):
        try:
            return mgr().patch_measurement(measurement_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc

    @app.delete("/api/measurements/{measurement_id}", status_code=204)
    async def delete_measurement(measurement_id: str):
        try:
            mgr().delete_measurement(measurement_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.get('/api/measurements/{measurement_id}/cycle-energy')
    def measurement_cycle_energy(measurement_id: str,
                                duration_s: float | None = Query(default=None, gt=0, le=31_536_000_000, allow_inf_nan=False)):
        try:
            return mgr().cycle_energy(measurement_id, duration_s)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='Measurement not found') from exc

    @app.post('/api/battery-report')
    def battery_multi_report(payload: BatteryMultiReportRequest):
        from .battery_report import render_multi_report
        try:
            measurements = [mgr().get_measurement(s.measurement_id) for s in payload.sources]
            body = render_multi_report(measurements, payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail='Measurement not found') from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return Response(body, media_type='application/pdf', headers={
            'Content-Disposition': 'attachment; filename="battery_life_combined.pdf"'})

    @app.post('/api/measurements/{measurement_id}/battery-report')
    def battery_report(measurement_id: str, payload: BatteryReportRequest):
        from .battery_report import render_report
        try:
            measurement = mgr().get_measurement(measurement_id)
            body = render_report(measurement, measurement['cycle_energy'], payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail='Measurement not found') from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        safe_id = ''.join(c for c in measurement_id if c.isalnum() or c in '-_')
        return Response(body, media_type='application/pdf', headers={
            'Content-Disposition': f'attachment; filename="battery_life_{safe_id}.pdf"'})

    @app.get("/api/measurements/{measurement_id}/overview")
    async def measurement_overview(
        measurement_id: str,
        max_points: int = Query(default=20_000, ge=100, le=1_000_000),
    ):
        try:
            return mgr().overview(measurement_id, max_points=max_points)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc

    @app.get("/api/measurements/{measurement_id}/series")
    def measurement_series(
        measurement_id: str,
        start_s: float | None = Query(default=None, ge=0),
        end_s: float | None = Query(default=None, ge=0),
        max_points: int = Query(default=20_000, ge=500, le=100_000),
        view: Literal["auto", "overview", "detail"] = Query(default="auto"),
    ):
        try:
            return mgr().series(
                measurement_id,
                start_s=start_s,
                end_s=end_s,
                max_points=max_points,
                view=view,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc

    @app.get("/api/measurements/{measurement_id}/events/{event_id}")
    def event_raw(
        measurement_id: str,
        event_id: int,
        max_points: int = Query(default=50_000, ge=100, le=500_000),
        start_s: float | None = Query(default=None),
        end_s: float | None = Query(default=None),
        raw_only: bool = Query(default=False),
        adaptive_view: bool = Query(default=False),
    ):
        try:
            return mgr().event_raw(measurement_id, event_id, max_points=max_points, start_s=start_s, end_s=end_s, raw_only=raw_only, adaptive_view=adaptive_view)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Event not found") from exc
        except RawDataLimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

    @app.get("/api/measurements/{measurement_id}/events/{event_id}/csv")
    def event_csv(measurement_id: str, event_id: int):
        try:
            filename, body = mgr().raw_event_csv(measurement_id, event_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Event not found") from exc
        except RawDataLimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/measurements/{measurement_id}/export/{kind}")
    def export_measurement(measurement_id: str, kind: str):
        try:
            if kind == "pdf":
                filename, body = mgr().export_report_pdf(measurement_id)
                return Response(
                    content=body, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            if kind == "json":
                filename, body = mgr().export_metadata_json(measurement_id)
                return Response(
                    content=body,
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            if kind == "csv":
                filename, body = mgr().export_overview_csv(measurement_id)
                return Response(
                    content=body,
                    media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            if kind == "bundle":
                path = mgr().export_bundle(measurement_id)
                return FileResponse(path, filename=path.name, media_type="application/zip")
            raise HTTPException(status_code=400, detail="kind must be json, csv, bundle or pdf")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Measurement not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


app = create_app()
