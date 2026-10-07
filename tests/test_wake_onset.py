import time

import numpy as np
import pytest

from app.schemas import MeasurementStartRequest
from signal_generator import MockPPK, SignalGenerator
from test_threshold import feed, recorder


def staircase_signal(rate=1000):
    return (SignalGenerator(rate=rate).plateau(.1, 500).plateau(1.5, 4)
            .plateau(.02, 35).plateau(.1, 4)
            .staircase([(1.2, 2000), (.1, 8000), (.1, 5000), (.05, 11000)])
            .plateau(1.2, 4))


@pytest.mark.parametrize("chunk", [1, 7, 731, 100000])
@pytest.mark.parametrize("rate", [1000, 10000])
def test_staircase_backdates_to_first_flank_not_peak_or_validation(chunk, rate):
    signal = staircase_signal(rate)
    rec, starts, events, markers, sleeps = recorder(sample_rate_hz=rate)
    for batch in signal.batches(chunk):
        rec.process(batch)
    rec.finish()
    assert starts == [round(.1 * rate)]
    assert len(events) == 1
    event = events[0]
    assert event.trigger_sample == round(1.62 * rate)  # 2 mA, not the 35-uA pulse.
    assert event.start_sample == round(.62 * rate)
    assert event.end_sample == round(3.07 * rate) - 1
    assert event.sample_index[-1] == round(4.07 * rate) - 1
    assert event.duration_us == 1_450_000
    assert event.charge_uc == pytest.approx(4250)
    assert [(m.kind, m.sample_index) for m in markers if m.kind.startswith('wake')] == [
        ('wake_start', round(1.62 * rate)),
        ('wake_validated', round(3.02 * rate) + round(.003 * rate) - 1)]
    assert rec.total_charge_uc == pytest.approx(np.sum(signal.values()[round(.1 * rate):], dtype=np.float64) / rate)
    assert sum(s.charge_uc for s in sleeps) + event.charge_uc == pytest.approx(rec.total_charge_uc)
    assert rec._wake_candidate is None


def test_return_to_sleep_discards_even_a_large_unconfirmed_pulse():
    rec, _, events, markers, _ = recorder()
    feed(rec, [4] * 5 + [20000] * 2 + [4] * 2 + [2000] * 10 + [8000] * 4 + [5000] * 4 + [11000] * 3)
    rec.finish()
    assert len(events) == 1 and events[0].trigger_sample == 9
    assert [(m.kind, m.sample_index) for m in markers if m.kind.startswith('wake')] == [
        ('wake_start', 9), ('wake_validated', 29)]


def test_subthreshold_activity_before_actual_flank_is_not_wake_charge():
    rec, _, events, _, sleeps = recorder()
    feed(rec, [4] * 5 + [35] * 40 + [2000] * 10 + [11000] * 3 + [4] * 5)
    rec.finish()
    assert events[0].trigger_sample == 45
    assert events[0].charge_uc == pytest.approx(53)
    assert sum(s.charge_uc for s in sleeps) + events[0].charge_uc == pytest.approx(rec.total_charge_uc)


def test_failed_threshold_attempt_keeps_first_flank_but_restarts_confirmation():
    import json
    rec, _, events, markers, _ = recorder()
    feed(rec, [4] * 5 + [2000] * 10 + [11000] * 2)
    assert rec.state == 'SLEEP' and events == []
    assert json.loads(json.dumps(rec.detection_info()))['wake_confirmation_samples'] == 2
    feed(rec, [5000] * 4 + [11000] * 3)
    rec.finish()
    assert events[0].trigger_sample == 5
    assert [(m.kind, m.sample_index) for m in markers if m.kind.startswith('wake')] == [
        ('wake_start', 5), ('wake_validated', 23)]


def test_single_adc_spike_does_not_become_retrospective_flank():
    rec, _, events, _, _ = recorder(sample_rate_hz=10000)
    feed(rec, [4] * 100 + [20000] + [35] * 199 + [2000] * 100 + [11000] * 30 + [4] * 50)
    rec.finish()
    assert len(events) == 1
    assert events[0].trigger_sample == 300


def test_short_ramp_refines_on_raw_samples_and_rejects_resting_noise():
    signal = (SignalGenerator(rate=10000).plateau(.2, 4, noise=.05)
              .ramp(.001, 4, 2000).plateau(.02, 2000, noise=1)
              .plateau(.01, 11000).plateau(.01, 4))
    rec, _, events, _, _ = recorder(sample_rate_hz=10000)
    for batch in signal.batches(37):
        rec.process(batch)
    rec.finish()
    assert events[0].trigger_sample == 2001  # First rising ramp sample above sleep.


def test_slow_prelude_uses_first_departure_from_confirmed_sleep():
    signal = (SignalGenerator(rate=1000).plateau(.1, 4)
              .ramp(2, 4, 11000).plateau(.02, 11000).plateau(.01, 4))
    rec, _, events, _, _ = recorder()
    for batch in signal.batches(71):
        rec.process(batch)
    rec.finish()
    expected = int(np.flatnonzero(signal.values()[100:] > 4.08)[0])+100
    assert events[0].trigger_sample == expected


