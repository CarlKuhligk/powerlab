from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app.display_summary import compact_summaries
from app.manager import ActiveRun
from app.ppk.base import SampleBatch
from app.schemas import MeasurementStartRequest
from test_live_lod import build_manager
from app.db import Measurement, WakeEvent


def make_active(manager, mid, count):
    recorder = SimpleNamespace(total_samples=count, detected_lost_samples=0, live_sleep_segment=lambda: None)
    active = ActiveRun(mid, MeasurementStartRequest(name=mid, port="COM4"), datetime.now(timezone.utc), None, recorder)
    active.thread = SimpleNamespace(is_alive=lambda: True)
    manager._active[mid] = active
    with manager.db.session() as session:
        session.add(Measurement(id=mid, name=mid, status="recording", sample_rate_hz=manager.settings.sample_rate_hz,
                                total_samples=count))
    return active


def test_weighted_mean_and_peak_survive_repeated_time_compaction(tmp_path):
    manager = build_manager(tmp_path)
    active = make_active(manager, "weighted", 100_007)
    # Unequal USB batch lengths and a single sharp impulse on a noisy plateau.
    current = np.resize(np.array([3.0, 5.0], dtype=np.float32), 100_007)
    current[50_000] = 10_000
    for a, b in [(0, 7), (7, 90_001), (90_001, len(current))]:
        manager._append_preview(active, a, SampleBatch(current[a:b], np.zeros(b - a, dtype=np.uint8)))
    blocks = active.preview_history
    for budget in [100, 31, 7, 4]:
        blocks = compact_summaries(blocks, budget)
        assert len(blocks) <= budget
        assert sum(p["sample_count"] for p in blocks) == len(current)
        assert sum(p["sum_ua"] for p in blocks) == pytest.approx(float(current.sum(dtype=np.float64)))
        assert max(p["max_ua"] for p in blocks) == 10_000
        assert min(p["min_ua"] for p in blocks) == 3
        assert blocks[0]["sample_index"] == 0
        assert blocks[-1]["end_sample"] == len(current) - 1
    assert sum(p["current_ua"] * p["sample_count"] for p in blocks) / len(current) == pytest.approx(current.mean(dtype=np.float64))


def test_overview_does_not_read_raw_even_for_short_measurement_and_zoom_does(tmp_path, monkeypatch):
    manager = build_manager(tmp_path)
    mid = "overview-then-zoom"
    current = np.full(10_000, 4.0, dtype=np.float32)
    current[5000] = 15_000
    idx = np.arange(len(current), dtype=np.int64)
    digital = np.zeros(len(current), dtype=np.uint8)
    raw_file = manager.raw_store.write_event(mid, 1, idx[4000:6000], current[4000:6000], digital[4000:6000])
    active = make_active(manager, mid, len(current))
    manager._append_preview(active, 0, SampleBatch(current, digital))
    with manager.db.session() as session:
        session.add(WakeEvent(measurement_id=mid, sequence=1, start_sample=4000, trigger_sample=5000,
                              end_sample=5999, duration_us=20_000, peak_ua=15_000, mean_ua=float(current[4000:6000].mean()),
                              charge_uc=1, raw_file=raw_file, digital_mask_seen=0))
    reads = []
    read_event = manager.raw_store.read_event
    def counted_read(*args, **kwargs):
        reads.append(args[0])
        return read_event(*args, **kwargs)
    monkeypatch.setattr(manager.raw_store, "read_event", counted_read)
    monkeypatch.setattr(manager.raw_store, "event_preview", lambda *a, **k: pytest.fail("Overview must not read raw files"))
    result = manager.series(mid, max_points=500, view="overview")
    assert result["display_mode"] == "summary"
    assert reads == []
    assert max(p["max_ua"] for p in result["summary_points"]) == 15_000
    assert max(p["current_ua"] for p in result["summary_points"]) < 50
    assert all(p["kind"] == "mean" for p in result["points"])
    result = manager.series(mid, start_s=.045, end_s=.055, max_points=2000, view="detail")
    assert len(reads) == 1
    assert result["display_mode"] == "detail"
    assert max(p["current_ua"] for p in result["history_points"]) == 15_000
    assert result["summary_points"] == []  # No mean overlay on loaded raw waveform.
    assert result["live_points"] == []  # No redundant preview overlay either.
    result = manager.series(mid, start_s=.03, end_s=.07, max_points=2000, view="detail")
    preview = result["live_points"]
    assert any(p["sample_index"] < 4000 for p in preview)
    assert any(p["sample_index"] > 5999 for p in preview)
    assert not any(4000 <= p["sample_index"] <= 5999 for p in preview)
    assert preview[0]["line_key"] != preview[-1]["line_key"]


def test_compaction_keeps_tick_gaps_and_sample_weighting(tmp_path):
    manager = build_manager(tmp_path)
    active = make_active(manager, "gapped", 1000)
    ticks = np.concatenate((np.arange(1000), np.arange(10_000, 12_000))).astype(np.int64)
    current = np.concatenate((np.full(1000, 4), np.full(2000, 80))).astype(np.float32)
    manager._append_preview(active, 0, SampleBatch(current, np.zeros(len(current), dtype=np.uint8), ticks))
    blocks = compact_summaries(active.preview_history, max_points=4)
    assert sum(p["sample_count"] for p in blocks) == 3000
    assert len({p["line_key"] for p in blocks}) == 2
    assert all(p["end_sample"] < 1000 or p["sample_index"] >= 10_000 for p in blocks)
    assert sum(p["sum_ua"] for p in blocks) == 164_000


def test_zoom_into_indivisible_summary_reports_actual_block_extent(tmp_path):
    manager = build_manager(tmp_path)
    active = make_active(manager, "partial", 800)
    manager._append_preview(active, 0, SampleBatch(np.full(800, 4, dtype=np.float32), np.zeros(800, dtype=np.uint8)))
    result = manager.series("partial", start_s=.001, end_s=.002, view="overview")
    point = result["summary_points"][0]
    assert point["sample_count"] == 400
    assert point["t_s"] == 0
    assert point["end_s"] == .00399
    assert point["display_start_s"] == .001
    assert point["display_end_s"] == .002
