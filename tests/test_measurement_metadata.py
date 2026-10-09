"""Device metadata persists through creation, editing, restart and exports."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Measurement
from app.main import create_app


@pytest.mark.parametrize("device_fields", [
    {},
    {"serial_number": "0000123", "firmware": "v1.2.3", "hardware_version": "Rev. B"},
])
def test_optional_device_metadata_survives_edit_restart_and_exports(tmp_path, device_fields):
    settings = Settings(_env_file=None, data_dir=tmp_path,
                        database_url=f"sqlite:///{tmp_path / 'metadata.db'}")
    body = {"name": "Metadata test", "port": "COM4", "start_mode": "scheduled",
            "scheduled_start_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            **device_fields}
    expected = {key: device_fields.get(key, "")
                for key in ("serial_number", "firmware", "hardware_version")}
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/measurements", json=body)
        assert response.status_code == 200, response.text
        mid = response.json()["id"]
        for key, value in expected.items():
            assert response.json()[key] == value
            assert response.json()["settings"][key] == value
        # Saving a pending plan must retain its metadata.
        response = client.patch(f"/api/measurements/{mid}/scheduled", json=body)
        assert response.status_code == 200, response.text
        for key, value in expected.items():
            assert response.json()[key] == value
        # Simulate a finished recording without requiring PPK2 hardware.
        with client.app.state.db.session() as session:
            session.get(Measurement, mid).status = "completed"
        response = client.get(f"/api/measurements/{mid}/export/json")
        assert response.status_code == 200
        for key, value in expected.items():
            assert response.json()[key] == value
        response = client.get(f"/api/measurements/{mid}/export/bundle")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            metadata = json.loads(bundle.read("metadata.json"))
            for key, value in expected.items():
                assert metadata[key] == value
        response = client.get(f"/api/measurements/{mid}/export/pdf")
        assert response.status_code == 200
        with pymupdf.open(stream=response.content, filetype="pdf") as document:
            text = " ".join(page.get_text() for page in document)
            for key, label in [("serial_number", "Seriennummer"), ("firmware", "Firmware-Version"),
                               ("hardware_version", "Hardware-Version")]:
                if device_fields.get(key):
                    assert label in text
            for value in device_fields.values():
                assert value in text
        # A partial metadata edit must preserve the other optional values.
        expected["serial_number"] = "SN-UPDATED"
        response = client.patch(f"/api/measurements/{mid}", json={"serial_number": "SN-UPDATED"})
        assert response.status_code == 200
        for key, value in expected.items():
            assert response.json()[key] == value
        snapshot = json.loads((tmp_path / "measurements" / mid / "metadata.json").read_text(encoding="utf-8"))
        assert snapshot["serial_number"] == "SN-UPDATED"
    with TestClient(create_app(settings)) as client:
        measurement = client.get(f"/api/measurements/{mid}").json()
        for key, value in expected.items():
            assert measurement[key] == value
        response = client.patch(f"/api/measurements/{mid}", json={key: "" for key in expected})
        assert response.status_code == 200
        assert all(response.json()[key] == "" for key in expected)
