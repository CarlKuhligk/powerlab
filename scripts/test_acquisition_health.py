"""Check a real PPK2 stream, then simulate silence to verify failure detection.

Uses ampere-meter mode without enabling DUT power. Test data is isolated from
the application's measurement database. Run with PowerLab stopped.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.db import Database
from app.manager import MeasurementManager
from app.schemas import MeasurementStartRequest
from app.storage.raw_store import RawStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--duration", type=float, default=60.0)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be positive")
    root = Path(__file__).resolve().parents[1] / "data" / f"hardware-health-{uuid4().hex}"
    settings = Settings(data_dir=root, database_url=f"sqlite:///{root / 'test.db'}",
                        acquisition_timeout_s=3.0, worker_timeout_s=30.0)
    settings.prepare()
    db = Database(settings.database_url)
    db.create_all()
    manager = MeasurementManager(settings, db, RawStore(root))
    report = {"port": args.port, "test_data": str(root), "healthy_duration_s": args.duration,
              "fault": "simulated missing samples after real hardware acquisition",
              "meter_mode": "ampere", "dut_power_enabled": False}
    active = None
    original_read = None
    try:
        measurement = manager.start(MeasurementStartRequest(name="Hardware acquisition health check", port=args.port,
                                                            meter_mode="ampere"))
        active = manager._active[measurement["id"]]
        report["measurement_id"] = measurement["id"]
        deadline = time.monotonic() + 35
        while active.recorder.total_samples == 0:
            if active.error:
                raise RuntimeError(active.error)
            if time.monotonic() >= deadline:
                raise TimeoutError("No first samples within startup deadline")
            time.sleep(0.05)
        print(f"Real stream started on {args.port}; DUT power is not enabled.", flush=True)
        started = time.monotonic()
        while time.monotonic() - started < args.duration:
            if active.error or not active.thread.is_alive():
                raise RuntimeError(active.error or "Worker stopped during healthy stream")
            time.sleep(0.1)
        report["real_samples"] = active.recorder.total_samples
        report["detected_lost_samples"] = active.recorder.detected_lost_samples
        if report["real_samples"] <= 0:
            raise RuntimeError("No real samples recorded")
        print(f"Healthy stream: {report['real_samples']:,} samples. Simulating silence now.", flush=True)
        original_read = active.driver.read_batch
        active.driver.read_batch = lambda: None
        injected = time.monotonic()
        deadline = injected + settings.acquisition_timeout_s + 2
        while manager.get_measurement(measurement["id"])["status"] != "failed":
            if time.monotonic() >= deadline:
                raise TimeoutError("Silent stream was not marked failed within the expected deadline")
            time.sleep(0.02)
        report["failure_detection_s"] = round(time.monotonic() - injected, 3)
        if manager.active_snapshot(measurement["id"])["running"] or manager.live_overview()["running"]:
            raise AssertionError("Failed measurement still advertised as recording")
        active.thread.join(10)
        if active.thread.is_alive():
            raise TimeoutError("Hardware cleanup did not finish")
        final = manager.get_measurement(measurement["id"])
        report["status"] = final["status"]
        report["error"] = final["error"]
        metadata = json.loads((manager.raw_store.measurement_dir(measurement["id"]) / "metadata.json").read_text(encoding="utf-8"))
        if metadata["status"] != "failed" or metadata["error"] != final["error"]:
            raise AssertionError("Failure not persisted in metadata")
        report["passed"] = True
    except Exception as exc:
        report["passed"] = False
        report["test_error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if active:
            if original_read:
                active.driver.read_batch = original_read
            active.stop_event.set()
            active.thread.join(10)
            active.watchdog.join(1)
        (root / "health-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
