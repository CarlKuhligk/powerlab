import math

import pytest
from fastapi.testclient import TestClient

from app.battery_life import combine, evaluate, measured_statistics, normal_approximation, profile
from app.battery_report import report_data, report_data_many
from app.config import Settings
from app.cycle_energy import confirmed_cycle_energy
from app.main import create_app
from app.schemas import BatteryReportRequest, BatteryMultiReportRequest
from test_cycle_energy import fixture

PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC'


def summary():
    return confirmed_cycle_energy(10, 3300, *fixture())


def weighted_sources():
    first = {'voltage_v': 3.3,
             'valid_wake_phases': [{'sequence':1,'duration_s':1,'energy_uwh':1}, {'sequence':2,'duration_s':2,'energy_uwh':3}],
             'valid_sleep_phases': [{'following_wake_sequence':1,'duration_s':9,'energy_uwh':.09},
                                   {'following_wake_sequence':2,'duration_s':18,'energy_uwh':.36}]}
    second = {'voltage_v': 3.3,
              'valid_wake_phases': [{'sequence':1,'duration_s':1,'energy_uwh':9}],
              'valid_sleep_phases': [{'following_wake_sequence':1,'duration_s':9,'energy_uwh':.09}]}
    return [{'id':'a','name':'A','summary':first,'weight':7}, {'id':'b','name':'B','summary':second,'weight':3}]


def test_measurement_weights_change_mean_and_include_between_measurement_variance():
    sources = weighted_sources()
    equal, cycles, custom = (combine(sources, method) for method in ['mean', 'cycle_count', 'custom'])
    assert equal['wake_energy_uwh'] == 5.5 and equal['sleep_power_uw'] == 48
    assert equal['statistics']['wake_energy']['variance'] == pytest.approx(20.4)
    assert cycles['wake_energy_uwh'] == pytest.approx(13/3)
    assert cycles['statistics']['wake_energy']['variance'] == pytest.approx(52/3)
    assert custom['wake_energy_uwh'] == pytest.approx(4.1)
    assert custom['weights'] == pytest.approx([.35,.35,.3])
    mostly_a = combine([{**sources[0],'weight':99}, {**sources[1],'weight':1}], 'custom')
    assert evaluate(mostly_a,1,'sleep',9)['percentiles']['50'] != evaluate(equal,1,'sleep',9)['percentiles']['50']
    only_a = combine([{**sources[0],'weight':1}, {**sources[1],'weight':0}], 'custom')
    assert only_a['count'] == 2 and only_a['statistics']['wake_energy']['max'] == 3
    assert only_a['active_measurement_count'] == 1
    for invalid in [[], [sources[0], sources[0]], [{**s,'weight':0} for s in sources],
                    [{**sources[0],'weight':-1}]]:
        with pytest.raises(ValueError):
            combine(invalid, 'custom')


def test_multi_pdf_records_normalized_weights_and_rejects_invalid_sources(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path/'multi-battery.db'}")
    body = dict(energy_wh=1, mode='sleep', value=86400, chart_png=PNG,
                weighting='custom', sources=[{'measurement_id':'a','weight':7}, {'measurement_id':'b','weight':3}])
    measurements = [{'id':s['id'],'name':s['name'],'cycle_energy':s['summary']} for s in weighted_sources()]
    data = report_data_many(measurements, BatteryMultiReportRequest(**body))
    assert data['weighting'] == 'Eigene Gewichte je Messung'
    assert [s[4] for s in data['sources']] == ['70,000 %', '30,000 %']
    assert all(len(row) == 3 for row in data['percentiles'])
    assert any('P5' in row[0] for row in data['results'])
    with TestClient(create_app(settings)) as client:
        monkeypatch.setattr(client.app.state.manager, 'get_measurement', lambda mid: next(m for m in measurements if m['id'] == mid))
        response = client.post('/api/battery-report', json=body)
        assert response.status_code == 200, response.text
        assert response.content.startswith(b'%PDF-')
        for sources in [[], [body['sources'][0]]*2, [{'measurement_id':'a','weight':-1}],
                        [{'measurement_id':'a','weight':0}]]:
            assert client.post('/api/battery-report', json={**body, 'sources':sources}).status_code == 422
        monkeypatch.setattr(client.app.state.manager, 'get_measurement', lambda mid: (_ for _ in ()).throw(KeyError(mid)))
        assert client.post('/api/battery-report', json=body).status_code == 404


