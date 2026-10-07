import time
from pathlib import Path
import pytest

from app.config import Settings
from app.db import Database
from app.manager import MeasurementManager
from app.schemas import MeasurementStartRequest
from app.storage.raw_store import RawStore


@pytest.mark.parametrize("mode", ["threshold", "spectral_compare"])
def test_ppk2_identity_and_config_are_logged(tmp_path: Path, monkeypatch, mode):
    import json
    import numpy as np
    from app.ppk.base import PowerProfilerDriver, SampleBatch

    class FakePPK(PowerProfilerDriver):
        sample_rate_hz = 100_000

        def __init__(self):
            self.running = False
            self.tick = 0

        def start(self):
            self.running = True

        def metadata(self):
            return {
                "device_id": "PPK2-TEST-001",
                "port": "COM4",
                "serial": "TESTSERIAL",
                "vid": "1915",
                "pid": "C00A",
                "interface": "01",
                "hw": "5595",
                "ia": "61",
                "calibrated": "0",
                "meter_mode": "source",
                "source_voltage_mv": 3300,
                "sample_rate_hz": 100_000,
                "calibration_modifiers": {"R": {"0": 999.0}},
            }

        def read_batch(self):
            import time
            time.sleep(0.002)
            idx = np.arange(self.tick, self.tick + 1000, dtype=np.int64)
            self.tick += 1000
            return SampleBatch(
                np.full(1000, 4.0, dtype=np.float32),
                np.zeros(1000, dtype=np.uint8),
                sample_ticks=idx,
            )

        def stop(self):
            self.running = False

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'powerlab.db'}",
        sample_rate_hz=100_000,
        sleep_checkpoint_s=0.1,
    )
    settings.prepare()
    db = Database(settings.database_url)
    db.create_all()
    raw = RawStore(tmp_path)

    manager = MeasurementManager(settings, db, raw)
    monkeypatch.setattr(manager, "_build_driver", lambda req: FakePPK())

    req = MeasurementStartRequest(
        name="hardware-log-test",
        detection_mode=mode,
        port="COM4",
        sleep_min_s=.005,
        wake_min_ms=3,
        sleep_threshold_ua=5,
        wake_threshold_ua=10000,
        sleep_checkpoint_s=0.1,
    )
    started = manager.start(req)
    assert manager._active[started["id"]].recorder.settings.wake_min_ms == 3
    assert manager._active[started["id"]].recorder.settings.sleep_min_s == .005
    assert manager._active[started["id"]].recorder.settings.sleep_threshold_ua == 5
    time.sleep(0.05)
    finished = manager.stop()

    assert started["driver"] == "ppk2"
    assert finished["ppk2_id"] == "PPK2-TEST-001"
    assert finished["ppk2_config"]["port"] == "COM4"
    assert finished["ppk2_config"]["hw"] == "5595"
    assert finished["ppk2_config"]["calibration_modifiers"]["R"]["0"] == 999.0

    metadata_path = raw.measurement_dir(finished["id"]) / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["ppk2_id"] == "PPK2-TEST-001"
    assert payload["ppk2_config"]["serial"] == "TESTSERIAL"
    assert finished["settings"]["wake_min_ms"] == 3
    assert finished["settings"]["sleep_min_s"] == .005
    assert finished["settings"]["sleep_threshold_ua"] == 5
    assert finished["settings"]["detection_mode"] == mode
    if mode == "spectral_compare":
        assert finished["settings"]["spectral_result"]["controls_recording"] is False
