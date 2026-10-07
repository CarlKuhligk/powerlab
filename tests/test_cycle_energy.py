import json
import time
from types import SimpleNamespace as Row

import pytest

from app.cycle_energy import confirmed_cycle_energy, project
from app.db import Measurement, OverviewPoint, SleepSegment, WakeEvent
from app.schemas import MeasurementStartRequest
from signal_generator import MockPPK, SignalGenerator
from test_history_states import build_manager


def event(start, end, mean, kind='wake'):
    return Row(trigger_sample=start, end_sample=end, mean_ua=mean,
               charge_uc=mean*(end-start+1)/10, event_kind=kind)


def segment(start, end, mean):
    return Row(start_sample=start,end_sample=end,sample_count=end-start+1,
               mean_ua=mean,charge_uc=mean*(end-start+1)/10)


def fixture():
    events=[event(10,11,1000),event(32,35,2000),event(56,57,50000)]
    segments=[segment(0,9,4),segment(12,31,6),segment(36,55,50)]
    markers=[Row(kind=kind,sample_index=tick) for kind,ticks in {
        'sleep_start':[0,12,36], 'sleep_validated':[4,14,40],
        'wake_start':[10,32,56], 'wake_validated':[10,32,56]}.items() for tick in ticks]
    return events,segments,markers


def test_only_complete_cycles_energy_and_weighted_projection():
    events,segments,markers=fixture()
    result=confirmed_cycle_energy(10,3300,events,segments,markers,3600)
    assert result['cycle_count']==2 and result['excluded_wake_count']==1
    phases = result['valid_wake_phases']
    assert len(phases) == 2
    assert [p['start_s'] for p in phases] == [1, 3.2]
    assert [p['mean_ua'] for p in phases] == [1000, 2000]
    assert phases[0]['duration_s'] == .2
    assert phases[0]['charge_uc'] == 200
    assert phases[0]['energy_uwh'] == pytest.approx(200*3.3/3600)
    sleep_phases = result['valid_sleep_phases']
    assert [p['duration_s'] for p in sleep_phases] == [1, 2]
    assert [p['charge_uc'] for p in sleep_phases] == [4, 12]
    assert result['average_wake_energy_uwh']==pytest.approx(500*3.3/3600)
    assert result['average_sleep_energy_uwh']==pytest.approx(8*3.3/3600)
    assert result['average_sleep_current_ua']==pytest.approx(16/3)
    assert result['average_wake_current_ua']==pytest.approx(1000/.6)
    assert result['counted_duration_s']==pytest.approx(3.6)
    assert result['average_cycle_duration_s']==pytest.approx(1.8)
    assert result['average_combined_power_uw']==pytest.approx(1016*3.3/3.6)
    for name,days in [('day',1),('week',7),('month',30),('year',365)]:
        value=result['projections'][name]
        assert value['combined_energy_uwh']==pytest.approx(22352*days)
        assert value['combined_energy_uwh']==pytest.approx(value['wake_energy_uwh']+value['sleep_energy_uwh'])
    assert result['projections']['custom']['combined_energy_uwh']==pytest.approx(931.333333333)


@pytest.mark.parametrize('missing', ['wake_start','wake_validated','sleep_validated','sleep_start'])
def test_missing_confirmation_never_looks_like_zero_energy(missing):
    events,segments,markers=fixture()
    result=confirmed_cycle_energy(10,3300,events,segments,[p for p in markers if p.kind!=missing])
    assert result['cycle_count']==0
    assert result['average_wake_energy_uwh'] is None
    assert result['projections']['day']['combined_energy_uwh'] is None


def test_wake_variability_uses_only_valid_cycles_and_equal_event_weights():
    result = confirmed_cycle_energy(10, 3300, *fixture())
    stats = result['wake_variability']
    assert stats['count'] == 2 and stats['ddof'] == 1
    for key, average, spread in [('duration_s', .3, .02),
                                  ('current_ua', 1500, 500000),
                                  ('energy_uwh', 500*3.3/3600, (600*3.3/3600)**2/2)]:
        assert stats[key]['mean'] == pytest.approx(average)
        assert stats[key]['variance'] == pytest.approx(spread)
        assert stats[key]['stddev'] == pytest.approx(spread**.5)
        assert stats[key]['cv_pct'] == pytest.approx(spread**.5/average*100)


