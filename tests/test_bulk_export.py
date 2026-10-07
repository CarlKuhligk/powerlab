import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Measurement
from app.main import create_app


@pytest.mark.parametrize("kind", ["json", "csv", "bundle", "pdf"])
def test_bulk_export_contains_only_selected_measurements_and_cleans_download(tmp_path, kind):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        with manager.db.session() as session:
            for measurement_id in ["first", "second", "unselected"]:
                session.add(Measurement(id=measurement_id, name=measurement_id, status="completed"))
        response = client.post("/api/measurements/bulk/export", json={
            "measurement_ids": ["first", "second", "first"], "kind": kind,
        })
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == 2
            assert not any("unselected" in name for name in archive.namelist())
            if kind == "json":
                assert json.loads(archive.read("measurement_first.json"))["id"] == "first"
            elif kind == "csv":
                assert archive.read("measurement_second_overview.csv").startswith(b"sample_index,t_s,")
            elif kind == "pdf":
                assert archive.read("measurement_second_report.pdf").startswith(b"%PDF-")
            else:
                with zipfile.ZipFile(io.BytesIO(archive.read("powerlab_first.zip"))) as bundle:
                    assert json.loads(bundle.read("metadata.json"))["id"] == "first"
                    assert "overview.csv" in bundle.namelist()
        assert not list(manager.raw_store.exports_dir.glob("tmp*.zip"))
        assert client.post("/api/measurements/bulk/export", json={"measurement_ids": ["first", "missing"]}).status_code == 404
        assert client.post("/api/measurements/bulk/export", json={"measurement_ids": []}).status_code == 422
        assert client.post("/api/measurements/bulk/export", json={"measurement_ids": ["first"], "kind": "invalid"}).status_code == 422
