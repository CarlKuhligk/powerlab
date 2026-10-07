"""Sleep reference, periodic nuisance peaks, and retrospective curved wake onset."""
import json
import time
import numpy as np
import pytest
from signal_generator import SignalGenerator, MockPPK
from test_threshold import recorder, feed
from app.schemas import MeasurementStartRequest


def periodic_sleep(rate=1000, period=10, repeats=3):
    signal=SignalGenerator(rate=rate).plateau(.1,500).plateau(.2,5,noise=.02,oscillation=.1)
    for _ in range(repeats):
        signal.plateau(period-.02,5,noise=.02,oscillation=.1).plateau(.02,35)
    return signal


@pytest.mark.parametrize('chunk',[1,137,1000000])
def test_periodic_peaks_learned_without_wake_or_startup_reset(chunk):
    signal=periodic_sleep().plateau(.1,5)
    rec,starts,events,markers,sleeps=recorder(sleep_threshold_ua=10,sleep_min_s=.1)
    for batch in signal.batches(chunk):
        rec.process(batch)
    rec.finish()
    info=rec.detection_info()
    assert len(starts)==1 and events==[]
    assert info['sleep_pattern']['period_s']==pytest.approx(10)
    assert info['sleep_pattern']['pulse_count']==3
    assert 4.9<info['sleep_pattern']['mean_ua']<5.1
    assert info['sleep_pattern']['std_ua']>0
    json.dumps(info)
    assert sum(s.charge_uc for s in sleeps)==pytest.approx(rec.total_charge_uc)


@pytest.mark.parametrize('chunk',[7,731,1000000])
def test_curved_wake_before_configured_sleep_threshold_has_all_raw_samples(chunk):
    signal=(SignalGenerator(rate=10000).plateau(.3,5,noise=.02)
            .curved_rise(.6,5,20).plateau(.01,2000).plateau(.01,11000).plateau(.1,5))
    rec,_,events,markers,sleeps=recorder(sample_rate_hz=10000,sleep_threshold_ua=10,sleep_min_s=.1)
    for batch in signal.batches(chunk):
        rec.process(batch)
    rec.finish()
    reference=rec.detection_info()['wake_sleep_reference']
    onset=int(np.flatnonzero(signal.values()[3000:]>reference['upper_ua'])[0])+3000
    assert len(events)==1 and events[0].trigger_sample==onset
    assert signal.values()[onset]<10
    assert onset < int(np.flatnonzero(signal.values()[3000:]>10)[0])+3000
    assert rec.detection_info()['wake_onset_method']=='sleep-pattern-departure'
    np.testing.assert_array_equal(events[0].current_ua,signal.values()[events[0].sample_index])
    assert events[0].charge_uc==pytest.approx(np.sum(signal.values()[onset:9200],dtype=np.float64)/10000)
    assert sum(s.charge_uc for s in sleeps)+events[0].charge_uc==pytest.approx(rec.total_charge_uc)


@pytest.mark.parametrize('chunk',[7,731])
def test_wake_interrupts_due_periodic_peak_and_uses_first_pattern_violation(chunk):
    signal=(periodic_sleep().plateau(9.98,5).plateau(.01,35)
            .curved_rise(.05,35,2000).plateau(.01,11000).plateau(.1,5))
    rec,_,events,markers,sleeps=recorder(sleep_threshold_ua=10,sleep_min_s=.1)
    for batch in signal.batches(chunk):
        rec.process(batch)
    rec.finish()
    assert len(events)==1
    peak_start=round(40.18*1000)
    # The normal 35-uA prefix still matches sleep. The learned pulse's duration
    # expires at ~21 ms; the curved wake can also violate its amplitude earlier.
    assert peak_start+10 < events[0].trigger_sample <= peak_start+21
    assert rec.detection_info()['wake_sleep_reference']['period_s']==10
    assert events[0].trigger_sample < peak_start+60
    assert sum(s.charge_uc for s in sleeps)+events[0].charge_uc==pytest.approx(rec.total_charge_uc)


def test_unconfirmed_activity_at_stop_does_not_teach_periodic_pulse():
    rec,_,events,_,_=recorder(sleep_threshold_ua=10)
    feed(rec,[5]*100+[35]*20)
    rec.finish()
    assert events==[] and rec.detection_info()['sleep_pattern']['pulse_count']==0


def test_fixed_confirmation_still_required_and_isolated_adc_spike_not_onset():
    rec,_,events,markers,_=recorder(sample_rate_hz=10000,sleep_threshold_ua=10)
    feed(rec,[5]*1000+[20000]+[5]*99+[7]*100+[2000]*100+[11000]*30)
    rec.finish()
    assert len(events)==1 and events[0].trigger_sample==1100
    assert [(m.kind,m.sample_index) for m in markers if m.kind=='wake_validated']==[('wake_validated',1329)]


def test_gap_invalidates_period_and_pre_gap_candidate():
    signal=periodic_sleep(period=.2).plateau(.1,5)
    rec,_,_,_,_=recorder(sleep_threshold_ua=10)
    for batch in signal.batches(137):
        rec.process(batch)
    assert rec.detection_info()['sleep_pattern']['period_s']==pytest.approx(.2)
    feed(rec,[5]*100,np.arange(len(signal.values())+10,len(signal.values())+110))
    assert rec.detection_info()['sleep_pattern']['period_s'] is None