@pytest.mark.parametrize('count', [0, 1])
def test_wake_variability_requires_two_valid_events(count):
    events, segments, markers = fixture()
    result = confirmed_cycle_energy(10, 3300, events[:count], segments, markers)
    stats = result['wake_variability']
    assert stats['count'] == count
    for key in ('duration_s', 'current_ua', 'energy_uwh'):
        assert (stats[key]['mean'] is None) == (count == 0)
        assert all(stats[key][field] is None for field in ('variance', 'stddev', 'cv_pct'))


def test_identical_wake_values_have_zero_variance():
    events, segments, markers = fixture()
    events[1] = event(32, 33, 1000)
    for marker in markers:
        if marker.sample_index == 36:
            marker.sample_index = 34
        elif marker.sample_index == 40:
            marker.sample_index = 35
    stats = confirmed_cycle_energy(10, 3300, events, segments, markers)['wake_variability']
    assert stats['count'] == 2
    for key in ('duration_s', 'current_ua', 'energy_uwh'):
        assert stats[key]['variance'] == stats[key]['stddev'] == stats[key]['cv_pct'] == 0


def test_sleep_without_wake_and_unclosed_wake_excluded():
    events,segments,markers=fixture()
    result=confirmed_cycle_energy(10,3300,[events[-1]],segments,markers)
    assert result['cycle_count']==0 and result['average_sleep_current_ua'] is None


@pytest.mark.parametrize('phase', ['sleep','wake'])
def test_missing_samples_exclude_affected_cycle(phase):
    events,segments,markers=fixture()
    if phase=='sleep':
        segments[0].sample_count-=1
    else:
        events[0].charge_uc-=100
    result=confirmed_cycle_energy(10,3300,events,segments,markers)
    assert result['cycle_count']==1 and result['excluded_cycles_with_gaps']==1


def test_background_charge_counted_once_as_sleep():
    events,segments,markers=fixture()
    events.append(event(4,5,35,'background'))
    segments[:1]=[segment(0,3,4),segment(6,9,4)]
    result=confirmed_cycle_energy(10,3300,events,segments,markers)
    assert result['cycle_count']==2
    assert result['average_sleep_current_ua']==pytest.approx((3.2+7+12)/3)


def small_gap_fixture(sleep_missing=32, wake_missing=16):
    rate = 100000
    events = [Row(trigger_sample=100000, end_sample=199999, mean_ua=1000,
                  charge_uc=1000*(100000-wake_missing)/rate, event_kind='wake')]
    segments = [Row(start_sample=0, end_sample=99999, sample_count=100000-sleep_missing,
                    mean_ua=4, charge_uc=4*(100000-sleep_missing)/rate)]
    markers = [Row(kind=kind, sample_index=tick) for kind, tick in
               [('sleep_start',0),('sleep_validated',100),('wake_start',100000),
                ('wake_validated',100100),('sleep_start',200000),('sleep_validated',200100)]]
    return rate, events, segments, markers


def test_small_gaps_restore_charge_and_report_estimated_samples():
    rate, events, segments, markers = small_gap_fixture()
    result = confirmed_cycle_energy(rate,3300,events,segments,markers)
    assert result['cycle_count'] == result['interpolated_cycle_count'] == 1
    assert result['interpolated_samples'] == 48
    assert result['interpolated_duration_s'] == pytest.approx(.00048)
    assert result['average_sleep_current_ua'] == pytest.approx(4)
    assert result['average_wake_current_ua'] == pytest.approx(1000)
    assert result['valid_sleep_phases'][0]['interpolated_samples'] == 32
    assert result['valid_wake_phases'][0]['interpolated_samples'] == 16
    assert result['projections']['day']['combined_energy_uwh'] == pytest.approx(1004*3.3/2*24)


@pytest.mark.parametrize('phase', ['sleep','wake'])
def test_interpolation_fraction_limit_excludes_larger_gaps(phase):
    rate, events, segments, markers = small_gap_fixture(
        sleep_missing=101 if phase=='sleep' else 0,
        wake_missing=101 if phase=='wake' else 0)
    result = confirmed_cycle_energy(rate,3300,events,segments,markers)
    assert result['cycle_count'] == 0
    assert result['excluded_cycles_with_gaps'] == 1
    assert result['interpolated_samples'] == 0


