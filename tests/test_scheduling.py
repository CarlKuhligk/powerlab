import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db import Database
from app.manager import MeasurementManager
from app.ppk.base import PowerProfilerDriver, SampleBatch
from app.schemas import MeasurementStartRequest
from app.storage.raw_store import RawStore


class FakePPK(PowerProfilerDriver):
    def __init__(self, port: str):
        self.port = port
        self.tick = 0

    def start(self):
        pass

    def metadata(self):
        return {"device_id": f"TEST-{self.port}", "port": self.port, "sample_rate_hz": 100_000}

    def read_batch(self):
        time.sleep(0.002)
        ticks = np.arange(self.tick, self.tick + 500, dtype=np.int64)
        self.tick += 500
        return SampleBatch(np.full(500, 4.0, dtype=np.float32), np.zeros(500, dtype=np.uint8), sample_ticks=ticks)

    def stop(self):
        pass


def make_manager(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}", sleep_checkpoint_s=0.05)
    settings.prepare()
    db = Database(settings.database_url); db.create_all()
    return MeasurementManager(settings, db, RawStore(tmp_path))


def test_multiple_ppk2_measurements_can_run_in_parallel(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(manager, "_build_driver", lambda req: FakePPK(req.port))
    a = manager.start(MeasurementStartRequest(name="A", port="COM4", sleep_min_s=.005))
    b = manager.start(MeasurementStartRequest(name="B", port="COM7", sleep_min_s=.005))
    time.sleep(0.04)
    live = manager.live_overview()
    assert len(live["running"]) == 2
    manager.stop(a["id"]); manager.stop(b["id"])


def test_scheduled_measurement_autostarts_and_stops_by_duration(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    monkeypatch.setattr(manager, "_build_driver", lambda req: FakePPK(req.port))
    start = datetime.now(timezone.utc) + timedelta(seconds=0.15)
    m = manager.start(MeasurementStartRequest(
        name="scheduled", port="COM4", start_mode="scheduled", scheduled_start_at=start,
        stop_mode="duration", duration_s=0.12, sleep_min_s=.005,
    ))
    assert m["status"] == "scheduled"
    manager.start_scheduler()
    deadline = time.time() + 2
    status = None
    while time.time() < deadline:
        status = manager.get_measurement(m["id"])["status"]
        if status in {"completed", "failed"}:
            break
        time.sleep(0.03)
    manager.shutdown()
    final = manager.get_measurement(m["id"])
    assert final["status"] == "completed"
    assert final["started_at"] is not None
    assert final["finished_at"] is not None


@pytest.mark.parametrize("mode", ["wake_count", "sleep_count"])
@pytest.mark.parametrize("count", [None, 0, -1, 1.5, True])
def test_event_stop_requires_a_positive_integer(mode, count):
    with pytest.raises(ValidationError):
        MeasurementStartRequest(name="count", port="COM4", stop_mode=mode, event_count=count)


class CyclingPPK(FakePPK):
    def __init__(self, values, chunk):
        super().__init__("COM4")
        self.values = np.asarray(values, dtype=np.float32)
        self.chunk = chunk

    def read_batch(self):
        if self.tick >= len(self.values):
            time.sleep(.001)
            return None
        end = min(self.tick + self.chunk, len(self.values))
        ticks = np.arange(self.tick, end, dtype=np.int64)
        values = self.values[self.tick:end]
        self.tick = end
        return SampleBatch(values, np.zeros(len(values), dtype=np.uint8), ticks)

    def stop(self):
        # Data still queued at the hardware must not extend a counted recording.
        return SampleBatch(np.full(10, 30000), np.zeros(10, dtype=np.uint8), np.arange(self.tick, self.tick + 10))


@pytest.mark.parametrize("mode,expected_end,expected_wakes", [("wake_count", 150, 5), ("sleep_count", 140, 4)])
@pytest.mark.parametrize("chunk", [1, 7, 10000])
def test_count_stop_uses_confirmed_opposite_start_and_trims_stats(tmp_path, monkeypatch, mode, expected_end, expected_wakes, chunk):
    manager = make_manager(tmp_path)
    manager.settings.sample_rate_hz = 1000
    values = [4] * 20 + ([20000] * 10 + [4] * 20) * 7
    driver = CyclingPPK(values, chunk)
    monkeypatch.setattr(manager, "_build_driver", lambda req: driver)
    m = manager.start(MeasurementStartRequest(name="count", port="COM4", detection_mode="threshold",
                      sleep_min_s=.005, wake_min_ms=3, stop_mode=mode, event_count=5,
                      pre_trigger_ms=0, post_trigger_ms=1000, sleep_checkpoint_s=.1))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        final = manager.get_measurement(m["id"])
        if final["status"] in {"completed", "failed"}:
            break
        time.sleep(.01)
    manager.shutdown()
    assert final["status"] == "completed", final.get("error")
    assert final["event_count"] == 5
    assert final["settings"]["event_count_result"]["stop_sample"] == expected_end
    assert final["duration_s"] == pytest.approx(expected_end / 1000, abs=1e-6)
    assert final["total_samples"] == expected_end
    assert final["detected_lost_samples"] == 0
    assert final["total_charge_uc"] == pytest.approx(sum(values[:expected_end]) / 1000)
    assert len(final["events"]) == expected_wakes
    assert final["wake_count"] == expected_wakes
    assert all(e["end_sample"] < expected_end for e in final["events"])


def test_count_stop_ignores_short_candidates_and_sleep_checkpoints(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    manager.settings.sample_rate_hz = 1000
    # Tentative wakes and tentative returns do not meet their confirmation duration.
    values = [4] * 100 + [20000] * 2 + [4] * 50 + [20000] * 10 + [4] * 4 + [20000] * 10 + [4] * 20
    monkeypatch.setattr(manager, "_build_driver", lambda req: CyclingPPK(values, 7))
    m = manager.start(MeasurementStartRequest(name="count", port="COM4", detection_mode="threshold",
                      sleep_min_s=.005, wake_min_ms=3, stop_mode="wake_count", event_count=1,
                      pre_trigger_ms=0, post_trigger_ms=0, sleep_checkpoint_s=.1))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        final = manager.get_measurement(m["id"])
        if final["status"] in {"completed", "failed"}:
            break
        time.sleep(.01)
    manager.shutdown()
    assert final["status"] == "completed"
    assert final["settings"]["event_count_result"] == {"confirmed_wakes": 1, "confirmed_sleeps": 2, "stop_sample": 176}
    assert final["total_samples"] == 176
    assert len(final["events"]) == 1


def test_scheduled_event_stop_survives_edit_and_reload(tmp_path):
    manager = make_manager(tmp_path)
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    request = MeasurementStartRequest(name="planned", port="COM4", start_mode="scheduled", scheduled_start_at=start,
                                      stop_mode="wake_count", event_count=5, detection_mode="threshold")
    m = manager.start(request)
    assert m["event_count"] == 5 and m["scheduled_end_at"] is None
    request.stop_mode = "sleep_count"
    request.event_count = 3
    updated = manager.update_scheduled(m["id"], request)
    assert updated["event_count"] == 3 and updated["stop_mode"] == "sleep_count"
    restored = make_manager(tmp_path).get_measurement(m["id"])
    assert restored["settings"]["event_count"] == 3


@pytest.mark.parametrize("detection", ["automatic", "legacy"])
def test_removed_detection_modes_are_rejected(detection):
    with pytest.raises(ValidationError):
        MeasurementStartRequest(name="removed", port="COM4", detection_mode=detection)
