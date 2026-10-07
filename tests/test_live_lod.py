from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.config import Settings
from app.db import Database, Measurement, OverviewPoint, SleepSegment, WakeEvent
from app.manager import MeasurementManager
from app.manager import ActiveRun
from app.ppk.base import SampleBatch
from app.recorder import RecorderSettings
from app.threshold import ThresholdRecorder
from app.schemas import MeasurementStartRequest
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


def test_live_series_is_bounded_and_zoom_loads_raw_wake_data(tmp_path: Path, monkeypatch):
    manager = build_manager(tmp_path)
    mid = "lod-test"
    started = datetime.now(timezone.utc)
    sample_rate = 100_000

    raw_idx = np.arange(995_000, 1_005_000, dtype=np.int64)
    raw_current = np.full(raw_idx.size, 4.0, dtype=np.float32)
    raw_current[4_900:5_100] = np.linspace(4.0, 15_000.0, 200, dtype=np.float32)
    raw_digital = np.zeros(raw_idx.size, dtype=np.uint8)
    raw_file = manager.raw_store.write_event(mid, 1, raw_idx, raw_current, raw_digital)

    with manager.db.session() as s:
        s.add(
            Measurement(
                id=mid,
                name="LOD test",
                started_at=started,
                finished_at=started,
                status="completed",
                sample_rate_hz=sample_rate,
                total_samples=6_000_000,
            )
        )
        # Dense synthetic overview to ensure the returned line must be bounded.
        for i in range(5000):
            s.add(
                OverviewPoint(
                    measurement_id=mid,
                    sample_index=i * 1200,
                    current_ua=4.0 + (i % 7) * 0.01,
                    kind="sleep",
                )
            )
        s.add(OverviewPoint(measurement_id=mid, sample_index=1_000_000, current_ua=15_000.0, kind="event_peak"))
        s.add(
            WakeEvent(
                measurement_id=mid,
                sequence=1,
                start_sample=995_000,
                trigger_sample=1_000_000,
                end_sample=1_005_000,
                duration_us=100_010.0,
                peak_ua=15_000.0,
                mean_ua=2500.0,
                charge_uc=250.0,
                raw_file=raw_file,
                digital_mask_seen=0,
            )
        )

    reads = []
    read_event = manager.raw_store.read_event
    event_preview = manager.raw_store.event_preview
    def counted_read(path, **kwargs):
        reads.append(path)
        return read_event(path, **kwargs)
    def counted_preview(path, **kwargs):
        reads.append(path)
        return event_preview(path, **kwargs)
    monkeypatch.setattr(manager.raw_store, "read_event", counted_read)
    monkeypatch.setattr(manager.raw_store, "event_preview", counted_preview)
    full = manager.series(mid, max_points=500)
    assert full["start_s"] == 0
    assert full["latest_s"] > 59.9
    assert full["point_count"] <= 502
    assert full["detail"] == "adaptive-overview+separate-live"
    assert len(full["events"]) == 1
    envelope = [p for p in full["history_points"] if p["kind"] == "wake_envelope"]
    assert len(envelope) > 3
    assert max(p["current_ua"] for p in envelope) > 10_000
    assert all(p["line_key"].startswith("wake-") for p in envelope)
    manager.series(mid, max_points=500)
    assert len(reads) == 1  # A live refresh reuses the compact envelope.

    zoom = manager.series(mid, start_s=9.95, end_s=10.05, max_points=2000)
    assert zoom["detail"] == "raw+wake+separate-live"
    assert zoom["point_count"] <= 2002
    assert zoom["point_count"] > 20
    assert max(p["current_ua"] for p in zoom["points"]) > 10_000
    assert zoom["events"][0]["sequence"] == 1
    assert len(reads) == 2  # Zoom reads the original waveform.


def test_live_series_keeps_recent_preview_separate_from_history(tmp_path: Path):
    from collections import deque
    from types import SimpleNamespace
    import threading

    manager = build_manager(tmp_path)
    mid = "separate-live-test"
    started = datetime.now(timezone.utc)
    sr = 100_000

    with manager.db.session() as s:
        s.add(
            Measurement(
                id=mid,
                name="separate live",
                started_at=started,
                status="recording",
                sample_rate_hz=sr,
                total_samples=10_000_000,
            )
        )
        s.add(OverviewPoint(measurement_id=mid, sample_index=50_000, current_ua=4.0, kind="calibration"))
        s.add(OverviewPoint(measurement_id=mid, sample_index=6_000_000, current_ua=4.1, kind="sleep"))

    preview = deque(
        {"sample_index": i, "current_ua": 4.2}
        for i in range(8_000_000, 10_000_001, 10_000)
    )
    open_sleep = SimpleNamespace(
        start_sample=6_000_001,
        end_sample=10_000_000,
        mean_ua=4.15,
    )
    recorder = SimpleNamespace(
        total_samples=10_000_001,
        detected_lost_samples=0,
        live_sleep_segment=lambda: open_sleep,
    )
    active = SimpleNamespace(
        thread=SimpleNamespace(is_alive=lambda: True),
        preview_lock=threading.Lock(),
        preview=preview,
        recorder=recorder,
    )
    manager._active[mid] = active

    result = manager.series(mid, max_points=500)

    assert result["history_points"]
    assert result["live_points"]
    assert all(p["kind"] != "live" for p in result["history_points"])
    assert all(p["kind"] == "live" for p in result["live_points"])
    assert any(p["kind"] == "open_sleep" for p in result["history_points"])
    assert result["history_points"][-1]["t_s"] >= 99.9
    assert result["live_points"][0]["t_s"] >= 80.0