def test_expected_runtime_conserves_energy_and_percentiles_use_paired_cycles():
    data = profile(summary())
    value = evaluate(data, 1, 'sleep', 10)
    expected_power = (500*3.3 + (16*3.3/3)*10) / 10.3
    assert value['power_uw'] == pytest.approx(expected_power)
    assert value['expected_h'] == pytest.approx(1e6/expected_power)
    scenarios = [1e6/((200*3.3 + 4*3.3*10)/10.2),
                 1e6/((800*3.3 + 6*3.3*10)/10.4)]
    assert value['percentiles']['0'] == pytest.approx(min(scenarios))
    assert value['percentiles']['100'] == pytest.approx(max(scenarios))
    assert value['percentiles']['50'] == pytest.approx(sum(scenarios)/2)
    assert evaluate(data, 2, 'sleep', 10)['expected_h'] == pytest.approx(2*value['expected_h'])
    duty = value['duty_pct']
    assert evaluate(data, 1, 'duty', duty)['expected_h'] == pytest.approx(value['expected_h'])
    assert evaluate(data, 1, 'duty', 100)['sleep_s'] == 0
    assert evaluate(data, 1, 'sleep', 100)['expected_h'] > value['expected_h']
    stats = measured_statistics(summary())
    assert stats['sleep_energy']['variance'] == pytest.approx((8*3.3/3600)**2/2)
    assert stats['sleep_power']['variance'] == pytest.approx((2*3.3)**2/2)
    assert stats['wake_energy']['variance'] == pytest.approx((600*3.3/3600)**2/2)


@pytest.mark.parametrize('energy,mode,value', [(0,'sleep',10),(-1,'sleep',10),(math.inf,'sleep',10),
    (1,'sleep',-1),(1,'duty',0),(1,'duty',101),(1,'bad',1)])
def test_invalid_battery_inputs_rejected(energy, mode, value):
    with pytest.raises(ValueError):
        evaluate(profile(summary()), energy, mode, value)


def test_no_cycles_rejected_and_single_cycle_does_not_invent_variance():
    empty = summary()
    empty['valid_wake_phases'] = []
    with pytest.raises(ValueError):
        profile(empty)
    events, segments, markers = fixture()
    single = confirmed_cycle_energy(10, 3300, events[:1], segments, markers)
    assert measured_statistics(single)['wake_energy']['variance'] is None
    assert normal_approximation(profile(single), 1, 'sleep', 86400)['stddev_h'] is None
    assert evaluate(profile(single), 1, 'sleep', 10)['percentiles']['0'] == evaluate(profile(single), 1, 'sleep', 10)['percentiles']['100']


def test_normal_model_centers_expected_runtime_and_uses_weighted_measured_scatter():
    data = combine(weighted_sources(), 'custom')
    point = evaluate(data, 1, 'sleep', 86400)
    normal = normal_approximation(data, 1, 'sleep', 86400)
    scenarios, weights = point['scenarios_h'], data['weights']
    mean = sum(v*w for v,w in zip(scenarios, weights))
    variance = sum(w*(v-mean)**2 for v,w in zip(scenarios, weights))/(1-sum(w*w for w in weights))
    assert normal['mean_h'] == point['expected_h']
    assert normal['variance_h2'] == pytest.approx(variance)
    assert normal['percentiles_h']['5'] == pytest.approx(point['expected_h']-1.6448536269514722*math.sqrt(variance))
    assert normal['percentiles_h']['95'] == pytest.approx(point['expected_h']+1.6448536269514722*math.sqrt(variance))
    assert normal_approximation(data, 2, 'sleep', 86400)['stddev_h'] == pytest.approx(2*normal['stddev_h'])


def test_battery_pdf_contains_recomputed_statistics_and_percentile_table(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path/'battery.db'}")
    body = dict(energy_wh=1, mode='sleep', value=10, range_min=1, range_max=100, chart_png=PNG)
    with TestClient(create_app(settings)) as client:
        measurement = {'id':'battery-test', 'name':'Test #panic("never source")', 'cycle_energy':summary()}
        monkeypatch.setattr(client.app.state.manager, 'get_measurement', lambda mid: measurement)
        data = report_data(measurement, summary(), BatteryReportRequest(**body))
        assert len(data['statistics']) == 3 and len(data['percentiles']) == 5
        assert data['percentiles'][2][:2] == ['P50 · Näherung', '50 %']
        assert data['results'][0][0] == 'Laufzeitschätzung aus mittlerer Leistung'
        response = client.post('/api/measurements/battery-test/battery-report', json=body)
        assert response.status_code == 200, response.text
        assert response.content.startswith(b'%PDF-')
        assert response.headers['content-type'] == 'application/pdf'
        for changes in [{'mode':'duty','range_max':101}, {'value':101},
                        {'chart_png':'data:image/png;base64,invalid'}, {'energy_wh':-1}]:
            assert client.post('/api/measurements/battery-test/battery-report', json={**body,**changes}).status_code == 422
        monkeypatch.setattr(client.app.state.manager, 'get_measurement', lambda mid: (_ for _ in ()).throw(KeyError(mid)))
        assert client.post('/api/measurements/missing/battery-report', json=body).status_code == 404