def test_small_gap_between_sleep_segments_uses_adjacent_means():
    rate, events, segments, markers = small_gap_fixture(0,0)
    segments = [Row(start_sample=0,end_sample=49999,sample_count=50000,mean_ua=4,charge_uc=2),
                Row(start_sample=50032,end_sample=99999,sample_count=49968,mean_ua=6,charge_uc=6*.49968)]
    result = confirmed_cycle_energy(rate,3300,events,segments,markers)
    assert result['cycle_count'] == 1
    assert result['interpolated_samples'] == 32
    assert result['average_sleep_current_ua'] == pytest.approx(2+6*.49968+5*.00032)


def test_interpolation_requires_sleep_coverage_and_rejects_overlap():
    rate, events, segments, markers = small_gap_fixture(0,0)
    for invalid in ([], segments+segments):
        result = confirmed_cycle_energy(rate,3300,events,invalid,markers)
        assert result['cycle_count'] == 0


def test_interpolation_absolute_limit_even_with_high_coverage():
    rate, events, segments, markers = small_gap_fixture(0,0)
    events[0].end_sample = 2099999
    events[0].charge_uc = 1000*(2000000-1001)/rate
    markers[-2].sample_index = 2100000
    markers[-1].sample_index = 2100100
    result = confirmed_cycle_energy(rate,3300,events,segments,markers)
    assert result['cycle_count'] == 0
    assert result['excluded_cycles_with_gaps'] == 1


@pytest.mark.parametrize('duration',[0,-1,float('nan'),float('inf')])
def test_invalid_custom_duration_rejected(duration):
    with pytest.raises(ValueError):
        project({},duration)


def test_mock_pipeline_counts_one_cycle_and_excludes_open_wake_and_last_sleep(tmp_path,monkeypatch):
    manager=build_manager(tmp_path)
    manager.settings.sample_rate_hz=1000
    signal=(SignalGenerator(rate=1000).plateau(.1,500).plateau(.1,4)
            .plateau(.01,11000).plateau(.1,4).plateau(.01,11000))
    driver=MockPPK(signal,chunk=37)
    monkeypatch.setattr(manager,'_build_driver',lambda _:driver)
    measurement=manager.start(MeasurementStartRequest(name='energy cycles',port='MOCK',
        detection_mode='threshold',sleep_min_s=.005,wake_min_ms=3,pre_trigger_ms=0,post_trigger_ms=0))
    try:
        deadline=time.monotonic()+5
        while not driver.exhausted and time.monotonic()<deadline:
            time.sleep(.01)
        assert driver.exhausted
    finally:
        result=manager.stop(measurement['id'])
    summary=result['cycle_energy']
    assert summary['cycle_count']==1 and summary['excluded_wake_count']==1
    assert summary['average_sleep_current_ua']==4
    assert summary['average_wake_energy_uwh']==pytest.approx(110*3.3/3600)
    assert summary['counted_duration_s']==pytest.approx(.11)
    assert manager.overview(measurement['id'])['analysis']['cycle_count']==1
    assert manager.overview(measurement['id'])['analysis']['average_wake_duration_s']==.01
    _, exported=manager.export_metadata_json(measurement['id'])
    assert json.loads(exported)['cycle_energy']==summary
    assert manager.cycle_energy(measurement['id'],600)['projections']['custom']['duration_s']==600


def test_sleep_only_protocol_reports_no_mean_instead_of_baseline(tmp_path,monkeypatch):
    manager=build_manager(tmp_path)
    manager.settings.sample_rate_hz=1000
    driver=MockPPK(SignalGenerator(rate=1000).plateau(.02,4))
    monkeypatch.setattr(manager,'_build_driver',lambda _:driver)
    measurement=manager.start(MeasurementStartRequest(name='sleep only',port='MOCK',detection_mode='threshold',sleep_min_s=.005))
    try:
        deadline=time.monotonic()+5
        while not driver.exhausted and time.monotonic()<deadline:
            time.sleep(.01)
    finally:
        result=manager.stop(measurement['id'])
    assert result['cycle_energy']['cycle_count']==0
    assert manager.overview(measurement['id'])['analysis']['average_sleep_current_ua'] is None
