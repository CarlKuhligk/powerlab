from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select

from .config import Settings
from .db import Database, Marker, Measurement, OverviewPoint, SleepSegment, WakeEvent, model_to_dict
from .display_summary import compact_summaries
from .live_stream import LiveSubscription
from .spectrogram import LiveSpectrogram
from .spectral_detection import SpectralComparison
from .measurement_preview import PreviewSession
from .ppk import NordicPPK2Driver, PowerProfilerDriver, SampleBatch, describe_ppk2_port, discover_ppk2_devices
from .recorder import OverviewData, RecorderSettings, SleepSegmentData, WakeEventData
from .threshold import ThresholdRecorder
from .cycle_energy import confirmed_cycle_energy
from .sleep_analysis import sleep_analysis
from .schemas import MeasurementPatchRequest, MeasurementStartRequest
from .storage.raw_store import RawStore

logger = logging.getLogger(__name__)

class EventCountReached(Exception):
    """Unwind the current USB batch at a confirmed recording boundary."""


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dt_for_sample(started_at: datetime, sample_index: int, sample_rate_hz: int) -> datetime:
    return _utc(started_at) + timedelta(seconds=sample_index / sample_rate_hz)


def _downsample_minmax(sample_index: np.ndarray, current: np.ndarray, digital: np.ndarray, max_points: int):
    n = len(current)
    if n <= max_points or max_points < 4:
        return sample_index, current, digital
    bins = max(1, max_points // 2)
    edges = np.linspace(0, n, bins + 1, dtype=np.int64)
    out_i, out_c, out_d = [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        block = current[a:b]
        lo = a + int(np.argmin(block)); hi = a + int(np.argmax(block))
        for pos in sorted({lo, hi}):
            out_i.append(int(sample_index[pos])); out_c.append(float(current[pos])); out_d.append(int(digital[pos]))
    return np.asarray(out_i, dtype=np.int64), np.asarray(out_c, dtype=np.float32), np.asarray(out_d, dtype=np.uint8)


def _downsample_series_points(points: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Downsample an irregular display series while preserving extrema.

    PowerLab never tries to ship the 100 kS/s acquisition stream to the browser.
    For a wide time range we keep a bounded number of display points and preserve
    local minima/maxima. Wake markers are returned independently and therefore
    remain exact even if the line itself is compacted.
    """
    if not points:
        return []
    points = sorted(points, key=lambda x: int(x["sample_index"]))
    # De-duplicate sample indices. Prefer raw/live/event points over summaries.
    priority = {"raw": 50, "live": 45, "event_peak": 40, "event_start": 35, "event_end": 35, "sleep": 10, "calibration": 5}
    dedup: dict[int, dict[str, Any]] = {}
    for point in points:
        idx = int(point["sample_index"])
        old = dedup.get(idx)
        if old is None or priority.get(str(point.get("kind", "")), 20) >= priority.get(str(old.get("kind", "")), 20):
            dedup[idx] = point
    points = [dedup[k] for k in sorted(dedup)]
    if len(points) <= max_points or max_points < 4:
        return points

    # Two extrema per bucket gives a Min/Max envelope with a strict point budget.
    bins = max(1, max_points // 2)
    edges = np.linspace(0, len(points), bins + 1, dtype=np.int64)
    out: list[dict[str, Any]] = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        block = points[a:b]
        values = np.asarray([float(x["current_ua"]) for x in block], dtype=np.float64)
        chosen = {int(np.argmin(values)), int(np.argmax(values))}
        for pos in sorted(chosen, key=lambda pos: int(block[pos]["sample_index"])):
            out.append(block[pos])
    if points[0]["sample_index"] != out[0]["sample_index"]:
        out.insert(0, points[0])
    if points[-1]["sample_index"] != out[-1]["sample_index"]:
        out.append(points[-1])
    return out[: max_points + 2]


@dataclass
class ActiveRun:
    measurement_id: str
    request: MeasurementStartRequest
    started_at: datetime
    driver: PowerProfilerDriver
    recorder: ThresholdRecorder
    planned_end_at: datetime | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    error: str | None = None
    event_id_by_sequence: dict[int, int] = field(default_factory=dict)
    preview: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=60_000))
    preview_history: list[dict[str, Any]] = field(default_factory=list)
    preview_segment: int = 0
    preview_next_sample: int | None = None
    preview_lock: threading.Lock = field(default_factory=threading.Lock)
    last_db_update: float = 0.0
    last_current_ua: float | None = None
    event_writers: dict[int, Any] = field(default_factory=dict)
    acquisition_finished_at: datetime | None = None
    finished_event: threading.Event = field(default_factory=threading.Event)
    watchdog: threading.Thread | None = None
    last_samples_at: float = field(default_factory=time.monotonic)
    phase: str = "starting"
    phase_started_at: float = field(default_factory=time.monotonic)
    confirmed_wakes: int = 0
    confirmed_sleeps: int = 0
    count_stop_sample: int | None = None
    recorded_samples: int = 0
    recorded_charge_uc: float = 0.0
    recorded_peak_ua: float = float("-inf")
    recorded_sleep_samples: int = 0
    recorded_sleep_sum: float = 0.0
    stream_metadata_revision: int = 0
    spectrogram: LiveSpectrogram | None = None
    spectral_comparison: SpectralComparison | None = None


class MeasurementManager:
    def __init__(self, settings: Settings, db: Database, raw_store: RawStore) -> None:
        self.settings = settings
        self.db = db
        self.raw_store = raw_store
        self._lock = threading.RLock()
        self._active: dict[str, ActiveRun] = {}
        self._previews: dict[str, PreviewSession] = {}
        self._live_subscribers: dict[str, set[LiveSubscription]] = {}
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None

    def start_scheduler(self) -> None:
        # A process restart cannot resume an open serial stream. Make stale sessions
        # visible in history instead of leaving them forever in RECORDING.
        with self.db.session() as s:
            stale = list(s.scalars(select(Measurement).where(Measurement.status.in_(["starting", "recording"]))))
            for row in stale:
                row.status = "failed"
                row.finished_at = datetime.now(timezone.utc)
                payload = json.loads(row.settings_json or "{}")
                payload["scheduler_error"] = "PowerLab restarted while the measurement was active"
                row.settings_json = json.dumps(payload)
        with self._lock:
            if self._scheduler_thread and self._scheduler_thread.is_alive():
                return
            self._scheduler_stop.clear()
            self._scheduler_thread = threading.Thread(target=self._scheduler_loop, name="powerlab-scheduler", daemon=True)
            self._scheduler_thread.start()

    def shutdown(self) -> None:
        self._scheduler_stop.set()
        thread = self._scheduler_thread
        if thread:
            thread.join(timeout=2.0)
        for preview_id in list(self._previews):
            try:
                self.stop_preview(preview_id)
            except Exception:
                logger.exception("Failed to stop preview %s", preview_id)
        with self._lock:
            ids = list(self._active)
        for measurement_id in ids:
            try:
                self.stop(measurement_id)
            except Exception:
                logger.exception("Failed to stop %s during shutdown", measurement_id)

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.wait(0.25):
            now = datetime.now(timezone.utc)
            with self.db.session() as s:
                scheduled = list(s.scalars(select(Measurement).where(Measurement.status == "scheduled")))
            for m in scheduled:
                start_at = _utc(m.scheduled_start_at)
                if not start_at or start_at > now:
                    continue
                try:
                    payload = json.loads(m.settings_json or "{}")
                    if payload.get("detection_mode", "legacy") not in {"threshold", "spectral_compare"}:
                        raise RuntimeError("Der bisherige Erkennungsmodus wurde entfernt. Bitte die Messung mit Stromschwellen und Mindestdauer neu planen.")
                    req = MeasurementStartRequest.model_validate(payload)
                    self._launch_existing(m.id, req)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Scheduled measurement %s failed to start", m.id)
                    with self.db.session() as s:
                        row = s.get(Measurement, m.id)
                        if row and row.status == "scheduled":
                            row.status = "failed"
                            row.finished_at = now
                            payload = json.loads(row.settings_json or "{}")
                            payload["scheduler_error"] = f"{type(exc).__name__}: {exc}"
                            row.settings_json = json.dumps(payload)
                    self._write_metadata_snapshot(m.id)
                    self._notify_live(m.id)

    def _build_driver(self, req: MeasurementStartRequest) -> PowerProfilerDriver:
        port = (req.port or "").strip()
        detected = {d["port"].upper(): d for d in discover_ppk2_devices()}
        if port.upper() not in detected:
            raise RuntimeError(f"PPK2 on {port or 'unknown port'} is not currently detected. Reconnect the PPK2 and refresh the device list.")
        return NordicPPK2Driver(port=port, meter_mode=req.meter_mode, voltage_mv=req.voltage_mv, sample_rate_hz=self.settings.sample_rate_hz)

    def _port_is_busy(self, port: str, ignore_id: str | None = None) -> bool:
        port = port.upper()
        with self._lock:
            return (any(mid != ignore_id and run.request.port and run.request.port.upper() == port and run.thread and run.thread.is_alive() for mid, run in self._active.items())
                    or any(run.request.port.upper() == port and run.thread.is_alive() for run in self._previews.values()))

    def start_preview(self, request) -> PreviewSession:
        with self._lock:
            for key, session in list(self._previews.items()):
                if not session.thread.is_alive() and not session.connected:
                    session.engine.dispose()
                    self._previews.pop(key, None)
            if self._port_is_busy(request.port):
                raise RuntimeError(f"PPK2 {request.port} wird bereits von einer Messung oder Vorschau verwendet.")
            session = PreviewSession(str(uuid.uuid4()), request, self.settings.sample_rate_hz,
                                     self._build_driver(request), self.settings.acquisition_timeout_s)
            self._previews[session.id] = session
            def expired(preview_id=session.id):
                with self._lock:
                    self._previews.pop(preview_id, None)
            session.on_expired = expired
            session.thread.start()
            return session

    def get_preview(self, preview_id) -> PreviewSession:
        with self._lock:
            return self._previews[preview_id]

    def stop_preview(self, preview_id) -> None:
        with self._lock:
            session = self._previews.get(preview_id)
        if session is None:
            return
        session.stop()
        with self._lock:
            self._previews.pop(preview_id, None)

    def _schedule_times(self, req: MeasurementStartRequest) -> tuple[datetime | None, datetime | None]:
        now = datetime.now(timezone.utc)
        start = _utc(req.scheduled_start_at) if req.start_mode == "scheduled" else now
        end = None
        if req.stop_mode == "duration" and req.duration_s:
            end = start + timedelta(seconds=req.duration_s)
        elif req.stop_mode == "end":
            end = _utc(req.scheduled_end_at)
        return start, end

    def _check_schedule_conflict(self, req: MeasurementStartRequest, ignore_id: str | None = None) -> None:
        if req.start_mode != "scheduled":
            return
        start, end = self._schedule_times(req)
        if not start:
            return
        with self.db.session() as s:
            rows = list(s.scalars(select(Measurement).where(Measurement.status == "scheduled", Measurement.port == req.port)))
        for row in rows:
            if row.id == ignore_id:
                continue
            other_start = _utc(row.scheduled_start_at)
            other_end = _utc(row.scheduled_end_at)
            if not other_start:
                continue
            this_end = end or datetime.max.replace(tzinfo=timezone.utc)
            that_end = other_end or datetime.max.replace(tzinfo=timezone.utc)
            if start < that_end and other_start < this_end:
                raise RuntimeError(f"PPK2 {req.port} is already reserved by '{row.name}' in this time window")

    def _store_new_measurement(self, measurement_id: str, req: MeasurementStartRequest, status: str) -> None:
        selected = describe_ppk2_port(req.port or "")
        start_at, end_at = self._schedule_times(req)
        initial_cfg = {"selected_device": selected, "requested_config": {"meter_mode": req.meter_mode, "voltage_mv": req.voltage_mv, "sample_rate_hz": self.settings.sample_rate_hz}}
        with self.db.session() as s:
            s.add(Measurement(
                id=measurement_id, name=req.name, project=req.project, device=req.device, firmware=req.firmware, notes=req.notes,
                driver="ppk2", port=req.port, meter_mode=req.meter_mode, voltage_mv=req.voltage_mv, sample_rate_hz=self.settings.sample_rate_hz,
                status=status, scheduled_start_at=start_at if req.start_mode == "scheduled" else None, scheduled_end_at=end_at,
                requested_duration_s=req.duration_s if req.stop_mode == "duration" else None, start_mode=req.start_mode, stop_mode=req.stop_mode,
                settings_json=json.dumps(req.model_dump(mode="json")), ppk2_id=selected.get("device_id") if selected else None,
                ppk2_config_json=json.dumps(initial_cfg),
            ))

    def start(self, req: MeasurementStartRequest) -> dict:
        measurement_id = str(uuid.uuid4())
        if req.start_mode == "scheduled":
            start_at, _ = self._schedule_times(req)
            if start_at and start_at <= datetime.now(timezone.utc):
                raise RuntimeError("Scheduled start time must be in the future")
            self._check_schedule_conflict(req)
            self._store_new_measurement(measurement_id, req, "scheduled")
            self._write_metadata_snapshot(measurement_id)
            self._notify_live(measurement_id)
            return self.get_measurement(measurement_id)

        if self._port_is_busy(req.port or ""):
            raise RuntimeError(f"PPK2 {req.port} is already used by another running measurement")
        self._store_new_measurement(measurement_id, req, "starting")
        try:
            self._launch_existing(measurement_id, req)
        except Exception:
            with self.db.session() as s:
                m = s.get(Measurement, measurement_id)
                if m:
                    m.status = "failed"
                    m.finished_at = datetime.now(timezone.utc)
            raise
        return self.get_measurement(measurement_id)

    def update_scheduled(self, measurement_id: str, req: MeasurementStartRequest) -> dict:
        if req.start_mode != "scheduled":
            raise RuntimeError("A pending measurement must keep start_mode=scheduled")
        start_at, end_at = self._schedule_times(req)
        if not start_at or start_at <= datetime.now(timezone.utc):
            raise RuntimeError("Scheduled start time must be in the future")
        self._check_schedule_conflict(req, ignore_id=measurement_id)
        selected = describe_ppk2_port(req.port or "")
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            if m.status != "scheduled":
                raise RuntimeError("Only pending scheduled measurements can be changed")
            m.name=req.name; m.project=req.project; m.device=req.device; m.firmware=req.firmware; m.notes=req.notes
            m.port=req.port; m.meter_mode=req.meter_mode; m.voltage_mv=req.voltage_mv
            m.scheduled_start_at=start_at; m.scheduled_end_at=end_at; m.requested_duration_s=req.duration_s if req.stop_mode == "duration" else None
            m.start_mode=req.start_mode; m.stop_mode=req.stop_mode; m.settings_json=json.dumps(req.model_dump(mode="json"))
            m.ppk2_id=selected.get("device_id") if selected else m.ppk2_id
            m.ppk2_config_json=json.dumps({"selected_device": selected, "requested_config": {"meter_mode": req.meter_mode, "voltage_mv": req.voltage_mv, "sample_rate_hz": self.settings.sample_rate_hz}})
        self._write_metadata_snapshot(measurement_id)
        self._notify_live(measurement_id)
        return self.get_measurement(measurement_id)

    def cancel_scheduled(self, measurement_id: str) -> dict:
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            if m.status != "scheduled":
                raise RuntimeError("Only pending scheduled measurements can be cancelled")
            m.status = "cancelled"
            m.finished_at = datetime.now(timezone.utc)
        self._write_metadata_snapshot(measurement_id)
        self._notify_live(measurement_id)
        return self.get_measurement(measurement_id)

    def _launch_existing(self, measurement_id: str, req: MeasurementStartRequest) -> None:
        with self._lock:
            if measurement_id in self._active and self._active[measurement_id].thread and self._active[measurement_id].thread.is_alive():
                return
            if self._port_is_busy(req.port or "", ignore_id=measurement_id):
                raise RuntimeError(f"PPK2 {req.port} is already used by another running measurement")
            if req.stop_mode == "end" and _utc(req.scheduled_end_at) and _utc(req.scheduled_end_at) <= datetime.now(timezone.utc):
                raise RuntimeError("Scheduled end time has already passed")
            driver = self._build_driver(req)

            recorder_settings = RecorderSettings(
                sample_rate_hz=self.settings.sample_rate_hz, detection_mode="threshold",
                sleep_threshold_ua=req.sleep_threshold_ua, wake_threshold_ua=req.wake_threshold_ua,
                sleep_min_s=req.sleep_min_s, wake_min_ms=req.wake_min_ms,
                pre_trigger_ms=req.pre_trigger_ms, post_trigger_ms=req.post_trigger_ms,
                sleep_checkpoint_s=req.sleep_checkpoint_s or self.settings.sleep_checkpoint_s,
            )
            active_holder: dict[str, ActiveRun] = {}

            def on_sleep(segment: SleepSegmentData) -> None:
                active = active_holder["active"]
                active.recorded_samples += segment.sample_count
                active.recorded_charge_uc += segment.charge_uc
                active.recorded_peak_ua = max(active.recorded_peak_ua, segment.max_ua)
                active.recorded_sleep_samples += segment.sample_count
                active.recorded_sleep_sum += segment.mean_ua * segment.sample_count
                with self.db.session() as s:
                    s.add(SleepSegment(measurement_id=measurement_id, start_sample=segment.start_sample, end_sample=segment.end_sample,
                        sample_count=segment.sample_count, mean_ua=segment.mean_ua, min_ua=segment.min_ua, max_ua=segment.max_ua,
                        std_ua=segment.std_ua, charge_uc=segment.charge_uc))

            def on_event(event: WakeEventData) -> None:
                active = active_holder["active"]
                active.recorded_samples += int(round(event.charge_uc * self.settings.sample_rate_hz / event.mean_ua)) if event.mean_ua else event.end_sample - event.trigger_sample + 1
                active.recorded_charge_uc += event.charge_uc
                active.recorded_peak_ua = max(active.recorded_peak_ua, event.peak_ua)
                writer = active.event_writers.get(event.sequence)
                raw_file = writer.finish() if writer else self.raw_store.write_event(measurement_id, event.sequence, event.sample_index, event.current_ua, event.digital)
                active.event_writers.pop(event.sequence, None)
                with self.db.session() as s:
                    db_event = WakeEvent(measurement_id=measurement_id, sequence=event.sequence, start_sample=event.start_sample,
                        trigger_sample=event.trigger_sample, end_sample=event.end_sample, duration_us=event.duration_us, peak_ua=event.peak_ua,
                        mean_ua=event.mean_ua, charge_uc=event.charge_uc, raw_file=raw_file, digital_mask_seen=event.digital_mask_seen, event_kind=event.event_kind)
                    s.add(db_event); s.flush(); active.event_id_by_sequence[event.sequence] = int(db_event.id)
                active.stream_metadata_revision += 1

            def on_overview(point: OverviewData) -> None:
                active = active_holder["active"]
                event_id = active.event_id_by_sequence.get(point.event_sequence) if point.event_sequence is not None else None
                with self.db.session() as s:
                    s.add(OverviewPoint(measurement_id=measurement_id, sample_index=point.sample_index, current_ua=point.current_ua, kind=point.kind, event_id=event_id))
                if point.kind in {"sleep_start", "wake_start", "sleep_validated", "wake_validated", "fft_sleep_start", "fft_wake_start"}:
                    active.stream_metadata_revision += 1

            def on_wake_chunk(sequence, idx, current, digital):
                active = active_holder["active"]
                writer = active.event_writers.get(sequence)
                if writer is None:
                    writer = self.raw_store.stream_event(measurement_id, sequence)
                    active.event_writers[sequence] = writer
                writer.append(idx, current, digital)

            def on_confirmed_state(kind, start_sample, validated_sample):
                active = active_holder["active"]
                if kind == "wake":
                    active.confirmed_wakes += 1
                    reached = req.stop_mode == "sleep_count" and active.confirmed_sleeps >= req.event_count
                else:
                    active.confirmed_sleeps += 1
                    reached = req.stop_mode == "wake_count" and active.confirmed_wakes >= req.event_count
                if reached:
                    active.count_stop_sample = start_sample
                    active.stop_event.set()
                    raise EventCountReached()

            def on_protocol_start(sample):
                active = active_holder["active"]
                active.started_at += timedelta(seconds=sample / self.settings.sample_rate_hz)
                # The start-search preview uses acquisition time. Once confirmed,
                # retain the plateau and switch all points to protocol time.
                with active.preview_lock:
                    active.spectrogram = None
                    active.spectral_comparison = None
                    def shift(points):
                        return [{**p, "sample_index": p["sample_index"] - sample,
                                 **({"end_sample": p["end_sample"] - sample} if "end_sample" in p else {}),
                                 "t_s": (p["sample_index"] - sample) / self.settings.sample_rate_hz}
                                for p in points if p["sample_index"] >= sample]
                    active.preview = deque(shift(active.preview), maxlen=active.preview.maxlen)
                    active.preview_history = shift(active.preview_history)
                    if active.preview_next_sample is not None:
                        active.preview_next_sample -= sample
                if req.stop_mode == "duration" and req.duration_s:
                    active.planned_end_at = active.started_at + timedelta(seconds=req.duration_s)
                with self.db.session() as s:
                    m = s.get(Measurement, measurement_id)
                    m.started_at = active.started_at
                    if active.planned_end_at:
                        m.scheduled_end_at = active.planned_end_at

            recorder = ThresholdRecorder(recorder_settings, on_protocol_start=on_protocol_start,
                                         on_sleep_segment=on_sleep, on_wake_event=on_event, on_overview=on_overview,
                                         on_wake_chunk=on_wake_chunk,
                                         on_confirmed_state=on_confirmed_state if req.stop_mode in {"wake_count", "sleep_count"} else None)
            active = ActiveRun(measurement_id=measurement_id, request=req, started_at=datetime.now(timezone.utc), driver=driver, recorder=recorder)
            active.preview = deque(maxlen=max(2_000, self.settings.live_preview_hz * 2 * 120))
            if req.stop_mode == "end":
                active.planned_end_at = _utc(req.scheduled_end_at)
            active_holder["active"] = active
            active.thread = threading.Thread(target=self._worker, name=f"powerlab-{measurement_id[:8]}", args=(active,), daemon=True)
            self._active[measurement_id] = active
            with self.db.session() as s:
                m = s.get(Measurement, measurement_id)
                if m:
                    m.status = "starting"
            active.thread.start()
            active.watchdog = threading.Thread(target=self._watch_run, name=f"powerlab-watch-{measurement_id[:8]}", args=(active,), daemon=True)
            active.watchdog.start()
            self._notify_live(measurement_id)

    def _fail_run(self, active: ActiveRun, reason: str) -> None:
        # Publish failure before cleanup, which may itself block on disconnected hardware.
        with self._lock:
            if active.finished_event.is_set() or self._active.get(active.measurement_id) is not active:
                return
            if active.error is None:
                active.error = reason
            active.stop_event.set()
            if active.acquisition_finished_at is None:
                active.acquisition_finished_at = datetime.now(timezone.utc)
            with self.db.session() as s:
                m = s.get(Measurement, active.measurement_id)
                if m:
                    m.status = "failed"
                    m.finished_at = active.acquisition_finished_at
                    payload = json.loads(m.settings_json or "{}")
                    payload["recording_error"] = active.error
                    m.settings_json = json.dumps(payload)
        logger.error("Measurement %s aborted: %s", active.measurement_id, active.error)
        self._notify_live(active.measurement_id)
        try:
            self._write_metadata_snapshot(active.measurement_id)
        except Exception:
            logger.exception("Failed to write acquisition failure metadata")

    def _watch_run(self, active: ActiveRun) -> None:
        interval = min(0.25, self.settings.acquisition_timeout_s / 4, self.settings.worker_timeout_s / 4)
        while not active.finished_event.wait(interval):
            if active.error:
                return
            now = time.monotonic()
            if active.phase == "reading":
                age = now - active.last_samples_at
                timeout = self.settings.acquisition_timeout_s
                reason = f"PPK2-Datenstrom unterbrochen: seit {timeout:g} Sekunden keine verwertbaren Samples."
            else:
                age = now - active.phase_started_at
                timeout = self.settings.worker_timeout_s
                reason = f"Aufzeichnung blockiert ({active.phase}): seit {timeout:g} Sekunden kein Fortschritt."
            if age >= timeout:
                try:
                    self._fail_run(active, reason)
                except Exception:
                    logger.exception("Failed to persist acquisition watchdog failure")
                return

    def _worker(self, active: ActiveRun) -> None:
        status = "completed"
        try:
            active.driver.start()
            if active.stop_event.is_set():
                return
            driver_metadata = active.driver.metadata()
            if driver_metadata:
                with self.db.session() as s:
                    m = s.get(Measurement, active.measurement_id)
                    if m:
                        m.ppk2_id = str(driver_metadata.get("device_id") or m.ppk2_id or active.request.port or "")
                        m.ppk2_config_json = json.dumps(driver_metadata)
                        settings_payload = json.loads(m.settings_json or "{}")
                        settings_payload["resolved_driver"] = driver_metadata
                        m.settings_json = json.dumps(settings_payload)
                self._write_metadata_snapshot(active.measurement_id)
            active.started_at = datetime.now(timezone.utc)
            with self._lock:
                if active.stop_event.is_set():
                    return
                with self.db.session() as s:
                    m = s.get(Measurement, active.measurement_id)
                    if m:
                        m.started_at = active.started_at; m.status = "recording"
                        if active.planned_end_at: m.scheduled_end_at = active.planned_end_at
                active.last_samples_at = time.monotonic()
                active.phase = "reading"
            self._notify_live(active.measurement_id)
            while not active.stop_event.is_set():
                if active.planned_end_at and datetime.now(timezone.utc) >= active.planned_end_at:
                    active.stop_event.set(); break
                start_sample = active.recorder.sample_index
                batch = active.driver.read_batch()
                if active.stop_event.is_set() and active.error:
                    break
                if batch is None or batch.size == 0:
                    active.stop_event.wait(0.001)
                    continue
                active.phase_started_at = time.monotonic()
                active.phase = "processing"
                active.recorder.process(batch)
                active.last_current_ua = float(batch.currents_ua[-1])
                self._append_preview(active, start_sample, batch)
                now = time.monotonic()
                if now - active.last_db_update >= 1.0:
                    self._update_running_stats(active); active.last_db_update = now
                active.last_samples_at = time.monotonic()
                active.phase = "reading"
        except EventCountReached:
            pass
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            self._fail_run(active, f"{type(exc).__name__}: {exc}")
            logger.exception("Measurement worker failed")
        finally:
            active.phase_started_at = time.monotonic()
            active.phase = "finalizing"
            # Stop acquisition before closing the remaining small file buffer.
            tail = None
            try: tail = active.driver.stop()
            except Exception as exc:
                self._fail_run(active, f"Stop failed: {type(exc).__name__}: {exc}")
                status = "failed"
            if active.acquisition_finished_at is None:
                active.acquisition_finished_at = datetime.now(timezone.utc)
            if active.count_stop_sample is None and isinstance(tail, SampleBatch) and tail.size:
                try:
                    start_sample = active.recorder.sample_index
                    active.recorder.process(tail)
                    active.last_current_ua = float(tail.currents_ua[-1])
                    self._append_preview(active, start_sample, tail)
                except EventCountReached:
                    pass
                except Exception as exc:
                    self._fail_run(active, f"Final samples failed: {type(exc).__name__}: {exc}"); status = "failed"
            try:
                if active.count_stop_sample is None:
                    active.recorder.finish()
                else:
                    self._finish_count_stop(active)
            except EventCountReached:
                self._finish_count_stop(active)
            except Exception as exc:
                self._fail_run(active, f"Recorder finalization failed: {type(exc).__name__}: {exc}"); status = "failed"
            for writer in active.event_writers.values():
                try: writer.abort()
                except Exception as exc:
                    self._fail_run(active, f"Wake writer failed: {type(exc).__name__}: {exc}"); status = "failed"
            try:
                self._finalize_measurement(active, "failed" if active.error else status)
            except Exception as exc:
                self._fail_run(active, f"Measurement finalization failed: {type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    self._active.pop(active.measurement_id, None)
                    active.finished_event.set()
                self._notify_live(active.measurement_id)

    def _finish_count_stop(self, active: ActiveRun) -> None:
        """Discard confirmation-only samples and the newly starting phase."""
        r = active.recorder
        if isinstance(r, ThresholdRecorder):
            for event in list(r._pending):
                if event.sequence not in active.event_id_by_sequence and event.end is not None and event.end < active.count_stop_sample:
                    r._emit(event)
            r._pending.clear()
        r.total_samples = active.recorded_samples
        r.total_sum_ua = active.recorded_charge_uc * self.settings.sample_rate_hz
        r.total_charge_uc = active.recorded_charge_uc
        r.peak_ua = active.recorded_peak_ua
        r.detected_lost_samples = max(0, active.count_stop_sample - r.total_samples)
        r.sample_index = active.count_stop_sample + (r.protocol_start_sample or 0) if isinstance(r, ThresholdRecorder) else active.count_stop_sample
        active.acquisition_finished_at = _dt_for_sample(active.started_at, active.count_stop_sample, self.settings.sample_rate_hz)
        with active.preview_lock:
            active.preview = deque((p for p in active.preview if p["sample_index"] < active.count_stop_sample), maxlen=active.preview.maxlen)
            active.preview_history = [p for p in active.preview_history if p["sample_index"] < active.count_stop_sample]

    def _append_preview(self, active: ActiveRun, start_sample: int, batch: SampleBatch) -> None:
        if batch.size == 0:
            return
        if isinstance(active.recorder, ThresholdRecorder):
            origin = active.recorder.protocol_start_sample
            origin = origin or 0
            ticks = batch.sample_ticks if batch.sample_ticks is not None else np.arange(start_sample, start_sample + batch.size)
            keep = ticks >= origin
            if not np.any(keep):
                return
            batch = SampleBatch(batch.currents_ua[keep], batch.digital[keep], ticks[keep] - origin)
            start_sample = int(batch.sample_ticks[0])
        factor = max(1, int(self.settings.sample_rate_hz / max(1, self.settings.live_preview_hz)))
        points = []
        summaries = []
        indices = batch.sample_ticks if batch.sample_ticks is not None else np.arange(start_sample, start_sample + batch.size, dtype=np.int64)
        boundaries = np.concatenate(([0], np.flatnonzero(np.diff(indices) != 1) + 1, [batch.size]))
        for begin, end in zip(boundaries[:-1], boundaries[1:]):
            if active.preview_next_sample != int(indices[begin]):
                active.preview_segment += 1
            line_key = f"acquisition-{active.preview_segment}"
            for a in range(int(begin), int(end), factor):
                b = min(int(end), a + factor); block = batch.currents_ua[a:b]
                lo = a + int(np.argmin(block)); hi = a + int(np.argmax(block))
                count = b - a
                total = float(np.sum(block, dtype=np.float64))
                summaries.append({
                    "sample_index": int(indices[a]), "end_sample": int(indices[b - 1]),
                    "sample_count": count, "sum_ua": total, "current_ua": total / count,
                    "min_ua": float(block[lo - a]), "max_ua": float(block[hi - a]),
                    "first_ua": float(block[0]), "last_ua": float(block[-1]),
                    "line_key": line_key, "kind": "mean",
                })
                positions = {lo, hi}
                if a == begin: positions.add(int(begin))
                if b == end: positions.add(int(end) - 1)
                for pos in sorted(positions):
                    si = int(indices[pos])
                    points.append({"sample_index": si, "t_s": si / self.settings.sample_rate_hz,
                        "current_ua": float(batch.currents_ua[pos]), "digital": int(batch.digital[pos]),
                        "line_key": line_key})
            active.preview_next_sample = int(indices[end - 1]) + 1
        with active.preview_lock:
            active.preview.extend(points)
            if active.spectrogram is None:
                active.spectrogram = LiveSpectrogram(self.settings.sample_rate_hz)
            active.spectrogram.append(indices, batch.currents_ua)
            spectral_markers = []
            if active.request.detection_mode == "spectral_compare":
                if active.spectral_comparison is None:
                    active.spectral_comparison = SpectralComparison(self.settings.sample_rate_hz, active.request.spectral_margin_db)
                if getattr(active.recorder, "protocol_start_sample", None) is not None:
                    for frame in active.spectrogram.frames:
                        marker = active.spectral_comparison.observe(frame, active.spectrogram.frequencies,
                            active.spectrogram.hop / self.settings.sample_rate_hz, active.recorder.state == "SLEEP")
                        if marker:
                            spectral_markers.append(marker)
            active.preview_history.extend(summaries)
            # Preserve the entire time span without retaining a growing raw stream.
            # Merge actual sums/counts rather than averaging selected extrema.
            if len(active.preview_history) > 80_000:
                active.preview_history = compact_summaries(active.preview_history, max_points=40_000)
        if spectral_markers:
            with self.db.session() as session:
                for marker in spectral_markers:
                    session.add(OverviewPoint(measurement_id=active.measurement_id, **marker))
            active.stream_metadata_revision += 1
        self._notify_live(active.measurement_id)

    def _update_running_stats(self, active: ActiveRun) -> None:
        r = active.recorder
        with self.db.session() as s:
            m = s.get(Measurement, active.measurement_id)
            if m:
                if isinstance(r, ThresholdRecorder):
                    payload = json.loads(m.settings_json or "{}")
                    payload["detection_result"] = r.detection_info()
                    if active.spectral_comparison is not None:
                        payload["spectral_result"] = active.spectral_comparison.snapshot()
                    m.settings_json = json.dumps(payload)
                m.total_samples=r.total_samples; m.detected_lost_samples=r.detected_lost_samples; m.wake_count=r.wake_count
                m.sleep_current_ua=r.sleep_current_ua; m.average_current_ua=r.average_current_ua
                m.peak_current_ua=None if r.peak_ua == float("-inf") else r.peak_ua; m.total_charge_uc=r.total_charge_uc

    def _finalize_measurement(self, active: ActiveRun, status: str) -> None:
        r = active.recorder
        with self._lock, self.db.session() as s:
            m = s.get(Measurement, active.measurement_id)
            if m:
                if isinstance(r, ThresholdRecorder):
                    payload = json.loads(m.settings_json or "{}")
                    payload["detection_result"] = r.detection_info()
                    if active.spectral_comparison is not None:
                        payload["spectral_result"] = active.spectral_comparison.snapshot()
                    m.settings_json = json.dumps(payload)
                    if r.protocol_start_sample is None:
                        status = "no_sleep"
                        m.started_at = None
                m.status="failed" if active.error else status; m.finished_at=active.acquisition_finished_at or datetime.now(timezone.utc); m.total_samples=r.total_samples
                m.detected_lost_samples=r.detected_lost_samples; m.wake_count=r.wake_count; m.sleep_current_ua=r.sleep_current_ua
                m.average_current_ua=r.average_current_ua; m.peak_current_ua=None if r.peak_ua == float("-inf") else r.peak_ua
                m.total_charge_uc=r.total_charge_uc
                if active.count_stop_sample is not None:
                    m.sleep_current_ua = active.recorded_sleep_sum / active.recorded_sleep_samples if active.recorded_sleep_samples else None
                    payload = json.loads(m.settings_json or "{}")
                    payload["event_count_result"] = {"confirmed_wakes": active.confirmed_wakes, "confirmed_sleeps": active.confirmed_sleeps, "stop_sample": active.count_stop_sample}
                    m.settings_json = json.dumps(payload)
        self._write_metadata_snapshot(active.measurement_id)

    def stop(self, measurement_id: str | None = None) -> dict:
        with self._lock:
            if measurement_id is None:
                live = [mid for mid, a in self._active.items() if a.thread and a.thread.is_alive()]
                if len(live) != 1: raise RuntimeError("measurement_id is required when zero or multiple measurements are running")
                measurement_id = live[0]
            active = self._active.get(measurement_id)
            if not active or not active.thread or not active.thread.is_alive(): raise RuntimeError("Measurement is not running")
            active.stop_event.set(); thread = active.thread
        self._notify_live(active.measurement_id)
        thread.join(timeout=10.0)
        if thread.is_alive(): raise RuntimeError("Measurement worker did not stop within 10 seconds")
        return self.get_measurement(measurement_id)

    def subscribe_live(self, measurement_id: str) -> LiveSubscription:
        subscription = LiveSubscription()
        with self._lock:
            self._live_subscribers.setdefault(measurement_id, set()).add(subscription)
        subscription.notify()
        return subscription

    def unsubscribe_live(self, measurement_id: str, subscription: LiveSubscription) -> None:
        subscription.close()
        with self._lock:
            subscribers = self._live_subscribers.get(measurement_id)
            if subscribers is not None:
                subscribers.discard(subscription)
                if not subscribers:
                    self._live_subscribers.pop(measurement_id, None)

    def _notify_live(self, measurement_id: str) -> None:
        with self._lock:
            subscribers = tuple(self._live_subscribers.get(measurement_id, ())) + tuple(self._live_subscribers.get("*", ()))
        for subscription in subscribers:
            subscription.notify()

    def live_frame(self, measurement_id: str, subscription: LiveSubscription, max_points: int = 1500) -> dict:
        """Send new weighted blocks; reset on epoch changes or overlapping compaction.

        Each viewer holds only a cursor and one notification. A slow viewer reads
        compacted acquisition history rather than accumulating USB batches.
        """
        snapshot = self.active_snapshot(measurement_id, include_preview=False)
        if not snapshot["running"]:
            return {"type": "live", "snapshot": snapshot, "series": None, "reset": False}
        with self._lock:
            active = self._active.get(measurement_id)
        if active is None:
            return {"type": "live", "snapshot": self.active_snapshot(measurement_id, include_preview=False), "series": None, "reset": False}
        with active.preview_lock:
            history = list(active.preview_history)
            spectral = active.spectrogram.snapshot() if active.spectrogram else None
            epoch = (active.started_at, active.stream_metadata_revision,
                     getattr(active.recorder, "protocol_start_sample", None))
        points = [p for p in history if p["end_sample"] > subscription.cursor]
        reset = subscription.epoch != epoch or bool(points and points[0]["sample_index"] <= subscription.cursor)
        if reset:
            series = self.series(measurement_id, max_points=max_points, view="overview")
            series['valid_wake_phases'] = self.cycle_energy(measurement_id)['valid_wake_phases']
            # Use the same history snapshot as the cursor, even if acquisition
            # advances while the metadata query runs.
            points = history
        else:
            series = {"measurement_id": measurement_id, "events": [], "state_markers": []}
        points = compact_summaries(points, max_points=max_points)
        rate = self.settings.sample_rate_hz
        series.update(summary_points=[{**p, "t_s": p["sample_index"] / rate,
                                      "end_s": p["end_sample"] / rate} for p in points],
                      display_mode="summary", points=[], history_points=[], live_points=[],
                      latest_s=(history[-1]["end_sample"] / rate if history else 0),
                      phase="startup" if isinstance(active.recorder, ThresholdRecorder) and epoch[2] is None else "protocol",
                      max_points=max_points, point_count=len(points))
        subscription.cursor = history[-1]["end_sample"] if history else -1
        if spectral is not None:
            frames = spectral["frames"]
            spectral_reset = reset or (bool(frames) and subscription.spectral_cursor < frames[0]["end_sample"] - round(spectral["hop_s"] * rate))
            spectral["reset"] = spectral_reset
            spectral["frames"] = frames if spectral_reset else [f for f in frames if f["end_sample"] > subscription.spectral_cursor]
            subscription.spectral_cursor = frames[-1]["end_sample"] if frames else -1
            series["spectrogram"] = spectral
        subscription.epoch = epoch
        return {"type": "live", "snapshot": snapshot, "series": series, "reset": reset}

    def active_snapshot(self, measurement_id: str, *, include_preview: bool = True) -> dict:
        with self._lock: active = self._active.get(measurement_id)
        if not active: return {"running": False, "measurement_id": measurement_id}
        r = active.recorder
        with active.preview_lock: preview = list(active.preview)[-4000:] if include_preview else []
        return {"running": bool(active.thread and active.thread.is_alive() and not active.error), "measurement_id": active.measurement_id,
            "stopping": active.stop_event.is_set(),
            "name": active.request.name, "started_at": _iso(active.started_at), "planned_end_at": _iso(active.planned_end_at),
            "detection": r.detection_info() if isinstance(r, ThresholdRecorder) else None,
            "spectral_comparison": active.spectral_comparison.snapshot() if active.spectral_comparison else None,
            "sample_rate_hz": self.settings.sample_rate_hz, "state": r.state, "current_ua": active.last_current_ua, "baseline_ua": r.baseline_ua, "threshold_ua": r.threshold_ua,
            "total_samples": r.total_samples, "detected_lost_samples": r.detected_lost_samples, "data_coverage_pct": r.data_coverage_pct,
            "wake_count": r.wake_count, "average_current_ua": r.average_current_ua,
            "peak_current_ua": None if r.peak_ua == float("-inf") else r.peak_ua, "total_charge_uc": r.total_charge_uc,
            "energy_uwh": r.total_charge_uc * active.request.voltage_mv / 1000.0 / 3600.0, "error": active.error,
            "driver": "ppk2", "port": active.request.port, "ppk2_id": active.driver.metadata().get("device_id"), "preview": preview}

    def live_overview(self) -> dict:
        now = datetime.now(timezone.utc)
        with self._lock:
            running = [{"measurement_id": a.measurement_id, "name": a.request.name, "port": a.request.port,
                "ppk2_id": a.driver.metadata().get("device_id") or a.request.port, "started_at": _iso(a.started_at),
                "planned_end_at": _iso(a.planned_end_at), "state": a.recorder.state, "wake_count": a.recorder.wake_count,
                "stop_mode": a.request.stop_mode, "event_count": a.request.event_count,
                "confirmed_wakes": a.confirmed_wakes, "confirmed_sleeps": a.confirmed_sleeps,
                "current_ua": a.last_current_ua, "status": "recording"} for a in self._active.values() if a.thread and a.thread.is_alive() and not a.error]
        with self.db.session() as s:
            pending = list(s.scalars(select(Measurement).where(Measurement.status == "scheduled").order_by(Measurement.scheduled_start_at)))
        return {"server_time": _iso(now), "running": running, "scheduled": [self._serialize_measurement(x) for x in pending]}

    def devices_status(self) -> list[dict[str, Any]]:
        devices = discover_ppk2_devices()
        with self._lock:
            by_port = {a.request.port.upper(): a for a in self._active.values() if a.request.port and a.thread and a.thread.is_alive()}
            previews = {p.request.port.upper(): p for p in self._previews.values() if p.thread.is_alive()}
        for d in devices:
            a = by_port.get(d["port"].upper())
            d["busy"] = bool(a); d["active_measurement_id"] = a.measurement_id if a else None; d["active_measurement_name"] = a.request.name if a else None
            preview = previews.get(d["port"].upper())
            if preview:
                d.update(busy=True, active_preview_id=preview.id, active_measurement_name="Messvorschau")
        return devices

    def add_marker(self, measurement_id: str | None, label: str) -> dict:
        with self._lock:
            if measurement_id is None:
                live = [mid for mid, a in self._active.items() if a.thread and a.thread.is_alive()]
                if len(live) != 1: raise RuntimeError("measurement_id is required when zero or multiple measurements are running")
                measurement_id = live[0]
            active = self._active.get(measurement_id)
        if not active or not active.thread or not active.thread.is_alive(): raise RuntimeError("Measurement is not running")
        if isinstance(active.recorder, ThresholdRecorder) and active.recorder.protocol_start_sample is None:
            raise RuntimeError("Sleep noch nicht bestätigt; das Messprotokoll hat noch nicht begonnen")
        with self.db.session() as s:
            marker = Marker(measurement_id=measurement_id, sample_index=active.recorder.timeline_samples, label=label)
            s.add(marker); s.flush(); return model_to_dict(marker)

    def list_measurements(self, limit: int = 200) -> list[dict]:
        return [self._serialize_measurement(m) for m in self.db.list_measurements(limit=limit)]

    def get_measurement(self, measurement_id: str) -> dict:
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            payload = self._serialize_measurement(m)
            stored_events = list(s.scalars(
                    select(WakeEvent)
                    .where(WakeEvent.measurement_id == measurement_id)
                    .order_by(WakeEvent.sequence)
                ))
            payload["events"] = [self._serialize_event(e, m) for e in stored_events]
            payload["background_events"] = [e for e in payload["events"] if e["event_kind"] == "background"]
            payload["events"] = [e for e in payload["events"] if e["event_kind"] != "background"]
            payload["markers"] = [
                self._serialize_marker(x, m)
                for x in s.scalars(
                    select(Marker)
                    .where(Marker.measurement_id == measurement_id)
                    .order_by(Marker.sample_index)
                )
            ]
            payload['cycle_energy'] = self._cycle_energy_for_session(s, m)
            segments = list(s.scalars(select(SleepSegment).where(
                SleepSegment.measurement_id == measurement_id).order_by(SleepSegment.start_sample)))
            payload['sleep_analysis'] = sleep_analysis(
                m.sample_rate_hz or self.settings.sample_rate_hz, m.voltage_mv, segments,
                [e for e in stored_events if e.event_kind == 'background'], payload['cycle_energy'])
            return payload

    def _cycle_energy_for_session(self, session, measurement, custom_duration_s=None):
        mid = measurement.id
        events = list(session.scalars(select(WakeEvent).where(WakeEvent.measurement_id == mid)))
        segments = list(session.scalars(select(SleepSegment).where(SleepSegment.measurement_id == mid)))
        points = list(session.scalars(select(OverviewPoint).where(
            OverviewPoint.measurement_id == mid,
            OverviewPoint.kind.in_(['sleep_start', 'sleep_validated', 'wake_start', 'wake_validated']))))
        return confirmed_cycle_energy(measurement.sample_rate_hz or self.settings.sample_rate_hz,
                                      measurement.voltage_mv, events, segments, points, custom_duration_s)

    def cycle_energy(self, measurement_id, custom_duration_s=None):
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if m is None:
                raise KeyError(measurement_id)
            return self._cycle_energy_for_session(s, m, custom_duration_s)

    def patch_measurement(self, measurement_id: str, patch: MeasurementPatchRequest) -> dict:
        changes = patch.model_dump(exclude_none=True)
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            for key, value in changes.items():
                setattr(m, key, value)
        payload = self.get_measurement(measurement_id)
        self._write_metadata_snapshot(measurement_id)
        return payload

    def _write_metadata_snapshot(self, measurement_id: str) -> None:
        try:
            self.raw_store.write_metadata(measurement_id, self.get_measurement(measurement_id))
        except Exception:  # noqa: BLE001
            logger.exception("Failed to write measurement metadata snapshot for %s", measurement_id)

    def delete_measurement(self, measurement_id: str) -> None:
        with self._lock:
            active = self._active.get(measurement_id)
            if active and active.thread and active.thread.is_alive():
                raise RuntimeError("Cannot delete a running measurement")
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            s.delete(m)
        mdir = self.raw_store.measurements_dir / measurement_id
        if mdir.exists():
            shutil.rmtree(mdir)

    def _serialize_measurement(self, m: Measurement) -> dict:
        duration_s = None
        if m.started_at:
            end = m.finished_at or datetime.now(timezone.utc)
            start = m.started_at
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            duration_s = max(0.0, (end - start).total_seconds())
        return {
            "id": m.id,
            "name": m.name,
            "project": m.project,
            "device": m.device,
            "firmware": m.firmware,
            "notes": m.notes,
            "driver": m.driver,
            "port": m.port,
            "meter_mode": m.meter_mode,
            "voltage_mv": m.voltage_mv,
            "sample_rate_hz": m.sample_rate_hz,
            "sample_period_us": 1_000_000 / m.sample_rate_hz,
            "status": m.status,
            "error": json.loads(m.settings_json or "{}").get("recording_error") or json.loads(m.settings_json or "{}").get("scheduler_error"),
            "start_mode": m.start_mode or "now",
            "stop_mode": m.stop_mode or "manual",
            "event_count": json.loads(m.settings_json or "{}").get("event_count"),
            "scheduled_start_at": _iso(m.scheduled_start_at),
            "scheduled_end_at": _iso(m.scheduled_end_at),
            "requested_duration_s": m.requested_duration_s,
            "started_at": _iso(m.started_at),
            "finished_at": _iso(m.finished_at),
            "created_at": _iso(m.created_at),
            "duration_s": duration_s,
            "total_samples": m.total_samples,
            "detected_lost_samples": m.detected_lost_samples,
            "data_coverage_pct": (100.0 * m.total_samples / (m.total_samples + m.detected_lost_samples)) if (m.total_samples + m.detected_lost_samples) else 100.0,
            "wake_count": m.wake_count,
            "sleep_current_ua": m.sleep_current_ua,
            "average_current_ua": m.average_current_ua,
            "peak_current_ua": m.peak_current_ua,
            "total_charge_uc": m.total_charge_uc,
            "energy_uwh": m.total_charge_uc * (m.voltage_mv / 1000.0) / 3600.0,
            "ppk2_id": m.ppk2_id,
            "ppk2_config": json.loads(m.ppk2_config_json or "{}"),
            "settings": json.loads(m.settings_json or "{}"),
        }

    def _serialize_event(self, e: WakeEvent, m: Measurement) -> dict:
        trigger_dt = _dt_for_sample(m.started_at, e.trigger_sample, m.sample_rate_hz) if m.started_at else None
        return {
            "id": e.id,
            "sequence": e.sequence,
            "start_sample": e.start_sample,
            "trigger_sample": e.trigger_sample,
            "end_sample": e.end_sample,
            "trigger_at": _iso(trigger_dt),
            "duration_us": e.duration_us,
            "peak_ua": e.peak_ua,
            "mean_ua": e.mean_ua,
            "charge_uc": e.charge_uc,
            "digital_mask_seen": e.digital_mask_seen,
            "event_kind": e.event_kind,
        }

    def _serialize_marker(self, marker: Marker, m: Measurement) -> dict:
        at = _dt_for_sample(m.started_at, marker.sample_index, m.sample_rate_hz) if m.started_at else None
        return {
            "id": marker.id,
            "sample_index": marker.sample_index,
            "label": marker.label,
            "at": _iso(at),
        }

    def _history_analysis(
        self,
        m: Measurement,
        sleep_segments: list[SleepSegment],
        events: list[WakeEvent],
        background_events: list[WakeEvent] = (),
    ) -> dict:
        """Compute cycle-oriented statistics for completed measurement history.

        The history overview deliberately describes device states instead of
        approximating a long 100 kS/s current trace. Sleep current is weighted by
        recorded sleep samples; wake current is weighted by wake duration. Period
        means trigger-to-trigger, which is the useful cadence for periodic ULP
        applications.
        """
        sample_rate = float(m.sample_rate_hz or self.settings.sample_rate_hz)

        sleep_samples = sum(max(0, int(seg.sample_count or 0)) for seg in sleep_segments)
        avg_sleep_current = None
        if sleep_samples:
            avg_sleep_current = sum(
                float(seg.mean_ua) * max(0, int(seg.sample_count or 0))
                for seg in sleep_segments
            ) / sleep_samples

        # Background pulses are part of sleep consumption; learned pulses are contiguous.
        background_samples = sum(round(e.duration_us * sample_rate / 1e6) for e in background_events)
        if sleep_samples + background_samples:
            sleep_charge = sum(seg.charge_uc for seg in sleep_segments) + sum(e.charge_uc for e in background_events)
            avg_sleep_current = sleep_charge * sample_rate / (sleep_samples + background_samples)

        wake_duration_s = sum(max(0.0, float(e.duration_us or 0.0)) for e in events) / 1_000_000.0
        avg_wake_current = None
        if wake_duration_s > 0:
            # charge_uc / seconds == microampere
            avg_wake_current = sum(float(e.charge_uc or 0.0) for e in events) / wake_duration_s

        period_values = [
            (int(b.trigger_sample) - int(a.trigger_sample)) / sample_rate
            for a, b in zip(events, events[1:])
            if int(b.trigger_sample) > int(a.trigger_sample)
        ]
        avg_period_s = float(np.mean(period_values)) if period_values else None
        period_std_s = float(np.std(period_values)) if period_values else None
        period_min_s = float(np.min(period_values)) if period_values else None
        period_max_s = float(np.max(period_values)) if period_values else None

        sleep_between_values = [
            max(0.0, (int(b.trigger_sample) - int(a.end_sample) - 1) / sample_rate)
            for a, b in zip(events, events[1:])
            if int(b.trigger_sample) > int(a.end_sample)
        ]

        timeline_samples = max(0, int((m.total_samples or 0) + (m.detected_lost_samples or 0)))
        for seg in sleep_segments:
            timeline_samples = max(timeline_samples, int(seg.end_sample) + 1)
        for event in events:
            timeline_samples = max(timeline_samples, int(event.end_sample) + 1)
        timeline_duration_s = timeline_samples / sample_rate if timeline_samples else 0.0

        return {
            "wake_count": len(events),
            "background_count": len(background_events),
            "background_charge_uc": sum(e.charge_uc for e in background_events),
            "cycle_count": max(0, len(events) - 1),
            "average_period_s": avg_period_s,
            "period_std_s": period_std_s,
            "period_min_s": period_min_s,
            "period_max_s": period_max_s,
            "average_sleep_current_ua": avg_sleep_current,
            "average_wake_current_ua": avg_wake_current,
            "average_wake_duration_s": (wake_duration_s / len(events)) if events else None,
            "average_sleep_duration_s": float(np.mean(sleep_between_values)) if sleep_between_values else None,
            "wake_duty_cycle_pct": (100.0 * wake_duration_s / timeline_duration_s) if timeline_duration_s > 0 else None,
            "total_wake_duration_s": wake_duration_s,
            "timeline_duration_s": timeline_duration_s,
        }

    def overview(self, measurement_id: str, max_points: int = 20_000) -> dict:
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            rows = list(
                s.scalars(
                    select(OverviewPoint)
                    .where(OverviewPoint.measurement_id == measurement_id)
                    .order_by(OverviewPoint.sample_index)
                )
            )
            sleep_segments = list(
                s.scalars(
                    select(SleepSegment)
                    .where(SleepSegment.measurement_id == measurement_id)
                    .order_by(SleepSegment.start_sample)
                )
            )
            markers = list(
                s.scalars(
                    select(Marker)
                    .where(Marker.measurement_id == measurement_id)
                    .order_by(Marker.sample_index)
                )
            )
            events = list(
                s.scalars(
                    select(WakeEvent)
                    .where(WakeEvent.measurement_id == measurement_id)
                    .order_by(WakeEvent.sequence)
                )
            )

            started_at = m.started_at
            sample_rate = int(m.sample_rate_hz or self.settings.sample_rate_hz)
            background_events = [e for e in events if e.event_kind == "background"]
            events = [e for e in events if e.event_kind != "background"]
            analysis = self._history_analysis(m, sleep_segments, events, background_events)
            cycle_energy = self._cycle_energy_for_session(s, m)
            analysis['confirmed_cycles'] = cycle_energy
            if json.loads(m.settings_json or '{}').get('detection_mode') in {'threshold', 'spectral_compare'}:
                analysis.update(cycle_count=cycle_energy['cycle_count'],
                    average_period_s=cycle_energy['average_cycle_duration_s'],
                    average_sleep_current_ua=cycle_energy['average_sleep_current_ua'],
                    average_wake_current_ua=cycle_energy['average_wake_current_ua'],
                    average_sleep_duration_s=cycle_energy['average_sleep_duration_s'],
                    average_wake_duration_s=cycle_energy['average_wake_duration_s'],
                    wake_duty_cycle_pct=cycle_energy['wake_duty_cycle_pct'])

            # Serialize while the ORM objects are attached to the session.
            serialized_events = [self._serialize_event(e, m) for e in events]
            serialized_markers = [self._serialize_marker(x, m) for x in markers]
            sleep_start_markers = [{"kind": p.kind, "sample_index": p.sample_index,
                                    "t_s": p.sample_index / sample_rate,
                                    "timestamp": _iso(_dt_for_sample(started_at, p.sample_index, sample_rate)) if started_at else None,
                                    "current_ua": p.current_ua, "event_id": p.event_id}
                                   for p in rows if p.kind in {"sleep_start", "wake_start", "sleep_validated", "wake_validated", "fft_sleep_start", "fft_wake_start"}]

        rows = [p for p in rows if not p.kind.startswith("fft_")]

        # Keep the old compact current overview for CSV/backward compatibility, but
        # the completed-measurement UI no longer uses it as its primary chart.
        if len(rows) > max_points:
            step = max(1, len(rows) // max_points)
            rows = rows[::step]

        points = []
        for p in rows:
            at = _dt_for_sample(started_at, p.sample_index, sample_rate) if started_at else None
            points.append(
                {
                    "sample_index": p.sample_index,
                    "t_s": p.sample_index / sample_rate,
                    "timestamp": _iso(at),
                    "current_ua": p.current_ua,
                    "kind": p.kind,
                    "event_id": p.event_id,
                }
            )

        # State timeline: one transition per wake start/end. This stays tiny even
        # for multi-day recordings and is exact at the 10 us sample timeline.
        end_sample = max(0, int(round(float(analysis["timeline_duration_s"]) * sample_rate)) - 1)
        timeline: list[dict[str, Any]] = []

        def add_state(sample_index: int, state_name: str, event_id: int | None = None) -> None:
            sample_index = max(0, int(sample_index))
            at = _dt_for_sample(started_at, sample_index, sample_rate) if started_at else None
            item = {
                "sample_index": sample_index,
                "t_s": sample_index / sample_rate,
                "timestamp": _iso(at),
                "state": state_name,
                "value": 1 if state_name == "wake" else 0,
                "event_id": event_id,
            }
            if timeline and timeline[-1]["sample_index"] == sample_index and timeline[-1]["state"] == state_name:
                return
            timeline.append(item)

        add_state(0, "sleep")
        for event in events:
            trigger = max(0, int(event.trigger_sample))
            finish = max(trigger, int(event.end_sample))
            add_state(trigger, "wake", int(event.id))
            add_state(finish + 1, "sleep", int(event.id))
        if end_sample > 0:
            add_state(end_sample, timeline[-1]["state"] if timeline else "sleep")

        return {
            "measurement_id": measurement_id,
            "points": points,
            "timeline": timeline,
            "state_markers": sleep_start_markers,
            "analysis": analysis,
            "events": serialized_events,
            "markers": serialized_markers,
        }

    @lru_cache(maxsize=128)
    def _wake_envelope(self, raw_file: str):
        """Cache compact immutable event data, never the full acquisition arrays."""
        raw, _ = self.raw_store.event_preview(raw_file, max_points=2000)
        idx, cur, dig = _downsample_minmax(
            raw.sample_index, raw.current_ua, raw.digital, max_points=2000
        )
        if len(raw.sample_index):
            # Preserve event boundaries as well as the extrema.
            positions = np.unique(np.concatenate(([raw.sample_index[0]], idx, [raw.sample_index[-1]])))
            offsets = np.searchsorted(raw.sample_index, positions)
            return positions.copy(), raw.current_ua[offsets].copy(), raw.digital[offsets].copy()
        return idx.copy(), cur.copy(), dig.copy()

    def series(
        self,
        measurement_id: str,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
        max_points: int = 20_000,
        view: str = "auto",
    ) -> dict:
        """Return weighted acquisition summaries or range-scoped waveform detail.

        Overview never reads stored waveforms when live summaries are available.
        Narrow explicit ranges load stored wake samples; unavailable raw intervals
        remain honest summaries. Independent history/live segments preserve gaps.
        """
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            sample_rate = int(m.sample_rate_hz or self.settings.sample_rate_hz)
            persisted_latest = max(0, int((m.total_samples or 0) + (m.detected_lost_samples or 0) - 1))

        with self._lock:
            active = self._active.get(measurement_id)

        live_preview: list[dict[str, Any]] = []
        acquisition_history: list[dict[str, Any]] = []
        active_latest = None
        open_sleep: SleepSegmentData | None = None
        if active and active.thread and active.thread.is_alive():
            with active.preview_lock:
                live_preview = list(active.preview)
                acquisition_history = list(getattr(active, "preview_history", []))
            if live_preview:
                active_latest = int(live_preview[-1]["sample_index"])
            else:
                active_latest = max(0, int(active.recorder.total_samples + active.recorder.detected_lost_samples - 1))
            open_sleep = active.recorder.live_sleep_segment()

        latest_sample = max(persisted_latest, active_latest or 0)
        start_sample = max(0, int((start_s or 0.0) * sample_rate))
        if end_s is None:
            end_sample = latest_sample
        else:
            end_sample = max(start_sample, int(end_s * sample_rate))
            if latest_sample:
                end_sample = min(end_sample, latest_sample)
        if end_sample < start_sample:
            end_sample = start_sample

        with self.db.session() as s:
            overview_rows = list(
                s.scalars(
                    select(OverviewPoint)
                    .where(
                        OverviewPoint.measurement_id == measurement_id,
                        OverviewPoint.sample_index >= start_sample,
                        OverviewPoint.sample_index <= end_sample,
                    )
                    .order_by(OverviewPoint.sample_index)
                )
            )
            events = list(
                s.scalars(
                    select(WakeEvent)
                    .where(
                        WakeEvent.measurement_id == measurement_id,
                        WakeEvent.end_sample >= start_sample,
                        WakeEvent.start_sample <= end_sample,
                    )
                    .order_by(WakeEvent.sequence)
                )
            )

            sleep_segments = list(s.scalars(
                select(SleepSegment).where(
                    SleepSegment.measurement_id == measurement_id,
                    SleepSegment.end_sample >= start_sample,
                    SleepSegment.start_sample <= end_sample,
                ).order_by(SleepSegment.start_sample)
            ))

        history_points: list[dict[str, Any]] = [
            {
                "sample_index": int(p.sample_index),
                "current_ua": float(p.current_ua),
                "kind": p.kind,
                "event_id": p.event_id,
            }
            for p in overview_rows if not p.kind.startswith("fft_")
        ]

        if sleep_segments:
            history_points = [p for p in history_points if p["kind"] != "sleep"]
            for segment in sleep_segments:
                for idx in sorted({max(start_sample, segment.start_sample), min(end_sample, segment.end_sample)}):
                    history_points.append({
                        "sample_index": int(idx), "current_ua": float(segment.mean_ua),
                        "kind": "sleep", "event_id": None, "line_key": f"sleep-{segment.id}",
                    })

        # Until the next sleep checkpoint is committed, expose the current open
        # sleep segment as a flat mean segment. This fills the live-history tail
        # without sending each 10-us sample to the browser.
        if open_sleep is not None and open_sleep.end_sample >= start_sample and open_sleep.start_sample <= end_sample:
            seg_start = max(start_sample, int(open_sleep.start_sample))
            seg_end = min(end_sample, int(open_sleep.end_sample))
            history_points.append({
                "sample_index": seg_start,
                "current_ua": float(open_sleep.mean_ua),
                "kind": "open_sleep",
                "event_id": None,
                "line_key": "open-sleep",
            })
            if seg_end != seg_start:
                history_points.append({
                    "sample_index": seg_end,
                    "current_ua": float(open_sleep.mean_ua),
                    "kind": "open_sleep",
                    "event_id": None,
                    "line_key": "open-sleep",
                })

        span_s = max(0.0, (end_sample - start_sample) / sample_rate)
        raw_loaded = False
        has_live = bool(live_preview)
        history_budget = max_points if not has_live else max(4, int(max_points * 0.74))
        live_budget = 0 if not has_live else max(4, max_points - history_budget)

        # Wide views use cached envelopes; zoom still reads original samples.
        load_raw = (view != "overview" and (start_s is not None or end_s is not None)
                    and (end_sample - start_sample + 1) <= 2_000_000 and len(events) <= 32)
        has_summaries = bool(acquisition_history and "sample_count" in acquisition_history[0])
        summary_points = []
        if has_summaries:
            # Include intersecting indivisible blocks, retaining their real extent.
            summary_points = compact_summaries([
                p for p in acquisition_history
                if p["end_sample"] >= start_sample and p["sample_index"] <= end_sample
            ], max_points=max_points)
        overview_mode = has_summaries and not load_raw
        waveform_event_ids: set[int] = set()
        if events and (not acquisition_history or load_raw):
            raw_budget = max(500, int(history_budget * 0.85))
            per_event_budget = max(250, raw_budget // max(1, len(events)))
            for event in events:
                try:
                    if load_raw:
                        raw = self.raw_store.read_event(event.raw_file, start_sample=start_sample, end_sample=end_sample)
                        raw_idx, raw_cur, raw_dig = raw.sample_index, raw.current_ua, raw.digital
                    else:
                        raw_idx, raw_cur, raw_dig = self._wake_envelope(event.raw_file)
                except Exception:  # noqa: BLE001 - old/missing raw data must not break history
                    logger.exception("Could not read raw event %s for LOD series", event.id)
                    continue
                mask = (raw_idx >= start_sample) & (raw_idx <= end_sample)
                if not np.any(mask):
                    continue
                idx = raw_idx[mask]
                cur = raw_cur[mask]
                dig = raw_dig[mask]
                idx, cur, _ = _downsample_minmax(idx, cur, dig, max_points=per_event_budget)
                waveform_event_ids.add(int(event.id))
                history_points.extend(
                    {
                        "sample_index": int(i),
                        "current_ua": float(c),
                        "kind": "raw" if load_raw else "wake_envelope",
                        "event_id": int(event.id),
                        "line_key": f"wake-{event.id}",
                    }
                    for i, c in zip(idx, cur)
                )
                raw_loaded = load_raw

        history_points = [p for p in history_points if not (
            p.get("event_id") in waveform_event_ids and str(p["kind"]).startswith("event_")
        )]
        if acquisition_history and not overview_mode:
            # Acquisition history includes unfinished wakes and sleep, independently
            # of detector persistence. Never overlay a guessed mean on this waveform.
            tail_start = int(live_preview[0]["sample_index"]) if live_preview else end_sample + 1
            raw_points = [p for p in history_points if p["kind"] == "raw"]
            raw_ranges = [(e.start_sample, e.end_sample) for e in events] if raw_points else []
            history_points = [
                {**p, "sample_index": int(p["sample_index"]), "current_ua": float(p["current_ua"]),
                 "kind": "mean" if "sample_count" in p else "acquisition",
                 "event_id": None, "line_key": p.get("line_key", "acquisition")}
                for p in acquisition_history
                if start_sample <= int(p["sample_index"]) <= end_sample and int(p["sample_index"]) < tail_start
                and not any(a <= int(p["sample_index"]) <= b for a, b in raw_ranges)
            ]
            history_points.extend(raw_points)
        if overview_mode:
            history_points = []
            live_budget = 0
        history_points = _downsample_series_points(history_points, max_points=history_budget)

        # Keep the live tail independent from persisted history. It may contain only
        # the most recent N seconds; joining both in one trace would fabricate a line
        # across the missing interval.
        live_points: list[dict[str, Any]] = []
        if live_preview and live_budget > 0:
            loaded_ranges = [(e.start_sample, e.end_sample) for e in events if int(e.id) in waveform_event_ids]
            filtered = [
                p for p in live_preview
                if start_sample <= int(p["sample_index"]) <= end_sample
                and not any(a <= int(p["sample_index"]) <= b for a, b in loaded_ranges)
            ]
            if filtered:
                for p in filtered:
                    live_points.append(
                        {
                            "sample_index": int(p["sample_index"]),
                            "current_ua": float(p["current_ua"]),
                            "kind": "live",
                            "event_id": None,
                            "line_key": (f"{p.get('line_key', 'live')}-detail-part-"
                                         f"{sum(b < int(p['sample_index']) for _, b in loaded_ranges)}"
                                         if loaded_ranges else p.get("line_key", "live")),
                        }
                    )
                last = filtered[-1]
                if not live_points or int(live_points[-1]["sample_index"]) != int(last["sample_index"]):
                    live_points.append(
                        {
                            "sample_index": int(last["sample_index"]),
                            "current_ua": float(last["current_ua"]),
                            "kind": "live",
                            "event_id": None,
                            "line_key": last.get("line_key", "live"),
                        }
                    )
                live_points = _downsample_series_points(live_points, max_points=live_budget)

        def payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {**point, "t_s": int(point["sample_index"]) / sample_rate}
                for point in items
            ]

        history_payload = payload(history_points)
        live_payload = payload(live_points)
        if has_summaries and not overview_mode:
            tail_start = int(live_preview[0]["sample_index"]) if live_preview else end_sample + 1
            raw_ranges = [(e.start_sample, e.end_sample) for e in events if int(e.id) in waveform_event_ids]
            summary_points = [p for p in summary_points if p["end_sample"] < tail_start
                              and not any(a <= p["end_sample"] and b >= p["sample_index"] for a, b in raw_ranges)]
        summary_payload = [
            {**p, "t_s": p["sample_index"] / sample_rate,
             "end_s": p["end_sample"] / sample_rate,
             "display_start_s": max(start_sample, p["sample_index"]) / sample_rate,
             "display_end_s": min(end_sample, p["end_sample"]) / sample_rate}
            for p in summary_points
        ]
        if overview_mode:
            tail_start = int(live_preview[0]["sample_index"]) if live_preview else end_sample + 1
            history_payload = [p for p in summary_payload if p["end_sample"] < tail_start]
            live_payload = [p for p in summary_payload if p["end_sample"] >= tail_start]

        # Backward-compatible merged field for API clients/tests. The web UI uses the
        # two separate fields below so it never connects unrelated data blocks.
        merged_points = _downsample_series_points(history_points + live_points, max_points=max_points)
        merged_payload = payload(merged_points)
        if overview_mode:
            merged_payload = summary_payload

        event_markers = [
            {
                "id": int(e.id),
                "sequence": int(e.sequence),
                "trigger_sample": int(e.trigger_sample),
                "t_s": int(e.trigger_sample) / sample_rate,
                "peak_ua": float(e.peak_ua),
            }
            for e in events if e.event_kind != "background"
        ]
        return {
            "measurement_id": measurement_id,
            "start_s": start_sample / sample_rate,
            "end_s": end_sample / sample_rate,
            "latest_s": latest_sample / sample_rate if sample_rate else 0.0,
            "point_count": len(merged_payload),
            "history_point_count": len(history_payload),
            "live_point_count": len(live_payload),
            "max_points": max_points,
            "display_mode": "summary" if overview_mode else "detail",
            "summary_points": summary_payload,
            "summary_point_count": len(summary_payload),
            "detail": "raw+wake+separate-live" if raw_loaded else "adaptive-overview+separate-live",
            "points": merged_payload,
            "history_points": history_payload,
            "live_points": live_payload,
            "events": event_markers,
            "state_markers": [{"kind": p.kind, "sample_index": p.sample_index,
                               "t_s": p.sample_index / sample_rate, "current_ua": p.current_ua,
                               "event_id": p.event_id} for p in overview_rows if p.kind in {"sleep_start", "wake_start", "sleep_validated", "wake_validated", "fft_sleep_start", "fft_wake_start"}],
            "phase": "startup" if active and isinstance(active.recorder, ThresholdRecorder) and active.recorder.protocol_start_sample is None else "protocol",
        }


    def event_raw(self, measurement_id: str, event_id: int, max_points: int = 50_000,
                  start_s: float | None = None, end_s: float | None = None,
                  raw_only: bool = False, adaptive_view: bool = False) -> dict:
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            e = s.get(WakeEvent, event_id)
            if not m or not e or e.measurement_id != measurement_id:
                raise KeyError((measurement_id, event_id))
            event_payload = self._serialize_event(e, m)
            started_at = m.started_at
            sample_rate = m.sample_rate_hz
            raw_file = e.raw_file
            trigger_sample = e.trigger_sample

        decoded_samples = None
        if adaptive_view and raw_only:
            from .storage.event_reader import RawDataLimitError
            raise RawDataLimitError("Choose adaptive view or raw-only mode")
        if raw_only or adaptive_view or start_s is not None or end_s is not None:
            from .storage.event_reader import RawDataLimitError
            if start_s is None or end_s is None or not np.isfinite(start_s) or not np.isfinite(end_s):
                raise RawDataLimitError("Für Rohdaten bitte Anfang und Ende des Zeitfensters angeben.")
            start_sample=trigger_sample + round(start_s * sample_rate)
            end_sample=trigger_sample + round(end_s * sample_rate)
            if adaptive_view:
                raw,decoded_samples=self.raw_store.event_view(raw_file,start_sample,end_sample,max_points)
            else:
                raw = self.raw_store.read_event(raw_file,start_sample=start_sample,end_sample=end_sample,
                    max_samples=max_points if raw_only else 2_000_000)
            aggregated = False
        else:
            raw, aggregated = self.raw_store.event_preview(raw_file, max_points=max_points)
        if decoded_samples is None:
            decoded_samples = len(raw.sample_index)
        if raw_only or adaptive_view:
            idx, current, digital = raw.sample_index, raw.current_ua, raw.digital
        else:
            idx, current, digital = _downsample_minmax(
                raw.sample_index, raw.current_ua, raw.digital, max_points=max_points
            )
        downsampled = not aggregated and len(idx) < decoded_samples
        t_us = (idx - trigger_sample) * (1_000_000.0 / sample_rate)
        state_markers = []
        if len(idx):
            with self.db.session() as s:
                points = s.scalars(select(OverviewPoint).where(
                    OverviewPoint.measurement_id == measurement_id,
                    OverviewPoint.sample_index >= int(idx[0]),
                    OverviewPoint.sample_index <= int(idx[-1]),
                    OverviewPoint.kind.in_(["sleep_start", "wake_start", "sleep_validated", "wake_validated"]),
                ).order_by(OverviewPoint.sample_index))
                state_markers = [{"kind": p.kind, "current_ua": p.current_ua,
                                  "t_us": (p.sample_index - trigger_sample) * 1_000_000.0 / sample_rate}
                                 for p in points]
        return {
            "measurement_id": measurement_id,
            "event": event_payload,
            "measurement_started_at": _iso(started_at),
            "sample_period_us": 1_000_000.0 / sample_rate,
            "aggregated": aggregated,
            "downsampled": downsampled,
            "decoded_samples": decoded_samples,
            "detail": "block-extrema" if aggregated else "sample-extrema" if downsampled else "raw",
            "display_notice": ("Aggregierte Block-Minima/Maxima. Zeitpositionen sind angenähert. Zoomen lädt Rohsamples nach." if aggregated
                               else "Anzeige verdichtet: Min/Max-Auswahl mit originalen Sample-Zeiten. Zoomen lädt Rohsamples nach." if downsampled else None),
            "sample_index": idx.tolist(),
            "t_us": t_us.tolist(),
            "state_markers": state_markers,
            "current_ua": current.astype(float).tolist(),
            "digital": digital.astype(int).tolist(),
        }

    def raw_event_csv(self, measurement_id: str, event_id: int) -> tuple[str, bytes]:
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            e = s.get(WakeEvent, event_id)
            if not m or not e or e.measurement_id != measurement_id:
                raise KeyError((measurement_id, event_id))
            raw_file = e.raw_file
            trigger_sample = e.trigger_sample
            sample_rate = m.sample_rate_hz
            started_at = m.started_at
        # CSV duplicates text while formatting/encoding. Cap before decoding arrays.
        raw = self.raw_store.read_event(raw_file, max_samples=250_000)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["sample_index", "t_us_from_trigger", "timestamp", "current_uA", "digital"])
        for idx, current, digital in zip(raw.sample_index, raw.current_ua, raw.digital):
            at = _dt_for_sample(started_at, int(idx), sample_rate) if started_at else None
            writer.writerow(
                [
                    int(idx),
                    (int(idx) - trigger_sample) * 1_000_000.0 / sample_rate,
                    _iso(at),
                    float(current),
                    int(digital),
                ]
            )
        return f"event_{e.sequence:06d}.csv", out.getvalue().encode("utf-8")

    def export_report_pdf(self, measurement_id: str) -> tuple[str, bytes]:
        from .report import render_report

        measurement = self.get_measurement(measurement_id)
        if measurement["status"] not in {"completed", "failed", "no_sleep", "cancelled"}:
            raise RuntimeError("PDF-Messprotokolle sind erst nach Ende der Messung verfügbar.")
        return f"measurement_{measurement_id}_report.pdf", render_report(
            measurement, self.overview(measurement_id), timezone_name=self.settings.timezone)

    def export_metadata_json(self, measurement_id: str) -> tuple[str, bytes]:
        payload = self.get_measurement(measurement_id)
        if payload["settings"].get("detection_mode") == "spectral_compare":
            with self.db.session() as session:
                rows = session.scalars(select(OverviewPoint).where(
                    OverviewPoint.measurement_id == measurement_id,
                    OverviewPoint.kind.in_(["fft_sleep_start", "fft_wake_start"])
                ).order_by(OverviewPoint.sample_index))
                payload["spectral_markers"] = [{"kind": p.kind, "sample_index": p.sample_index,
                    "t_s": p.sample_index / payload["sample_rate_hz"], "current_ua": p.current_ua} for p in rows]
        return (
            f"measurement_{measurement_id}.json",
            json.dumps(payload, indent=2, default=str).encode("utf-8"),
        )

    def export_overview_csv(self, measurement_id: str) -> tuple[str, bytes]:
        payload = self.overview(measurement_id, max_points=1_000_000)
        out = io.StringIO()
        writer = csv.DictWriter(
            out,
            fieldnames=["sample_index", "t_s", "timestamp", "current_ua", "kind", "event_id"],
        )
        writer.writeheader()
        writer.writerows(payload["points"])
        return f"measurement_{measurement_id}_overview.csv", out.getvalue().encode("utf-8")

    def export_measurements(self, measurement_ids: list[str], kind: str = "bundle") -> Path:
        if kind not in {"json", "csv", "bundle", "pdf"}:
            raise ValueError("kind must be json, csv, bundle or pdf")
        ids = list(dict.fromkeys(measurement_ids))
        for measurement_id in ids:
            self.get_measurement(measurement_id)
        with tempfile.NamedTemporaryFile(suffix=".zip", dir=self.raw_store.exports_dir, delete=False) as tmp:
            path = Path(tmp.name)
        try:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for measurement_id in ids:
                    if kind == "bundle":
                        bundle = self.export_bundle(measurement_id)
                        archive.write(bundle, arcname=bundle.name, compress_type=zipfile.ZIP_STORED)
                    else:
                        export = {"json": self.export_metadata_json, "csv": self.export_overview_csv,
                                  "pdf": self.export_report_pdf}[kind]
                        filename, body = export(measurement_id)
                        archive.writestr(filename, body)
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def export_bundle(self, measurement_id: str) -> Path:
        with self.db.session() as s:
            m = s.get(Measurement, measurement_id)
            if not m:
                raise KeyError(measurement_id)
            sleep = [
                model_to_dict(x)
                for x in s.scalars(
                    select(SleepSegment)
                    .where(SleepSegment.measurement_id == measurement_id)
                    .order_by(SleepSegment.start_sample)
                )
            ]
            events = [
                model_to_dict(x)
                for x in s.scalars(
                    select(WakeEvent)
                    .where(WakeEvent.measurement_id == measurement_id)
                    .order_by(WakeEvent.sequence)
                )
            ]
            overview = [
                model_to_dict(x)
                for x in s.scalars(
                    select(OverviewPoint)
                    .where(OverviewPoint.measurement_id == measurement_id)
                    .order_by(OverviewPoint.sample_index)
                )
            ]
            markers = [
                model_to_dict(x)
                for x in s.scalars(
                    select(Marker)
                    .where(Marker.measurement_id == measurement_id)
                    .order_by(Marker.sample_index)
                )
            ]
        return self.raw_store.create_bundle(
            measurement_id,
            metadata=self.get_measurement(measurement_id),
            sleep_segments=sleep,
            wake_events=events,
            overview_points=overview,
            markers=markers,
        )

    def system_status(self) -> dict:
        return {
            "sample_rate_hz": self.settings.sample_rate_hz,
            "sample_period_us": 1_000_000 / self.settings.sample_rate_hz,
            "data_dir": str(self.settings.data_dir.resolve()),
        }
