"""Profiles are validated, persist across restarts, and do not start hardware."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def settings_for(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path,
                    database_url=f"sqlite:///{tmp_path / 'profiles.db'}")


def test_profile_can_be_saved_before_measurement_without_metadata_or_hardware(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/measurement-profiles').json() == []
        response = client.post('/api/measurement-profiles', json={
            'name': '  Sensor 3,3 V  ', 'settings': {'wake_threshold_ua': 2500, 'pre_trigger_ms': .025},
        })
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved['name'] == 'Sensor 3,3 V'
        assert saved['settings']['name'] == ''
        assert saved['settings']['port'] is None
        assert saved['settings']['wake_threshold_ua'] == 2500
        assert saved['settings']['pre_trigger_ms'] == .025
        assert client.get('/api/measurements').json() == []
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/measurement-profiles').json() == [saved]


def test_complete_profile_can_be_reused_for_scheduled_measurement(tmp_path):
    settings = settings_for(tmp_path)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    values = dict(name='Sensor test', project='Project A', device='Sensor', serial_number='000123',
                  firmware='v2', hardware_version='Rev. C', notes='Test build', port='COM42',
                  meter_mode='ampere', voltage_mv=2800, start_mode='scheduled',
                  scheduled_start_at=start.isoformat(), stop_mode='wake_count', event_count=7,
                  detection_mode='spectral_compare', spectral_margin_db=18,
                  sleep_threshold_ua=.35, wake_threshold_ua=2500, sleep_min_s=.005,
                  wake_min_ms=.25, pre_trigger_ms=.025, post_trigger_ms=100)
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/measurement-profiles', json={'name': 'Sensor', 'settings': values})
        assert response.status_code == 201, response.text
        profile = client.get('/api/measurement-profiles').json()[0]
        reused = {**profile['settings'], 'name': 'Next measurement'}
        response = client.post('/api/measurements', json=reused)
        assert response.status_code == 200, response.text
        measurement = response.json()
        assert measurement['status'] == 'scheduled'
        for key, value in values.items():
            if key != 'name' and key != 'scheduled_start_at':
                assert measurement['settings'][key] == value, key
        assert measurement['settings']['name'] == 'Next measurement'


@pytest.mark.parametrize('body', [
    {'name': '   ', 'settings': {}},
    {'name': 'Invalid trigger', 'settings': {'sleep_threshold_ua': 10, 'wake_threshold_ua': 5}},
    {'name': 'Invalid duration', 'settings': {'stop_mode': 'duration'}},
    {'name': 'Invalid count', 'settings': {'stop_mode': 'wake_count', 'event_count': 0}},
    {'name': 'Invalid mode', 'settings': {'detection_mode': 'automatic'}},
    {'name': 'Invalid voltage', 'settings': {'voltage_mv': 100}},
])
def test_invalid_profile_is_rejected_without_creating_measurements(tmp_path, body):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        assert client.post('/api/measurement-profiles', json=body).status_code == 422
        assert client.get('/api/measurement-profiles').json() == []
        assert client.get('/api/measurements').json() == []


def test_duplicate_profile_name_does_not_overwrite_settings(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        body = {'name': 'Sensor', 'settings': {'voltage_mv': 3300}}
        assert client.post('/api/measurement-profiles', json=body).status_code == 201
        body['name'] = ' Sensor '
        body['settings']['voltage_mv'] = 1800
        assert client.post('/api/measurement-profiles', json=body).status_code == 409
        assert client.get('/api/measurement-profiles').json()[0]['settings']['voltage_mv'] == 3300
