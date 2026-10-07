import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Measurement
from app.main import create_app
from app.schemas import MeasurementStartRequest
from test_history_states import build_manager


def test_threshold_is_the_only_default_and_old_fields_are_not_stored():
    request = MeasurementStartRequest(name="default", port="COM4", startup_confirm_s=60, hard_trigger_ua=20)
    payload = request.model_dump()
    assert payload["detection_mode"] == "threshold"
    assert payload["pre_trigger_ms"] == payload["post_trigger_ms"] == 1000
    assert "startup_confirm_s" not in payload and "hard_trigger_ua" not in payload


@pytest.mark.parametrize("mode", ["automatic", "legacy"])
def test_api_rejects_removed_modes_before_starting_hardware(tmp_path, mode):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'modes.db'}")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/measurements", json={"name": "removed", "port": "COM4", "detection_mode": mode})
        assert response.status_code == 422
        assert client.app.state.manager._active == {}


@pytest.mark.parametrize("mode", ["automatic", "legacy", None])
def test_old_scheduled_modes_are_not_silently_converted(tmp_path, monkeypatch, mode):
    manager = build_manager(tmp_path)
    request = MeasurementStartRequest(name="old plan", port="COM4", start_mode="scheduled",
                                      scheduled_start_at=datetime.now(timezone.utc) + timedelta(hours=1))
    measurement = manager.start(request)
    with manager.db.session() as session:
        row = session.get(Measurement, measurement["id"])
        payload = json.loads(row.settings_json)
        if mode is None:
            payload.pop("detection_mode")
        else:
            payload["detection_mode"] = mode
        row.settings_json = json.dumps(payload)
        row.scheduled_start_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    driver_calls = []
    monkeypatch.setattr(manager, "_build_driver", lambda req: driver_calls.append(req))
    manager.start_scheduler()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = manager.get_measurement(measurement["id"])
            if result["status"] == "failed":
                break
            time.sleep(.02)
        assert result["status"] == "failed"
        assert "Erkennungsmodus wurde entfernt" in result["error"]
        assert driver_calls == []
    finally:
        manager.shutdown()


def test_old_plan_can_be_explicitly_updated_and_history_keeps_original_settings(tmp_path):
    manager = build_manager(tmp_path)
    request = MeasurementStartRequest(name="old plan", port="COM4", start_mode="scheduled",
                                      scheduled_start_at=datetime.now(timezone.utc) + timedelta(hours=1))
    measurement = manager.start(request)
    old_payload = {**request.model_dump(mode="json"), "detection_mode": "automatic", "startup_confirm_s": 60}
    with manager.db.session() as session:
        row = session.get(Measurement, measurement["id"])
        row.settings_json = json.dumps(old_payload)
        session.add(Measurement(id="archived", name="Archived", status="completed", settings_json=json.dumps(old_payload)))
    updated = manager.update_scheduled(measurement["id"], request)
    assert updated["settings"]["detection_mode"] == "threshold"
    assert "startup_confirm_s" not in updated["settings"]
    archived = manager.get_measurement("archived")
    assert archived["settings"]["detection_mode"] == "automatic"
    assert archived["settings"]["startup_confirm_s"] == 60