@pytest.mark.parametrize('gap,expected_onset', [(32,10000), (100,20100)])
def test_gap_during_wake_prelude_preserves_only_short_observed_departures(gap,expected_onset):
    rec, _, events, markers, _ = recorder(sample_rate_hz=100000)
    feed(rec, [4]*10000 + [2000]*10000)
    # Same sub-ms acquisition loss as the saved third wake: activity remains
    # elevated, but confirmation must still come from consecutive new samples.
    tail = [2000]*10000 + [20000]*500 + [4]*1000
    feed(rec, tail, np.arange(20000+gap, 20000+gap+len(tail)))
    rec.finish()
    assert len(events) == 1
    assert events[0].trigger_sample == expected_onset
    assert rec.detected_lost_samples == gap
    if gap == 32:
        assert events[0].start_sample < events[0].trigger_sample
        assert np.any(np.diff(events[0].sample_index) == 33)
    assert next(m.sample_index for m in markers if m.kind == 'wake_start') == expected_onset


def test_short_gap_preserves_onset_but_restarts_wake_confirmation():
    rec, _, events, markers, _ = recorder(sample_rate_hz=100000)
    feed(rec, [4]*10000 + [20000]*200)
    feed(rec, [20000]*200, np.arange(10232,10432))
    assert rec.state == 'SLEEP'  # 400 received samples cannot bridge the gap.
    feed(rec, [20000]*100)
    assert rec.state == 'ACTIVE'
    rec.finish()
    assert events[0].trigger_sample == 10000
    assert next(m.sample_index for m in markers if m.kind == 'wake_validated') == 10531


def test_short_gap_followed_by_sleep_discards_previous_departure():
    rec, _, events, _, _ = recorder(sample_rate_hz=100000)
    feed(rec, [4]*10000 + [2000]*10000)
    tail = [4]*10000 + [20000]*500
    feed(rec, tail, np.arange(20032,20032+len(tail)))
    rec.finish()
    assert events[0].trigger_sample == 30032


def test_mock_pipeline_exports_pattern_reference_and_energy_without_double_count(tmp_path,monkeypatch):
    from test_history_states import build_manager
    manager=build_manager(tmp_path)
    manager.settings.sample_rate_hz=1000
    signal=(periodic_sleep(period=.2).plateau(.1,5).curved_rise(.1,5,2000)
            .plateau(.01,11000).plateau(.1,5))
    driver=MockPPK(signal,chunk=137)
    monkeypatch.setattr(manager,'_build_driver',lambda _:driver)
    measurement=manager.start(MeasurementStartRequest(name='curved wake',port='MOCK',detection_mode='threshold',
        sleep_threshold_ua=10,sleep_min_s=.1,wake_min_ms=3))
    try:
        deadline=time.monotonic()+5
        while not driver.exhausted and time.monotonic()<deadline:
            time.sleep(.01)
        assert driver.exhausted
    finally:
        result=manager.stop(measurement['id'])
    assert result['wake_count']==1 and result['cycle_energy']['cycle_count']==1
    reference=result['settings']['detection_result']['wake_sleep_reference']
    assert reference['period_s']==pytest.approx(.2)
    assert result['events'][0]['trigger_sample'] < 940
    _,metadata=manager.export_metadata_json(measurement['id'])
    assert json.loads(metadata)['settings']['detection_result']['wake_sleep_reference']==reference


def test_out_of_phase_low_peak_is_not_exempted_from_confirmed_wake_onset():
    signal=periodic_sleep(period=.2).plateau(.1,5).plateau(.02,35).plateau(.01,11000).plateau(.1,5)
    rec,_,events,_,_=recorder(sleep_threshold_ua=10,sleep_min_s=.1)
    for batch in signal.batches(137):
        rec.process(batch)
    rec.finish()
    assert events[0].trigger_sample==900
    assert rec.detection_info()['wake_sleep_reference']['period_s']==pytest.approx(.2)


def test_periodic_adc_spike_is_ignored_in_retrospective_matching():
    signal=periodic_sleep(period=.2).plateau(.18,5)
    signal.parts.append(np.asarray([20000]+[35]*19,dtype=np.float32))
    signal.curved_rise(.05,35,2000).plateau(.01,11000).plateau(.1,5)
    rec,_,events,_,_=recorder(sleep_threshold_ua=10,sleep_min_s=.1)
    for batch in signal.batches(731):
        rec.process(batch)
    rec.finish()
    assert len(events)==1 and events[0].trigger_sample>=1001


def test_inconsistent_pulse_shapes_do_not_get_a_periodic_exemption():
    signal=SignalGenerator(rate=1000).plateau(.2,5)
    for amplitude in [35,350,35]:
        signal.plateau(.18,5).plateau(.02,amplitude)
    signal.plateau(.1,5)
    rec,_,events,_,_=recorder(sleep_threshold_ua=10)
    for batch in signal.batches(137):
        rec.process(batch)
    assert rec.detection_info()['sleep_pattern']['period_s'] is None
