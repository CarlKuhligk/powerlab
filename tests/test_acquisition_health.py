import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import Settings
from app.db import Database
from app.manager import MeasurementManager
from app.ppk.base import PowerProfilerDriver, SampleBatch
from app.ppk.nordic import NordicPPK2Driver
from app.schemas import MeasurementStartRequest
from app.storage.raw_store import RawStore


def samples():
    return SampleBatch(np.full(1000, 4.0), np.zeros(1000, dtype=np.uint8))


class BrokenStream(PowerProfilerDriver):
    def __init__(self, mode):
        self.mode = mode
        self.release = threading.Event()
        self.stop_called = False
        self.sent = False

    def start(self):
        if self.mode == "startup":
            self.release.wait(5)

    def read_batch(self):
        if self.mode == "no_first_sample":
            return None
        if not self.sent:
            self.sent = True
            return samples()
        if self.mode == "exception":
            raise OSError("USB disconnected")
        if self.mode == "blocked":
            self.release.wait(5)
        if self.mode == "empty":
            return SampleBatch([], [])
        time.sleep(0.002)
        return None

    def stop(self):
        self.stop_called = True
        if self.mode == "cleanup":
            self.release.wait(5)


def manager_for(tmp_path, monkeypatch, driver):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}", acquisition_timeout_s=0.15, worker_timeout_s=0.4)
    settings.prepare()
    db = Database(settings.database_url)
    db.create_all()
    manager = MeasurementManager(settings, db, RawStore(tmp_path))
    monkeypatch.setattr(manager, "_build_driver", lambda req: driver)
    return manager


@pytest.mark.parametrize("mode", ["silent", "empty", "blocked", "exception", "startup", "cleanup", "processing", "no_first_sample"])
def test_aborted_stream_is_failed_even_when_worker_is_blocked(tmp_path, monkeypatch, mode):
    driver = BrokenStream(mode)
    manager = manager_for(tmp_path, monkeypatch, driver)
    if mode == "processing":
        from app.recorder import RecorderBase
        original = RecorderBase.process

        def blocked_process(self, batch):
            driver.release.wait(5)
            return original(self, batch)

        monkeypatch.setattr(RecorderBase, "process", blocked_process)
    m = manager.start(MeasurementStartRequest(name="broken-stream", port="COM4", sleep_min_s=.005))
    active = manager._active[m["id"]]
    try:
        deadline = time.monotonic() + 2
        if mode == "cleanup":
            while active.recorder.total_samples == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            active.stop_event.set()
        while time.monotonic() < deadline:
            final = manager.get_measurement(m["id"])
            if final["status"] == "failed":
                break
            time.sleep(0.01)
        assert final["status"] == "failed"
        assert final["error"]
        assert final["finished_at"]
        assert not manager.active_snapshot(m["id"])["running"]
        assert manager.live_overview()["running"] == []
        if mode in {"blocked", "startup", "cleanup", "processing"}:
            assert active.thread.is_alive()
            assert manager._port_is_busy("COM4")
        if mode == "exception":
            assert "USB disconnected" in final["error"]
    finally:
        driver.release.set()
        active.thread.join(3)
        active.watchdog.join(1)
    assert not active.thread.is_alive()
    final = manager.get_measurement(m["id"])
    assert final["status"] == "failed"
    assert driver.stop_called
    if mode not in {"startup", "no_first_sample"}:
        assert final["total_samples"] == 1000
    metadata = json.loads((manager.raw_store.measurement_dir(m["id"]) / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["error"] == final["error"]


def test_brief_stream_pause_and_manual_stop_complete_normally(tmp_path, monkeypatch):
    driver = BrokenStream("silent")
    manager = manager_for(tmp_path, monkeypatch, driver)
    m = manager.start(MeasurementStartRequest(name="manual", port="COM4", sleep_min_s=.005))
    active = manager._active[m["id"]]
    time.sleep(0.04)
    final = manager.stop(m["id"])
    active.watchdog.join(1)
    assert final["status"] == "completed"
    assert final["error"] is None
    assert not active.watchdog.is_alive()


@pytest.mark.parametrize("failure", ["stop", "metadata"])
def test_cleanup_error_cannot_leave_a_measurement_recording(tmp_path, monkeypatch, failure):
    driver = BrokenStream("silent")
    manager = manager_for(tmp_path, monkeypatch, driver)
    m = manager.start(MeasurementStartRequest(name="cleanup-error", port="COM4", sleep_min_s=.005))
    active = manager._active[m["id"]]

    def broken(*args):
        raise OSError("cleanup write failed")

    if failure == "stop":
        monkeypatch.setattr(driver, "stop", broken)
    else:
        monkeypatch.setattr(manager, "_write_metadata_snapshot", broken)
    final = manager.stop(m["id"])
    active.watchdog.join(1)
    assert final["status"] == "failed"
    assert "cleanup write failed" in final["error"]
    assert m["id"] not in manager._active
    assert not active.watchdog.is_alive()


@pytest.mark.parametrize("fetcher", [None, SimpleNamespace(is_alive=lambda: False)])
def test_dead_hardware_reader_is_detected_without_waiting_for_timeout(fetcher):
    driver = NordicPPK2Driver(port="COM4")
    driver._running = True
    driver._api = SimpleNamespace(_fetcher=fetcher, get_data=lambda: b"")
    with pytest.raises(RuntimeError, match="reader stopped"):
        driver.read_batch()
