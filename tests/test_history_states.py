from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database, Measurement, SleepSegment, WakeEvent
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


def test_completed_history_returns_state_timeline_and_cycle_statistics(tmp_path: Path):
    manager = build_manager(tmp_path)
    mid = "states-test"
    started = datetime.now(timezone.utc)
    sr = 100_000

    with manager.db.session() as s:
        s.add(
            Measurement(
                id=mid,
                name="state history",
                started_at=started,
                finished_at=started,
                status="completed",
                sample_rate_hz=sr,
                total_samples=3_000_000,
            )
        )
        # Weighted sleep mean = (4*1M + 6*2M) / 3M = 5.333... uA
        s.add(SleepSegment(measurement_id=mid, start_sample=0, end_sample=999_999, sample_count=1_000_000,
                           mean_ua=4.0, min_ua=3.9, max_ua=4.1, std_ua=0.02, charge_uc=40.0))
        s.add(SleepSegment(measurement_id=mid, start_sample=1_000_000, end_sample=2_999_999, sample_count=2_000_000,
                           mean_ua=6.0, min_ua=5.9, max_ua=6.1, std_ua=0.02, charge_uc=120.0))
        # Triggers at 10 s and 20 s -> 10 s period. Wake means 1000 uA/0.1 s and 2000 uA/0.2 s.
        s.add(WakeEvent(measurement_id=mid, sequence=1, start_sample=990_000, trigger_sample=1_000_000,
                        end_sample=1_009_999, duration_us=100_000.0, peak_ua=1500.0, mean_ua=1000.0,
                        charge_uc=100.0, raw_file="x1.npz", digital_mask_seen=0))
        s.add(WakeEvent(measurement_id=mid, sequence=2, start_sample=1_990_000, trigger_sample=2_000_000,
                        end_sample=2_019_999, duration_us=200_000.0, peak_ua=2500.0, mean_ua=2000.0,
                        charge_uc=400.0, raw_file="x2.npz", digital_mask_seen=0))

    overview = manager.overview(mid)
    a = overview["analysis"]

    assert a["average_period_s"] == pytest.approx(10.0)
    assert a["average_sleep_current_ua"] == pytest.approx(16.0 / 3.0)
    assert a["average_wake_current_ua"] == pytest.approx(500.0 / 0.3)
    assert a["average_wake_duration_s"] == pytest.approx(0.15)
    assert a["wake_count"] == 2
    assert [p["state"] for p in overview["timeline"][:5]] == ["sleep", "wake", "sleep", "wake", "sleep"]
    assert overview["timeline"][1]["sample_index"] == 1_000_000
    assert overview["timeline"][3]["sample_index"] == 2_000_000
