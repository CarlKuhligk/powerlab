from dataclasses import fields

import numpy as np
import pytest

from app.ppk.base import SampleBatch
from app.recorder import RecorderSettings
from app.threshold import ThresholdRecorder
from app.storage.raw_store import RawStore


@pytest.mark.parametrize("chunk_size", [37, 1000, 200_000])
@pytest.mark.parametrize("finish_active", [False, True])
def test_streamed_wakes_match_raw_recording_and_integrate_all_received_samples(chunk_size, finish_active):
    values = np.concatenate((np.full(5000, 4), np.full(500, 4), np.full(1000, 500),
                             np.full(1000, 4), np.full(1000, 750), np.full(3000, 4),
                             np.full(3000, 1000) if finish_active else np.full(3000, 4))).astype(np.float32)
    digital = np.arange(len(values), dtype=np.uint8) % 4
    ticks = np.arange(len(values), dtype=np.int64)
    ticks[6300:] += 20  # Missing samples change elapsed time, never fabricate charge.
    results = []
    for streaming in [False, True]:
        events, overview, chunks, sleeps = [], [], {}, []
        def on_chunk(sequence, idx, current, dig):
            chunks.setdefault(sequence, []).append((idx.copy(), current.copy(), dig.copy()))
        rec = ThresholdRecorder(
            RecorderSettings(sample_rate_hz=10_000, sleep_checkpoint_s=.25, sleep_min_s=.2, wake_threshold_ua=100, wake_min_ms=1, pre_trigger_ms=100, post_trigger_ms=200),
            on_sleep_segment=sleeps.append, on_wake_event=events.append, on_overview=overview.append,
            on_wake_chunk=on_chunk if streaming else None,
        )
        for begin in range(0, len(values), chunk_size):
            end = min(len(values), begin + chunk_size)
            lost = 20 if begin <= 6300 < end else 0
            rec.process(SampleBatch(values[begin:end], digital[begin:end], sample_ticks=ticks[begin:end], detected_lost_samples=lost))
            if streaming:
                assert rec._active is None or rec._active.chunks == []
                assert rec._return_count <= rec.settings.event_close_samples + rec._window
                assert rec._ring._count <= rec._ring.capacity
        rec.finish()
        expected_charge = float(np.sum(values, dtype=np.float64)) / 10_000
        assert rec.total_charge_uc == pytest.approx(expected_charge, rel=1e-13)
        assert rec.average_current_ua == pytest.approx(float(np.mean(values, dtype=np.float64)), rel=1e-13)
        assert rec.total_samples == len(values)
        assert rec.detected_lost_samples == 20
        assert rec.data_coverage_pct == pytest.approx(100 * len(values) / (len(values) + 20))
        assert sleeps
        results.append((events, overview, chunks))
    legacy, streamed = results
    assert len(legacy[0]) == len(streamed[0]) == (2 if finish_active else 1)
    for expected, actual in zip(legacy[0], streamed[0]):
        for field in fields(expected):
            if field.name not in ("sample_index", "current_ua", "digital"):
                assert getattr(actual, field.name) == pytest.approx(getattr(expected, field.name), rel=1e-12)
        arrays = [np.concatenate(parts) for parts in zip(*streamed[2][actual.sequence])]
        for array, field in zip(arrays, ["sample_index", "current_ua", "digital"]):
            np.testing.assert_array_equal(array, getattr(expected, field))
    assert legacy[1] == streamed[1]


@pytest.mark.parametrize("fallback", [False, True])
def test_wake_file_blocks_are_written_before_finish_and_read_back_exactly(tmp_path, monkeypatch, fallback):
    import app.storage.raw_store as module
    if fallback:
        monkeypatch.setattr(module, "pq", None)
        monkeypatch.setattr(module, "pa", None)
    store = RawStore(tmp_path)
    writer = store.stream_event("stream-test", 1)
    writer.block_samples = 1000
    idx = np.arange(2505, dtype=np.int64) + 42
    current = np.linspace(.1, 1000, len(idx), dtype=np.float32)
    digital = np.arange(len(idx), dtype=np.uint8)
    for start in range(0, len(idx), 113):
        writer.append(idx[start:start + 113], current[start:start + 113], digital[start:start + 113])
        assert writer.buffered_samples < 1000
    assert not writer.closed
    if fallback:
        assert len(writer.parts) == 2
    else:
        assert writer.path.exists() and writer.path.stat().st_size > 0
    raw = store.read_event(writer.finish())
    assert writer.closed and writer.buffered_samples == 0
    np.testing.assert_array_equal(raw.sample_index, idx)
    np.testing.assert_array_equal(raw.current_ua, current)
    np.testing.assert_array_equal(raw.digital, digital)


