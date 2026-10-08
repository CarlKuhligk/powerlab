from datetime import datetime, timedelta, timezone

import pytest
import pymupdf
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Measurement, Marker, WakeEvent
from app.main import create_app
from app.report import date, number, report_data


@pytest.mark.parametrize("charge_uc,charge_text,energy_text", [
    (400, "400,000 µC", "366,666667 nWh"),
    (1_200_000_000, "1,200 kC", "1,100000 Wh"),
])
@pytest.mark.parametrize("timezone_name,expected_start", [
    ("Europe/Berlin", "06.10.2026 10:00:00 CEST (UTC+02:00)"),
    ("UTC", "06.10.2026 08:00:00 UTC (UTC+00:00)"),
])
def test_pdf_download_uses_history_values_and_treats_user_text_as_data(
        tmp_path, charge_uc, charge_text, energy_text, timezone_name, expected_start):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}", timezone=timezone_name)
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        start = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)
        with manager.db.session() as session:
            session.add(Measurement(id="report", name='Prüfling #include "missing.typ"',
                                    notes="Äöü µA\n#panic(\"not source\")", status="completed",
                                    started_at=start, finished_at=start + timedelta(seconds=2),
                                    total_samples=200000, total_charge_uc=charge_uc,
                                    average_current_ua=200, peak_current_ua=500,
                                    voltage_mv=3300))
            session.flush()
            session.add(WakeEvent(measurement_id="report", sequence=1, start_sample=10000,
                                  trigger_sample=10000, end_sample=19999, duration_us=100000,
                                  mean_ua=300, peak_ua=500, charge_uc=30, raw_file="unused.parquet"))
            session.add(Marker(measurement_id="report", sample_index=10000, label="Start [Test]"))
        m = manager.get_measurement("report")
        overview = manager.overview("report")
        data = report_data(m, overview)
        assert ["Mittlerer Wake-Strom", "300,000 µA"] in data["results"]
        assert ["Gesamtladung", charge_text] in data["results"]
        assert ["Energie (konfigurierte Spannung)", energy_text] in data["results"]
        assert data["events"][0][2] == "100,000000 ms"
        assert data["markers"] == [["100,000000 ms", "Start [Test]"]]
        response = client.get("/api/measurements/report/export/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert 'filename="measurement_report_report.pdf"' in response.headers["content-disposition"]
        assert response.content.startswith(b"%PDF-")
        with pymupdf.open(stream=response.content, filetype="pdf") as document:
            text = " ".join(page.get_text() for page in document)
            assert expected_start in text
            assert timezone_name in text
        assert m["started_at"] == "2026-10-06T08:00:00Z"
        assert client.get("/api/measurements/missing/export/pdf").status_code == 404


@pytest.mark.parametrize("value,expected", [
    ("2026-10-08T13:14:15Z", "08.10.2026 15:14:15 CEST (UTC+02:00)"),
    ("2026-01-08T13:14:15Z", "08.01.2026 14:14:15 CET (UTC+01:00)"),
    ("2026-10-08T13:14:15", "08.10.2026 15:14:15 CEST (UTC+02:00)"),
    ("2026-10-08T15:14:15+02:00", "08.10.2026 15:14:15 CEST (UTC+02:00)"),
    ("2026-03-29T00:59:59Z", "29.03.2026 01:59:59 CET (UTC+01:00)"),
    ("2026-03-29T01:00:00Z", "29.03.2026 03:00:00 CEST (UTC+02:00)"),
    ("2026-10-25T00:30:00Z", "25.10.2026 02:30:00 CEST (UTC+02:00)"),
    ("2026-10-25T01:30:00Z", "25.10.2026 02:30:00 CET (UTC+01:00)"),
    (None, "—"),
])
def test_report_dates_follow_berlin_dst_and_disambiguate_repeated_hour(value, expected):
    assert date(value) == expected


def test_timezone_setting_reads_environment_and_rejects_invalid_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/London")
    assert Settings(_env_file=None).timezone == "Europe/London"
    with pytest.raises(ValidationError, match="Unknown IANA timezone"):
        Settings(_env_file=None, timezone="Invalid/Timezone")


def test_timezone_setting_reads_dotenv_and_environment_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("TZ=Europe/Berlin\n", encoding="utf-8")
    assert Settings(_env_file=env_file).timezone == "Europe/Berlin"
    monkeypatch.setenv("TZ", "UTC")
    assert Settings(_env_file=env_file).timezone == "UTC"


@pytest.mark.parametrize("value,unit,expected", [
    (1_000_000, "µWh", "1,000 Wh"),
    (1500, "µWh", "1,500 mWh"),
    (1_250_000_000, "µWh", "1,250 kWh"),
    (0.25, "µWh", "250,000 nWh"),
    (-2_500_000, "µWh", "-2,500 Wh"),
    (999.9999, "µWh", "1,000 mWh"),
    (0, "µWh", "0,000 µWh"),
    (None, "µWh", "—"),
    (float("nan"), "µWh", "—"),
    (float("inf"), "µWh", "—"),
    (0.005, "µA", "5,000 nA"),
    (12_500, "µA", "12,500 mA"),
    (1_200_000, "µA", "1,200 A"),
    (7500, "µC", "7,500 mC"),
    (2_000_000, "µC", "2,000 C"),
    (0.00025, "s", "250,000 µs"),
    (0.125, "s", "125,000 ms"),
    (90, "s", "1,500 min"),
    (7200, "s", "2,000 h"),
    (172800, "s", "2,000 d"),
    (1500, "ms", "1,500 s"),
    (100_000, "", "100 000,000"),
    (99.5, "%", "99,500 %"),
    (1e-12, "s²", "1,000 µs²"),
    (4e6, "µA²", "4,000 mA²"),
    (1e-6, "µWh²", "1,000 nWh²"),
])
def test_report_numbers_choose_readable_units(value, unit, expected):
    assert number(value, unit) == expected


@pytest.mark.parametrize("status", ["created", "scheduled", "starting", "recording", "running", "stopping"])
def test_pdf_rejects_running_measurement_and_cleans_failed_bulk_export(tmp_path, status):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        with manager.db.session() as session:
            session.add(Measurement(id="active", name="Active", status=status))
            session.add(Measurement(id="done", name="Done", status="completed"))
        assert client.get("/api/measurements/active/export/pdf").status_code == 409
        response = client.post("/api/measurements/bulk/export", json={
            "measurement_ids": ["done", "active"], "kind": "pdf"})
        assert response.status_code == 409
        assert not list(manager.raw_store.exports_dir.glob("tmp*.zip"))
