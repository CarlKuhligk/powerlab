"""User-defined measurement context survives profiles, edits, restarts and reports."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db import Database, Measurement
from app.main import create_app


def test_custom_fields_survive_profile_schedule_edit_restart_and_exports(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path,
                        database_url=f"sqlite:///{tmp_path / 'custom.db'}")
    fields = [{'label': '  Umgebungstemperatur  ', 'value': '23 °C'},
              {'label': 'Build', 'value': '00042'},
              {'label': '#panic("label")', 'value': '#panic("value")\nTest <b>value</b>'}]
    expected = [{**field, 'label': field['label'].strip()} for field in fields]
    body = dict(name='Custom context', notes='Test note', port='COM42', start_mode='scheduled',
                scheduled_start_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                custom_fields=fields)
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/measurement-profiles', json={'name': 'Sensor', 'settings': body})
        assert response.status_code == 201, response.text
        profile = client.get('/api/measurement-profiles').json()[0]
        assert profile['settings']['custom_fields'] == expected
        response = client.post('/api/measurements', json=profile['settings'])
        assert response.status_code == 200, response.text
        measurement = response.json()
        mid = measurement['id']
        assert measurement['custom_fields'] == expected
        assert measurement['settings']['custom_fields'] == expected
        expected[0]['value'] = '25 °C'
        body['custom_fields'] = expected
        response = client.patch(f'/api/measurements/{mid}/scheduled', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['custom_fields'] == expected
        # A normal metadata edit must not remove custom context.
        response = client.patch(f'/api/measurements/{mid}', json={'notes': 'Updated note'})
        assert response.status_code == 200
        assert response.json()['custom_fields'] == expected
        # Remove one field, rename another and add a field via metadata editing.
        expected = [expected[0], expected[2], {'label': 'Board revision', 'value': 'Rev. D'}]
        expected[0]['label'] = 'Raumtemperatur'
        response = client.patch(f'/api/measurements/{mid}', json={'custom_fields': expected})
        assert response.status_code == 200, response.text
        assert response.json()['settings']['custom_fields'] == expected
        with client.app.state.db.session() as session:
            session.get(Measurement, mid).status = 'completed'
        for path in [f'/api/measurements/{mid}', f'/api/measurements/{mid}/export/json']:
            assert client.get(path).json()['custom_fields'] == expected
        response = client.get(f'/api/measurements/{mid}/export/bundle')
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert json.loads(bundle.read('metadata.json'))['custom_fields'] == expected
        response = client.get(f'/api/measurements/{mid}/export/pdf')
        assert response.status_code == 200, response.text
        with pymupdf.open(stream=response.content, filetype='pdf') as document:
            contents = ' '.join(page.get_text() for page in document)
            for field in expected:
                assert field['label'] in contents
                for line in field['value'].splitlines():
                    assert line in contents
            assert 'Build' not in contents
            assert 'Firmware-Version' not in contents
        snapshot = json.loads((tmp_path / 'measurements' / mid / 'metadata.json').read_text(encoding='utf-8'))
        assert snapshot['custom_fields'] == expected
    with TestClient(create_app(settings)) as client:
        assert client.get(f'/api/measurements/{mid}').json()['custom_fields'] == expected
        assert client.get('/api/measurement-profiles').json()[0]['settings']['custom_fields'] == profile['settings']['custom_fields']
        response = client.patch(f'/api/measurements/{mid}', json={'custom_fields': []})
        assert response.status_code == 200
        assert response.json()['custom_fields'] == []
        assert response.json()['settings']['custom_fields'] == []


@pytest.mark.parametrize('fields', [
    [{'label': '', 'value': 'test'}], [{'label': '   ', 'value': 'test'}],
    [{'label': 'x' * 161, 'value': 'test'}], [{'label': 'Test', 'value': 'x' * 4001}],
    [{'label': f'Field {n}', 'value': ''} for n in range(101)],
])
def test_invalid_custom_fields_are_rejected_for_profiles_creation_and_edit(tmp_path, fields):
    settings = Settings(_env_file=None, data_dir=tmp_path,
                        database_url=f"sqlite:///{tmp_path / 'validation.db'}")
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/measurement-profiles', json={'name': 'Bad', 'settings': {'custom_fields': fields}}).status_code == 422
        assert client.post('/api/measurements', json={'name': 'Bad', 'port': 'COM42', 'custom_fields': fields}).status_code == 422
        assert client.patch('/api/measurements/unused', json={'custom_fields': fields}).status_code == 422
        assert client.get('/api/measurement-profiles').json() == []
        assert client.get('/api/measurements').json() == []


def test_old_measurements_get_empty_custom_fields_without_losing_metadata(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE measurements (id VARCHAR(36) PRIMARY KEY, name VARCHAR(160), firmware VARCHAR(160))"))
        connection.execute(text("INSERT INTO measurements VALUES ('legacy', 'Existing', 'v1.2')"))
    engine.dispose()
    database = Database(url)
    database.create_all()
    database.create_all()
    with database.engine.begin() as connection:
        assert connection.execute(text('SELECT name, firmware, custom_fields_json FROM measurements')).one() == ('Existing', 'v1.2', '[]')