def test_manager_streams_open_wake_and_persists_total_sleep_and_wake_charge(tmp_path, monkeypatch):
    import threading
    import time
    from app.config import Settings
    from app.db import Database
    from app.manager import MeasurementManager
    from app.schemas import MeasurementStartRequest

    class Driver:
        def __init__(self):
            self.tick = 0
            self.exhausted = threading.Event()
            self.stopped = False
        def start(self):
            pass
        def metadata(self):
            return {}
        def read_batch(self):
            if self.tick == 531_000:
                self.exhausted.set()
                time.sleep(.001)
                return None
            count = min(10_000, 531_000 - self.tick)
            ticks = np.arange(self.tick, self.tick + count, dtype=np.int64)
            current = np.where(ticks < 31_000, 4.0, 5000.0).astype(np.float32)
            self.tick += count
            return SampleBatch(current, np.zeros(count, dtype=np.uint8), sample_ticks=ticks)
        def stop(self):
            self.stopped = True
            return SampleBatch(np.full(3, 5000, dtype=np.float32), np.zeros(3, dtype=np.uint8),
                               sample_ticks=np.arange(531_000, 531_003, dtype=np.int64))

    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    settings.prepare()
    db = Database(settings.database_url)
    db.create_all()
    store = RawStore(tmp_path)
    manager = MeasurementManager(settings, db, store)
    driver = Driver()
    monkeypatch.setattr(manager, "_build_driver", lambda _: driver)
    monkeypatch.setattr("app.manager.describe_ppk2_port", lambda _: {"device_id": "TEST", "port": "COM4"})
    started = manager.start(MeasurementStartRequest(name="streaming totals", port="COM4", sleep_min_s=.01, wake_threshold_ua=1000, wake_min_ms=1, pre_trigger_ms=100))
    mid = started["id"]
    assert driver.exhausted.wait(5)
    active = manager._active[mid]
    assert active.recorder.state == "ACTIVE"
    assert active.recorder._active.chunks == []
    writer = active.event_writers[1]
    assert writer.path.exists() and writer.path.stat().st_size > 0
    expected_charge = (31_000 * 4.0 + 500_000 * 5000.0) / 100_000
    manager._update_running_stats(active)
    live = manager.get_measurement(mid)
    assert live["total_charge_uc"] == pytest.approx(expected_charge)
    assert live["events"] == []  # File blocks exist before the event is closed.
    original_finish = writer.finish
    def finish_after_hardware_stops():
        assert driver.stopped
        return original_finish()
    monkeypatch.setattr(writer, "finish", finish_after_hardware_stops)
    finished = manager.stop(mid)
    expected_charge += 3 * 5000 / 100_000
    assert finished["status"] == "completed"
    assert finished["total_samples"] == 531_003
    assert finished["total_charge_uc"] == pytest.approx(expected_charge)
    assert finished["energy_uwh"] == pytest.approx(expected_charge * 3.3 / 3600)
    assert len(finished["events"]) == 1
    raw = store.read_event(str(writer.path.relative_to(tmp_path)))
    # Old sleep samples have been discarded, while their full charge remains.
    assert raw.sample_index[0] == 21_000
    assert raw.sample_index[-1] == 531_002
    assert finished["events"][0]["charge_uc"] == pytest.approx(25_000.15)
    assert expected_charge > finished["events"][0]["charge_uc"]


@pytest.mark.parametrize("chunk_size", [37, 1000, 200_000])
def test_discarded_sleep_samples_keep_exact_accumulated_charge(chunk_size):
    sleeps, events = [], []
    def unexpected_raw_write(*_):
        pytest.fail("Sleep samples must not be written as a raw wake")
    rec = ThresholdRecorder(
        RecorderSettings(sample_rate_hz=10_000, sleep_checkpoint_s=.25, sleep_min_s=.2, wake_threshold_ua=100, wake_min_ms=1, pre_trigger_ms=100, post_trigger_ms=200),
        on_sleep_segment=sleeps.append, on_wake_event=events.append, on_overview=lambda _: None,
        on_wake_chunk=unexpected_raw_write,
    )
    values = (.1234567 + .0001 * np.sin(np.arange(200_033) * .003)).astype(np.float32)
    for begin in range(0, len(values), chunk_size):
        current = values[begin:begin + chunk_size]
        rec.process(SampleBatch(current, np.zeros(len(current), dtype=np.uint8)))
    rec.finish()
    assert events == []
    assert rec._active is None
    expected = float(np.sum(values, dtype=np.float64)) / 10_000
    assert rec.total_charge_uc == pytest.approx(expected, rel=1e-13)
    assert sum(s.charge_uc for s in sleeps) == pytest.approx(expected, rel=1e-13)
    assert sum(s.sample_count for s in sleeps) == len(values)