def test_gap_prevents_linking_flank_before_gap_to_later_wake():
    rec, _, events, _, _ = recorder()
    feed(rec, [4] * 5 + [2000] * 20)
    feed(rec, [8000] * 5 + [11000] * 3, np.arange(40, 48))
    rec.finish()
    assert events[0].trigger_sample >= 40
    assert rec.detected_lost_samples == 15


def test_long_unconfirmed_candidate_keeps_bounded_buffers_and_closes_spool():
    rec, _, events, _, sleeps = recorder()
    feed(rec, [4] * 1005)
    feed(rec, [2000] * 10000)
    candidate = rec._wake_candidate
    assert candidate.count == 10000 and not candidate.file.closed
    assert rec._ring._count <= rec._ring.capacity
    assert len(candidate.context[0]) <= rec._ring.capacity
    assert rec.total_samples == 11005 and events == []
    feed(rec, [11000] * 3)
    assert candidate.file.closed
    rec.finish()
    assert events[0].trigger_sample == 1005
    assert events[0].sample_index[0] == 5
    assert events[0].duration_us == 10_003_000
    assert sum(s.charge_uc for s in sleeps) + events[0].charge_uc == pytest.approx(rec.total_charge_uc)


def test_stopping_unconfirmed_candidate_preserves_charge_as_sleep():
    rec, _, events, _, sleeps = recorder()
    feed(rec, [4] * 10 + [2000] * 10)
    candidate = rec._wake_candidate
    rec.finish()
    assert candidate.file.closed and rec._wake_candidate is None and events == []
    assert sum(s.charge_uc for s in sleeps) == pytest.approx(rec.total_charge_uc)


def test_millisecond_block_boundary_preserves_first_raw_flank_sample():
    rec, _, events, _, _ = recorder(sample_rate_hz=10000)
    feed(rec, [4] * 100 + [35] * 99 + [2000] * 101 + [11000] * 30 + [4] * 50)
    rec.finish()
    assert events[0].trigger_sample == 199


def test_multi_millisecond_rise_keeps_onset_instead_of_later_threshold():
    signal = (SignalGenerator(rate=10000).plateau(.2, 4)
              .ramp(.02, 4, 2000).plateau(.05, 2000)
              .plateau(.01, 11000).plateau(.01, 4))
    rec, _, events, _, _ = recorder(sample_rate_hz=10000)
    for batch in signal.batches(731):
        rec.process(batch)
    rec.finish()
    assert events[0].trigger_sample == 2001


def test_isolated_range_switching_dip_does_not_discard_initial_wake_data():
    rec, _, events, _, sleeps = recorder(sample_rate_hz=10000)
    values = [4] * 100 + [2000] * 400 + [11000] * 30 + [4] * 50
    values[200] = 4
    feed(rec, values)
    rec.finish()
    assert events[0].trigger_sample == 100
    assert events[0].sample_index[0] == 0
    assert sum(s.charge_uc for s in sleeps) + events[0].charge_uc == pytest.approx(rec.total_charge_uc)


def test_default_analysis_ring_covers_confirmations_and_pre_roll_at_ppk_rate():
    from app.recorder import RecorderSettings
    from app.threshold import ThresholdRecorder
    rec = ThresholdRecorder(RecorderSettings(detection_mode='threshold', pre_trigger_ms=1000),
                            on_sleep_segment=lambda _:None, on_wake_event=lambda _:None,
                            on_overview=lambda _:None)
    assert rec.detection_info()['analysis_buffer_s'] == pytest.approx(6.02)
    assert rec._ring.capacity == 602000


def test_mock_streaming_pipeline_persists_backdated_markers_and_full_staircase(tmp_path, monkeypatch):
    from test_history_states import build_manager
    manager = build_manager(tmp_path)
    manager.settings.sample_rate_hz = 1000
    signal = staircase_signal()
    driver = MockPPK(signal, chunk=137)
    monkeypatch.setattr(manager, '_build_driver', lambda _: driver)
    measurement = manager.start(MeasurementStartRequest(
        name='staircase wake onset', port='MOCK', detection_mode='threshold', sleep_min_s=.005, wake_min_ms=3))
    try:
        deadline = time.monotonic() + 5
        while not driver.exhausted and time.monotonic() < deadline:
            time.sleep(.01)
        assert driver.exhausted
    finally:
        result = manager.stop(measurement['id'])
    assert result['wake_count'] == 1
    event = result['events'][0]
    assert event['duration_us'] == 1_450_000
    raw = manager.event_raw(measurement['id'], event['id'])
    assert raw['sample_index'][0] == 620 and raw['sample_index'][-1] == 4069
    assert [(m['kind'], m['t_us']) for m in raw['state_markers'] if m['kind'].startswith('wake')] == [
        ('wake_start', 0), ('wake_validated', 1_402_000)]
    assert {2000, 8000, 5000, 11000}.issubset(set(raw['current_ua']))
