from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.db import Database, Measurement, OverviewPoint, WakeEvent
from app.manager import MeasurementManager
from app.storage.raw_store import RawStore


def build_manager(tmp_path: Path) -> MeasurementManager:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'powerlab.db'}",
        sample_rate_hz=100_000,
    )
    settings.prepare()
    db = Database(settings.database_url)
    db.create_all()
    return MeasurementManager(
        settings,
        db,
        RawStore(tmp_path),
    )


def test_overview_returns_wake_events_independently_of_history_downsampling(tmp_path: Path):
    manager = build_manager(tmp_path)
    measurement_id = "history-event-test"
    started = datetime.now(timezone.utc)

    with manager.db.session() as s:
        s.add(
            Measurement(
                id=measurement_id,
                name="history event test",
                started_at=started,
                finished_at=started,
                status="completed",
                sample_rate_hz=100_000,
            )
        )
        # More overview points than requested later, so ordinary history is downsampled.
        for i in range(1000):
            s.add(
                OverviewPoint(
                    measurement_id=measurement_id,
                    sample_index=i * 100,
                    current_ua=4.0,
                    kind="sleep",
                )
            )
        s.add(
            WakeEvent(
                measurement_id=measurement_id,
                sequence=1,
                start_sample=49_000,
                trigger_sample=50_000,
                end_sample=51_000,
                duration_us=10_010.0,
                peak_ua=12_500.0,
                mean_ua=4_000.0,
                charge_uc=40.0,
                raw_file="measurements/history-event-test/events/event_000001.npz",
                digital_mask_seen=1,
            )
        )

    overview = manager.overview(measurement_id, max_points=100)

    assert len(overview["points"]) <= 101
    assert len(overview["events"]) == 1
    event = overview["events"][0]
    assert event["sequence"] == 1
    assert event["trigger_sample"] == 50_000
    assert event["trigger_at"] is not None
    assert event["peak_ua"] == 12_500.0
