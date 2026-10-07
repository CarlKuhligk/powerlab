import time

import numpy as np
import pytest
from pydantic import ValidationError

from app.ppk.base import SampleBatch
from app.recorder import RecorderSettings
from app.schemas import MeasurementStartRequest
from app.threshold import ThresholdRecorder
from signal_generator import MockPPK, SignalGenerator


def recorder(**settings):
    starts, events, markers, sleeps = [], [], [], []
    config = dict(sample_rate_hz=1000, detection_mode="threshold", sleep_min_s=.005, wake_min_ms=3,
                  pre_trigger_ms=1000, post_trigger_ms=1000)
    config.update(settings)
    rec = ThresholdRecorder(RecorderSettings(**config), on_protocol_start=starts.append,
                            on_wake_event=events.append, on_sleep_segment=sleeps.append,
                            on_overview=markers.append)
    return rec, starts, events, markers, sleeps


def feed(rec, values, ticks=None):
    values = np.asarray(values, dtype=np.float32)
    rec.process(SampleBatch(values, np.zeros(len(values), dtype=np.uint8), ticks))


@pytest.mark.parametrize("chunk", [1, 7, 731, 10000])
def test_exact_edges_validation_and_one_second_raw_context(chunk):
    signal = SignalGenerator(rate=1000).plateau(.1, 500).plateau(1.5, 4)
    # No learned periodic peaks: uninterrupted departure at 35 uA belongs to wake.
    signal.plateau(.02, 35).plateau(.03, 20000).plateau(1.2, 4)
    rec, starts, events, markers, sleeps = recorder()
    for batch in signal.batches(chunk):
        rec.process(batch)
    rec.finish()
    assert starts == [100]
    assert len(events) == 1
    event = events[0]
    assert (event.start_sample, event.trigger_sample, event.end_sample) == (500, 1500, 1549)
    assert (event.sample_index[0], event.sample_index[-1]) == (500, 2549)
    assert event.duration_us == 50000
    assert event.charge_uc == pytest.approx(600.7)
    assert [(m.kind, m.sample_index) for m in markers if m.kind in {
        "sleep_start", "wake_start", "sleep_validated", "wake_validated"}] == [
            ("sleep_start", 0), ("sleep_validated", 4), ("wake_start", 1500),
            ("wake_validated", 1522), ("sleep_start", 1550), ("sleep_validated", 1554)]
    assert rec.total_charge_uc == pytest.approx(np.sum(signal.values()[100:], dtype=np.float64) / 1000)
    assert sum(s.charge_uc for s in sleeps) + sum(e.charge_uc for e in events) == pytest.approx(rec.total_charge_uc)


def test_short_wake_and_intermediate_currents_preserve_sleep():
    rec, starts, events, _, _ = recorder()
    feed(rec, [4] * 5 + [20000] * 2 + [35] * 20 + [10000] * 3 + [4] * 5)
    rec.finish()
    assert starts == [0] and events == []
    assert rec.total_samples == 35


def test_sleep_must_be_continuously_below_strict_threshold():
    rec, starts, events, markers, _ = recorder()
    feed(rec, [4] * 4 + [5] + [4] * 4)
    assert starts == []
    feed(rec, [4] + [20000] * 3 + [4] * 4 + [35] + [4] * 4)
    assert starts == [5]
    assert rec.state == "ACTIVE"
    feed(rec, [4])
    assert rec.state == "SLEEP"
    rec.finish()
    assert len(events) == 1
    assert events[0].end_sample == 12  # Interrupted sleep belongs to the wake.
    assert [m.sample_index for m in markers if m.kind == "sleep_start"] == [0, 13]


@pytest.mark.parametrize("phase", ["startup", "wake", "return"])
def test_data_gap_interrupts_confirmation(phase):
    rec, starts, events, markers, _ = recorder(pre_trigger_ms=0, post_trigger_ms=0)
    if phase == "startup":
        feed(rec, [4] * 4)
        feed(rec, [4] * 4, np.arange(10, 14))
        assert starts == []
        feed(rec, [4])
        assert starts == [10]
    elif phase == "wake":
        feed(rec, [4] * 5 + [20000] * 2)
        feed(rec, [20000] * 2, np.arange(10, 12))
        assert rec.state == "SLEEP"
        feed(rec, [20000])
        assert rec.state == "ACTIVE"
        rec.finish()
        assert events[0].trigger_sample == 10
    else:
        feed(rec, [4] * 5 + [20000] * 3 + [4] * 4)
        feed(rec, [4] * 4, np.arange(20, 24))
        assert rec.state == "ACTIVE"
        feed(rec, [4])
        assert rec.state == "SLEEP"
        assert [m.sample_index for m in markers if m.kind == "sleep_start"] == [0, 20]


def test_stop_never_validates_short_candidates():
    rec, starts, events, _, _ = recorder()
    feed(rec, [4] * 4)
    rec.finish()
    assert starts == [] and rec.total_samples == 0
    rec, _, events, markers, _ = recorder()
    feed(rec, [4] * 5 + [20000] * 3 + [4] * 4)
    rec.finish()
    assert len(events) == 1
    assert len([m for m in markers if m.kind == "sleep_start"]) == 1


@pytest.mark.parametrize("changes", [dict(wake_threshold_ua=5), dict(sleep_threshold_ua=0),
                                      dict(wake_min_ms=0), dict(sleep_min_s=float('nan'))])
def test_schema_rejects_invalid_threshold_settings(changes):
    with pytest.raises(ValidationError):
        MeasurementStartRequest(name="invalid", port="MOCK", detection_mode="threshold", **changes)


def test_mock_pipeline_persists_thresholds_markers_and_raw_context(tmp_path, monkeypatch):
    from test_history_states import build_manager
    manager = build_manager(tmp_path)
    manager.settings.sample_rate_hz = 1000
    signal = SignalGenerator(rate=1000).plateau(.1, 500).plateau(1.5, 4)
    # No learned periodic peaks: uninterrupted departure at 35 uA belongs to wake.
    signal.plateau(.02, 35).plateau(.03, 20000).plateau(1.2, 4)
    driver = MockPPK(signal, chunk=137)
    monkeypatch.setattr(manager, "_build_driver", lambda _: driver)
    measurement = manager.start(MeasurementStartRequest(
        name="fixed thresholds", port="MOCK", detection_mode="threshold", sleep_min_s=.005, wake_min_ms=3))
    try:
        deadline = time.monotonic() + 5
        while not driver.exhausted and time.monotonic() < deadline:
            time.sleep(.01)
        assert driver.exhausted
    finally:
        result = manager.stop(measurement["id"])
    assert result["wake_count"] == 1
    assert result["settings"]["detection_result"]["mode"] == "threshold"
    assert result["settings"]["pre_trigger_ms"] == result["settings"]["post_trigger_ms"] == 1000
    assert result["settings"]["detection_result"]["startup_excluded_s"] == .1
    series = manager.series(measurement["id"])
    assert {m["kind"] for m in series["state_markers"]} == {
        "sleep_start", "wake_start", "sleep_validated", "wake_validated"}
    raw = manager.event_raw(measurement["id"], result["events"][0]["id"])
    assert [(m["kind"], m["t_us"]) for m in raw["state_markers"]] == [
        ("wake_start", 0), ("wake_validated", 22000),
        ("sleep_start", 50000), ("sleep_validated", 54000)]
