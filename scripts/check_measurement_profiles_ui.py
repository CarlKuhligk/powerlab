"""Exercise the profile form in Edge against the actual local API, without hardware."""
import json
import logging
import math
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import Settings
from app.db import Measurement
from app.main import create_app


def main():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    directory = Path(tempfile.mkdtemp(prefix='profiles-ui-', dir=ROOT / '.test-tools'))
    settings = Settings(_env_file=None, data_dir=directory,
                        database_url=f"sqlite:///{directory / 'profiles.db'}")
    with TestClient(create_app(settings)) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(locale='de-DE')
        errors, writes = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route_web_socket('**/ws/devices', lambda socket: socket.send(json.dumps({
            'ppk2': [{'port': 'COM42', 'device_id': 'TEST-PPK', 'busy': False}]})))
        page.route_web_socket('**/ws/live', lambda socket: socket.send(json.dumps({'running': [], 'scheduled': []})))

        def route(request):
            url = urlparse(request.request.url)
            if url.hostname == 'cdn.plot.ly':
                request.fulfill(body='window.Plotly={react:async(el)=>{el.on=()=>{};el.removeAllListeners=()=>{}},purge:()=>{}}',
                                content_type='application/javascript')
                return
            path = url.path
            if path == '/api/system':
                request.fulfill(json={'sample_rate_hz': 100000, 'sample_period_us': 10, 'data_dir': 'data'})
                return
            if path == '/api/live':
                request.fulfill(json={'running': [], 'scheduled': []})
                return
            if request.request.method == 'POST':
                writes.append((path, request.request.post_data_json))
            response = client.request(request.request.method, path,
                                      content=request.request.post_data,
                                      headers={'Content-Type': 'application/json'})
            request.fulfill(status=response.status_code, body=response.content,
                            content_type=response.headers.get('content-type', 'application/json'))

        page.route('**/*', route)
        metadata = dict(name='Sensor test', notes='Test build')
        custom_fields = [{'label': 'Projekt', 'value': 'Project A'}, {'label': 'Seriennummer', 'value': '000123'},
                         {'label': 'Raumtemperatur', 'value': '23 °C'},
                         {'label': '<b>Build</b>', 'value': '#panic("test")\nRev. C'}]
        for width, height in [(1280, 900), (390, 844)]:
            profile_name = f'Sensor · {width}'
            page.set_viewport_size({'width': width, 'height': height})
            page.goto('http://powerlab.test/')
            page.locator('#newMeasurementBtn').click()
            page.wait_for_function("!document.getElementById('measurementProfileSelect').disabled")
            for name in metadata:
                assert page.locator(f'#measurementForm [name="{name}"]').input_value() == '', name
            assert page.locator('#measurementFormCustomFields .custom-field-row').count() == 0
            assert '(optional)' not in page.locator('.measurement-basics').inner_text()
            assert page.locator('.measurement-basics [name]').count() == 2
            assert page.evaluate("""() => {
                const hardware=document.querySelector('.measurement-hardware');
                return hardware.nextElementSibling.classList.contains('event-settings') &&
                    hardware.contains(document.getElementById('portSelect')) &&
                    !document.querySelector('.measurement-basics').contains(document.getElementById('portSelect'));
            }""")
            for name, value in metadata.items():
                page.locator(f'#measurementForm [name="{name}"]').fill(value)
            # Add and remove a field, then create arbitrary labels and multiline values.
            add = page.locator('[data-add-measurement-field="measurementForm"]')
            add.click()
            page.locator('#measurementFormCustomFields button').click()
            assert page.locator('#measurementFormCustomFields .custom-field-row').count() == 0
            for field in custom_fields:
                add.click()
                row = page.locator('#measurementFormCustomFields .custom-field-row').last
                row.locator('[data-field-label]').fill(field['label'])
                row.locator('[data-field-value]').fill(field['value'])
            page.locator('[name="meter_mode"]').select_option('ampere')
            page.locator('[name="voltage_mv"]').fill('2800')
            page.locator('#detectionMode').select_option('spectral_compare')
            page.locator('[name="spectral_margin_db"]').fill('18')
            for field, value in [('sleepThresholdUa', '350nA'), ('wakeThresholdUa', '2,5mA'),
                                 ('sleepMinS', '5ms'), ('wakeMinMs', '250µs'),
                                 ('preTriggerMs', '25µs'), ('postTriggerMs', '100ms')]:
                page.locator(f'#{field}').fill(value)
                page.locator(f'#{field}').press('Tab')
            page.locator('#startMode').select_option('scheduled')
            page.locator('#scheduledStartAt').fill('2027-01-01T12:00')
            page.locator('#stopMode').select_option('wake_count')
            page.locator('#eventCount').fill('7')
            assert page.locator('#saveMeasurementProfile').count() == 0
            assert page.locator('#measurementForm .dialog-actions button').all_text_contents() == ['Abbrechen', 'Einstellungen speichern', 'Messung anlegen']
            before = client.get('/api/measurement-profiles').json()
            # Canceling the name dialog leaves the measurement form and profiles intact.
            page.locator('#measurementProfileSave').click()
            page.wait_for_selector('#measurementProfileDialog[open]')
            assert page.locator('#measurementProfileName').evaluate('el=>document.activeElement===el')
            page.locator('#measurementProfileName').fill('Discarded')
            page.locator('#measurementProfileDialog .dialog-actions [data-close-profile-dialog]').click()
            page.wait_for_selector('#measurementProfileDialog', state='hidden')
            assert page.locator('#measurementDialog').is_visible()
            assert client.get('/api/measurement-profiles').json() == before
            page.locator('#measurementProfileSave').click()
            page.locator('#measurementProfileName').fill('   ')
            page.locator('#measurementProfileConfirm').click()
            assert page.locator('#measurementProfileName').evaluate('el=>!el.checkValidity()')
            assert client.get('/api/measurement-profiles').json() == before
            page.locator('#measurementProfileName').fill(profile_name)
            page.locator('#measurementProfileName').press('Enter')
            page.wait_for_selector('#measurementProfileDialog', state='hidden')
            page.wait_for_function("document.getElementById('measurementProfileStatus').textContent.includes('gespeichert.')")
            assert page.locator('#measurementDialog').is_visible()
            assert client.get('/api/measurements').json() == []
            stored = next(p for p in client.get('/api/measurement-profiles').json() if p['name'] == profile_name)
            assert stored['settings']['custom_fields'] == custom_fields
            expected = dict(sleep_threshold_ua=.35, wake_threshold_ua=2500, sleep_min_s=.005,
                            wake_min_ms=.25, pre_trigger_ms=.025, post_trigger_ms=100)
            for key, value in expected.items():
                assert abs(stored['settings'][key] - value) < 1e-12, key
            # Loading in a new page proves the dropdown uses persistent server data.
            page.reload()
            page.locator('#newMeasurementBtn').click()
            page.wait_for_function("!document.getElementById('measurementProfileSelect').disabled")
            page.locator('#measurementProfileSelect').select_option(stored['id'])
            page.wait_for_function("document.getElementById('measurementProfileStatus').textContent.includes('übernommen.')")
            applied = page.evaluate('measurementPayload()')
            assert applied['custom_fields'] == custom_fields
            for key, value in {**metadata, **expected, 'meter_mode': 'ampere', 'voltage_mv': 2800,
                               'detection_mode': 'spectral_compare', 'spectral_margin_db': 18,
                               'start_mode': 'scheduled', 'stop_mode': 'wake_count', 'event_count': 7}.items():
                assert math.isclose(applied[key], value, abs_tol=1e-12) if isinstance(value, float) else applied[key] == value, (key, applied[key], value)
            assert page.locator('#scheduledStartAt').input_value().startswith('2027-01-01T12:00')
            # An absent saved device must not silently select the remaining device.
            page.evaluate("renderDevices({ppk2:[{port:'COM99',device_id:'OTHER'}]})")
            assert page.locator('#portSelect').input_value() == ''
            page.evaluate("renderDevices({ppk2:[{port:'COM42',device_id:'TEST-PPK'}]})")
            assert page.locator('#portSelect').input_value() == 'COM42'
            page.locator('#measurementDialog').evaluate('el=>el.scrollTop=0')
            page.screenshot(path=str(directory / f'profiles-{width}.png'))
            page.locator('#measurementName').fill('Next measurement')
            page.locator('#measurementProfileSave').click()
            page.locator('#measurementProfileName').fill(profile_name)
            page.locator('#measurementProfileConfirm').click()
            page.wait_for_selector('#measurementProfileSaveError', state='visible')
            assert 'existiert bereits' in page.locator('#measurementProfileSaveError').inner_text()
            assert client.get('/api/measurements').json() == []
            page.screenshot(path=str(directory / f'profile-name-dialog-{width}.png'))
            page.locator('#measurementProfileName').fill(profile_name + ' copy')
            page.locator('#measurementProfileConfirm').click()
            page.wait_for_selector('#measurementProfileDialog', state='hidden')
            assert client.get('/api/measurements').json() == []
            page.locator('#measurementSubmit').click()
            page.wait_for_selector('#measurementDialog', state='hidden')
            assert writes[-2][0] == '/api/measurement-profiles'
            assert writes[-1][0] == '/api/measurements'
            assert writes[-1][1]['name'] == 'Next measurement'
            assert writes[-1][1]['wake_threshold_ua'] == 2500
            assert writes[-1][1]['custom_fields'] == custom_fields
            assert 'Raumtemperatur: 23 °C' in page.locator('#sessionMeta').inner_text()
            # Pending-plan editing restores rows and sends renamed/removed fields.
            page.locator('#editScheduledBtn').click()
            assert page.locator('#measurementFormCustomFields .custom-field-row').count() == len(custom_fields)
            page.locator('#measurementFormCustomFields .custom-field-row').first.locator('[data-field-label]').fill('Testprojekt')
            page.locator('#measurementFormCustomFields .custom-field-row').last.locator('button').click()
            page.locator('#measurementSubmit').click()
            page.wait_for_selector('#measurementDialog', state='hidden')
            current = client.get('/api/measurements').json()[0]
            assert current['custom_fields'][0]['label'] == 'Testprojekt'
            assert len(current['custom_fields']) == len(custom_fields) - 1
            # Completed measurements expose the same custom context and editable rows.
            with client.app.state.db.session() as session:
                session.get(Measurement, current['id']).status = 'completed'
            page.evaluate('async id=>{await openMeasurement(id);await state.historyPlotPromise}', current['id'])
            page.wait_for_selector('#detailView:not(.hidden)')
            assert 'Testprojekt' in page.locator('#metadataList').inner_text()
            assert 'Raumtemperatur' in page.locator('#metadataList').inner_text()
            page.locator('#editMeasurement').click()
            assert page.locator('#editFormCustomFields .custom-field-row').count() == len(custom_fields) - 1
            page.locator('[data-add-measurement-field="editForm"]').click()
            row = page.locator('#editFormCustomFields .custom-field-row').last
            row.locator('[data-field-label]').fill('Zusatz <b>Feld</b>')
            row.locator('[data-field-value]').fill('Zeile 1\n<b>Wert</b>')
            page.locator('#editForm [type="submit"]').click()
            page.wait_for_selector('#editDialog', state='hidden')
            page.wait_for_function("document.getElementById('metadataList').textContent.includes('Zusatz <b>Feld</b>')")
            assert page.locator('#metadataList b').count() == 0
            finished = client.get(f"/api/measurements/{current['id']}").json()
            assert finished['custom_fields'][-1] == {'label': 'Zusatz <b>Feld</b>', 'value': 'Zeile 1\n<b>Wert</b>'}
            for measurement in client.get('/api/measurements').json():
                client.delete(f"/api/measurements/{measurement['id']}")
            # Reopening and choosing no profile restores blank metadata.
            page.locator('#newMeasurementBtn').click()
            page.wait_for_function("!document.getElementById('measurementProfileSelect').disabled")
            page.locator('#measurementProfileSelect').select_option(stored['id'])
            page.wait_for_function("document.getElementById('measurementProfileStatus').textContent.includes('übernommen.')")
            page.locator('#measurementProfileSelect').select_option('')
            page.wait_for_function("document.getElementById('measurementProfileStatus').textContent.startsWith('Ohne Messprofil:')")
            for name in metadata:
                assert page.locator(f'#measurementForm [name="{name}"]').input_value() == '', name
            assert page.locator('#measurementFormCustomFields .custom-field-row').count() == 0
            page.locator('#measurementForm .dialog-actions [data-close-dialog]').click()
            # Profiles from before user-defined fields retain their legacy context.
            legacy = client.post('/api/measurement-profiles', json={
                'name': f'Legacy {width}', 'settings': {'project': 'Old project', 'serial_number': '00099',
                                                       'custom_fields': [{'label': 'Build', 'value': 'v1'}]}}).json()
            page.locator('#newMeasurementBtn').click()
            page.wait_for_function("!document.getElementById('measurementProfileSelect').disabled")
            page.locator('#measurementProfileSelect').select_option(legacy['id'])
            page.wait_for_function("document.getElementById('measurementProfileStatus').textContent.includes('übernommen.')")
            payload = page.evaluate('measurementPayload()')
            assert payload['project'] == payload['serial_number'] == ''
            assert payload['custom_fields'] == [{'label': 'Projekt', 'value': 'Old project'},
                                                {'label': 'Seriennummer', 'value': '00099'},
                                                {'label': 'Build', 'value': 'v1'}]
            page.locator('#measurementForm .dialog-actions [data-close-dialog]').click()
        assert not errors, errors
        browser.close()
    print('Footer save button, profile name dialog, cancel, validation, duplicate-name retry and saving without measurement start; profile reuse and custom field editing passed on desktop and mobile.')


if __name__ == '__main__':
    main()
