"""Export contract and page layout checks using the rendered PDF."""
from copy import deepcopy
from datetime import datetime, timezone

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.battery_report import render_report, render_multi_report
from app.config import Settings
from app.main import create_app
from app.schemas import BatteryReportRequest, BatteryMultiReportRequest
from test_battery_life import summary


CREATED = datetime(2026, 10, 7, 13, 14, 15, 123456, tzinfo=timezone.utc)


def assert_page_content_fits(document):
    for page in document:
        for block in page.get_text('dict')['blocks']:
            if block['type'] == 0:
                for line in block['lines']:
                    for span in line['spans']:
                        rect = pymupdf.Rect(span['bbox'])
                        assert page.rect.contains(rect), span['text']


@pytest.mark.parametrize('multi', [False, True])
def test_download_and_pdf_share_the_creation_timestamp_without_browser_chart(tmp_path, monkeypatch, multi):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return CREATED

    monkeypatch.setattr('app.main.datetime', FrozenDatetime)
    measurement = {'id': 'battery-test', 'name': 'Sensor · Laufzeitprüfung', 'cycle_energy': summary()}
    settings = Settings(_env_file=None, data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'battery.db'}")
    body = {'energy_wh': 1, 'mode': 'sleep', 'value': 86400}
    if multi:
        body.update(sources=[{'measurement_id': 'battery-test'}], weighting='mean')
    with TestClient(create_app(settings)) as client:
        monkeypatch.setattr(client.app.state.manager, 'get_measurement', lambda _: measurement)
        response = client.post('/api/battery-report' if multi else
                               '/api/measurements/battery-test/battery-report', json=body)
    assert response.status_code == 200, response.text
    assert response.headers['content-disposition'] == (
        f'attachment; filename="battery_life_{"combined" if multi else "battery-test"}_'
        '2026-10-07_13-14-15_123456Z.pdf"')
    with pymupdf.open(stream=response.content, filetype='pdf') as document:
        assert len(document) == 2
        for page in document:
            assert '07.10.2026 13:14:15.123456 UTC' in page.get_text()
        assert 'Berechnetes Betriebsszenario' in document[0].get_text()
        assert 'Messgrundlage und Verfahren' in document[1].get_text()
        assert 'Rechenmodell' in document[1].get_text()
        assert len(document[0].get_drawings()) > 40  # Real vector chart, not the old 1px fixture PNG.
        assert_page_content_fits(document)


@pytest.mark.parametrize('case', ['single', 'zero_scatter', 'zero_power', 'duty', 'period'])
def test_degenerate_scenarios_remain_printable_without_inventing_uncertainty(case):
    data = deepcopy(summary())
    if case == 'single':
        data['valid_wake_phases'] = data['valid_wake_phases'][:1]
        data['valid_sleep_phases'] = data['valid_sleep_phases'][:1]
    elif case == 'zero_scatter':
        data['valid_wake_phases'][1] = {**data['valid_wake_phases'][0], 'sequence': 2}
        data['valid_sleep_phases'][1] = {**data['valid_sleep_phases'][0], 'following_wake_sequence': 2}
    elif case == 'zero_power':
        for phase in data['valid_wake_phases'] + data['valid_sleep_phases']:
            phase['energy_uwh'] = 0
    request = BatteryReportRequest(energy_wh=1, mode='period' if case == 'period' else 'duty' if case == 'duty' else 'sleep',
                                   value=100 if case == 'duty' else 10)
    pdf = render_report({'id': 'edge', 'name': 'Grenzfall'}, data, request, generated_at=CREATED)
    with pymupdf.open(stream=pdf, filetype='pdf') as document:
        assert len(document) == 2
        first = ' '.join(document[0].get_text().split())
        if case == 'single':
            assert 'Nicht schätzbar' in first
            assert 'Mindestens zwei gültige Zyklen' in first
        elif case == 'zero_power':
            assert 'Kein Verbrauch im Modell' in first
        elif case == 'zero_scatter':
            assert 'Keine beobachtete Streuung' in first
        assert_page_content_fits(document)


def test_pdf_exports_sleep_current_statistics_and_separate_device_runtime_scatter():
    from app.battery_report import report_data_many
    measurements = []
    for name, current in [('a', 10), ('b', 20)]:
        data = deepcopy(summary())
        for w in data['valid_wake_phases']:
            w.update(duration_s=1, energy_uwh=1)
        for s in data['valid_sleep_phases']:
            s['energy_uwh'] = current * data['voltage_v'] * s['duration_s'] / 3600
        measurements.append({'id': name, 'name': f'Gerät {name}', 'cycle_energy': data})
    request = BatteryMultiReportRequest(energy_wh=1, mode='period', value=10,
        sources=[{'measurement_id': m['id']} for m in measurements])
    result = report_data_many(measurements, request, generated_at=CREATED)
    row = next(row for row in result['statistics'] if row[0].startswith('Sleep-Strom'))
    assert row[1:] == ['15,000 µA', '10,000 µA', '20,000 µA', '7,071 µA', '50,000 µA²', '47,140 %']
    assert result['chart_estimate']['within_log_variance'] == 0
    assert result['chart_estimate']['between_log_variance'] > 0
    with pymupdf.open(stream=render_multi_report(measurements, request, generated_at=CREATED), filetype='pdf') as document:
        text = ' '.join(' '.join(page.get_text().split()) for page in document)
        for fragment in ['Sleep-Strom', '15,000 µA', '7,071 µA', '50,000 µA²', '47,140 %',
                         'Laufzeitstreuung zwischen Geräten', 'Periodendauer', 'Wake-Beginn']:
            assert fragment in text
        assert 'Sleep-Energie' not in text
        assert len(document) == 2
        assert_page_content_fits(document)


def test_many_long_measurement_names_paginate_without_losing_sources():
    measurements = [{'id': f'source-{i:02d}',
                     'name': f'Sensor {i} mit ausführlicher Beschreibung der Betriebsbedingungen',
                     'cycle_energy': summary()} for i in range(35)]
    request = BatteryMultiReportRequest(energy_wh=1, mode='sleep', value=86400,
        sources=[{'measurement_id': m['id']} for m in measurements])
    pdf = render_multi_report(measurements, request, generated_at=CREATED)
    with pymupdf.open(stream=pdf, filetype='pdf') as document:
        assert len(document) > 2
        text = '\n'.join(page.get_text() for page in document)
        for measurement in measurements:
            assert measurement['id'] in text
        assert 'Gültigkeit und Grenzen' in text
        assert_page_content_fits(document)
