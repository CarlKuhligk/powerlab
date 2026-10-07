import json
import zipfile

import pytest

from app.db import Measurement, OverviewPoint, SleepSegment, WakeEvent
from app.report import report_data
from test_history_states import build_manager


@pytest.mark.parametrize('with_wake', [False, True])
def test_sleep_chapter_includes_background_and_sleep_without_complete_cycle(tmp_path, with_wake):
    manager = build_manager(tmp_path)
    with manager.db.session() as session:
        session.add(Measurement(id='sleep-report', name='Sleep report', status='completed',
                                sample_rate_hz=10, voltage_mv=3300, total_samples=34 if with_wake else 20,
                                settings_json=json.dumps({'detection_mode':'threshold'})))
        for start, end, current in [(0,9,4), (12,19,6)] + ([(24,33,4)] if with_wake else []):
            session.add(SleepSegment(measurement_id='sleep-report', start_sample=start, end_sample=end,
                                    sample_count=end-start+1, mean_ua=current, min_ua=current,
                                    max_ua=current, std_ua=0, charge_uc=current*(end-start+1)/10))
        session.add(WakeEvent(measurement_id='sleep-report', sequence=1, event_kind='background',
                             start_sample=10, trigger_sample=10, end_sample=11, duration_us=200000,
                             mean_ua=20, peak_ua=25, charge_uc=4, raw_file=''))
        points = [('sleep_start',0),('sleep_validated',1)]
        if with_wake:
            session.add(WakeEvent(measurement_id='sleep-report', sequence=2, event_kind='wake',
                                 start_sample=20, trigger_sample=20, end_sample=23, duration_us=400000,
                                 mean_ua=1000, peak_ua=1500, charge_uc=400, raw_file=''))
            points += [('wake_start',20),('wake_validated',21),('sleep_start',24),('sleep_validated',25)]
        for kind, tick in points:
            session.add(OverviewPoint(measurement_id='sleep-report', kind=kind, sample_index=tick, current_ua=4))
    measurement = manager.get_measurement('sleep-report')
    sleep = measurement['sleep_analysis']
    assert sleep['background_count'] == 1 and sleep['background_charge_uc'] == 4
    assert sleep['recorded_duration_s'] == (3 if with_wake else 2)
    assert sleep['charge_uc'] == pytest.approx(16.8 if with_wake else 12.8)
    assert sleep['average_current_ua'] == pytest.approx(5.6 if with_wake else 6.4)
    assert sleep['energy_uwh'] == pytest.approx(sleep['charge_uc']*3.3/3600)
    assert sleep['valid_phase_count'] == int(with_wake)
    if with_wake:
        phase = sleep['valid_phases'][0]
        assert phase['following_wake_sequence'] == 2
        assert phase['start_s'] == 0 and phase['duration_s'] == 2
        assert phase['charge_uc'] == pytest.approx(12.8)
        assert phase['mean_ua'] == pytest.approx(6.4)
    else:
        assert sleep['valid_phases'] == [] and sleep['average_valid_duration_s'] is None
    _, exported = manager.export_metadata_json('sleep-report')
    assert json.loads(exported)['sleep_analysis'] == sleep
    with zipfile.ZipFile(manager.export_bundle('sleep-report')) as bundle:
        assert json.loads(bundle.read('metadata.json'))['sleep_analysis'] == sleep
        assert 'sleep_segments.csv' in bundle.namelist()
    data = report_data(measurement, manager.overview('sleep-report'))
    assert ['Hintergrundereignisse im Sleep', '1'] in data['sleep_results']
    assert len(data['sleep_phases']) == int(with_wake)
    assert data['wake_variability_count'] == str(int(with_wake))
    _, pdf = manager.export_report_pdf('sleep-report')
    assert pdf.startswith(b'%PDF-')


def test_empty_sleep_chapter_has_no_fabricated_current_or_energy(tmp_path):
    manager = build_manager(tmp_path)
    with manager.db.session() as session:
        session.add(Measurement(id='empty-sleep', name='No Sleep', status='no_sleep'))
    sleep = manager.get_measurement('empty-sleep')['sleep_analysis']
    assert sleep['sample_count'] == 0 and sleep['valid_phases'] == []
    assert sleep['average_current_ua'] is sleep['charge_uc'] is sleep['energy_uwh'] is None