def test_sleep_history_uses_clipped_flat_segments_with_independent_lines(tmp_path: Path):
    manager = build_manager(tmp_path)
    mid = "sleep-lines"
    with manager.db.session() as s:
        s.add(Measurement(id=mid, name=mid, status="completed", sample_rate_hz=100_000, total_samples=6_000_001))
        for start, end, mean in [(0, 1_000_000, 4.0), (4_000_000, 6_000_000, 50.0)]:
            s.add(SleepSegment(
                measurement_id=mid, start_sample=start, end_sample=end,
                sample_count=end - start + 1, mean_ua=mean, min_ua=mean,
                max_ua=mean, std_ua=0.0, charge_uc=0.0,
            ))
            s.add(OverviewPoint(measurement_id=mid, sample_index=end, current_ua=mean, kind="sleep"))
    result = manager.series(mid, start_s=5.0, end_s=45.0)
    points = result["history_points"]
    assert [p["t_s"] for p in points] == [5.0, 10.0, 40.0, 45.0]
    assert [p["current_ua"] for p in points] == [4.0, 4.0, 50.0, 50.0]
    assert points[0]["line_key"] == points[1]["line_key"]
    assert points[2]["line_key"] == points[3]["line_key"]
    assert points[1]["line_key"] != points[2]["line_key"]


def test_long_unfinished_wake_keeps_full_acquisition_history_after_live_tail_rolls_over(tmp_path: Path):
    from collections import deque
    from types import SimpleNamespace

    manager = build_manager(tmp_path)
    manager.settings.sample_rate_hz = 1000
    mid = "long-open-wake"
    started = datetime.now(timezone.utc)
    events = []
    recorder = ThresholdRecorder(
        RecorderSettings(sample_rate_hz=1000, sleep_min_s=.1, wake_threshold_ua=40), on_sleep_segment=lambda x: None,
        on_wake_event=events.append, on_overview=lambda x: None,
    )
    active = ActiveRun(mid, MeasurementStartRequest(name=mid, port="COM4"), started, None, recorder)
    active.thread = SimpleNamespace(is_alive=lambda: True)
    active.preview = deque(maxlen=60_000)
    manager._active[mid] = active
    with manager.db.session() as s:
        s.add(Measurement(id=mid, name=mid, status="recording", sample_rate_hz=1000, started_at=started))
    for second in range(270):
        current = np.full(1000, 4.0 if second < 10 else 50.0 + second % 3, dtype=np.float32)
        current[::2] -= .01
        current[1::2] += .01
        if second == 40: current[500] = 2000.0
        if second == 269: current[500] = 3000.0
        batch = SampleBatch(current, np.zeros(1000, dtype=np.uint8))
        recorder.process(batch)
        manager._append_preview(active, second * 1000, batch)
    assert recorder.state == "ACTIVE"
    assert events == []  # Nothing has been persisted for this long wake yet.
    assert active.preview[0]["sample_index"] > 10_000
    assert len(active.preview_history) <= 80_000
    result = manager.series(mid, max_points=500)
    history = result["history_points"]
    assert history[0]["t_s"] == 0
    for second in [15, 50, 100, 140]:
        assert any(second - 2 <= p["t_s"] <= second + 2 and p["current_ua"] >= 49 for p in history)
    assert result["display_mode"] == "summary"
    assert max(p["max_ua"] for p in history) == 2000.0
    assert max(p["current_ua"] for p in history) < 60.0
    assert history[-1]["t_s"] < result["live_points"][0]["t_s"]
    assert result["live_points"][-1]["end_sample"] == 269_999
    assert max(p["max_ua"] for p in result["live_points"]) == 3000.0
    assert result["history_point_count"] + result["live_point_count"] <= 504
    # Zooming into the vanished part still returns its waveform.
    zoom = manager.series(mid, start_s=35, end_s=45, max_points=500)
    assert zoom["history_points"]
    assert max(p["max_ua"] for p in zoom["summary_points"]) == 2000.0
    assert zoom["live_points"] == []


def test_acquisition_preview_keeps_boundaries_and_breaks_device_tick_gaps(tmp_path: Path):
    from types import SimpleNamespace
    manager = build_manager(tmp_path)
    recorder = SimpleNamespace()
    active = ActiveRun("gap-preview", MeasurementStartRequest(name="gap-preview", port="COM4"), datetime.now(timezone.utc), None, recorder)
    ticks = np.array([0, 1, 2, 3, 1000, 1001, 1002], dtype=np.int64)
    batch = SampleBatch(np.array([4, 5, 6, 4, 50, 60, 50], dtype=np.float32), np.zeros(7, dtype=np.uint8), sample_ticks=ticks)
    manager._append_preview(active, 0, batch)
    points = active.preview_history
    assert points[0]["sample_index"] == 0
    assert points[-1]["end_sample"] == 1002
    before = [p for p in points if p["sample_index"] < 1000]
    after = [p for p in points if p["sample_index"] >= 1000]
    assert len({p["line_key"] for p in before}) == 1
    assert len({p["line_key"] for p in after}) == 1
    assert before[-1]["line_key"] != after[0]["line_key"]
