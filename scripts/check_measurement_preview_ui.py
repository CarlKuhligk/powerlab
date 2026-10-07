"""Browser verification with real detector replay and synthetic USB samples."""
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.measurement_preview import PreviewEngine
from app.schemas import MeasurementPreviewRequest


def main():
    rng = np.random.default_rng(4)
    current = np.r_[4 + rng.normal(0, .01, 32000), 20 + rng.normal(0, 1, 2000), 4 + rng.normal(0, .01, 3000)].astype(np.float32)
    ticks = np.arange(len(current))
    digital = np.zeros(len(current), dtype=np.uint8)
    sockets, calls, errors, deferred = [], [], [], []
    store = {'engine': None, 'paused': False, 'delay': False, 'measurement': None, 'revision': 0}
    devices = {'ppk2': [{'port': 'MOCK', 'device_id': 'TEST-PPK', 'busy': False}]}
    def frame(reset=False):
        value = store['engine'].snapshot()
        value.update(preview_id='preview-mock', running=not store['paused'], paused=store['paused'], error=None)
        value['spectrogram']['reset'] = reset
        if not reset:
            value['spectrogram']['frames'] = []
        return json.dumps(value)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 1050}, locale='de-DE')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route_web_socket('**/ws/devices', lambda socket: socket.send(json.dumps(devices)))
        page.route_web_socket('**/ws/live', lambda socket: socket.send(json.dumps({'running': [], 'scheduled': []})))
        def connect(socket):
            sockets.append(socket)
            socket.send(frame(True))
        page.route_web_socket('**/ws/measurement-previews/*', connect)
        def route(request):
            url = urlparse(request.request.url)
            path, method = url.path, request.request.method
            if url.hostname == 'cdn.plot.ly':
                request.continue_()
            elif path == '/':
                request.fulfill(path=str(ROOT / 'app/static/index.html'), content_type='text/html; charset=utf-8')
            elif path.startswith('/static/'):
                request.fulfill(path=str(ROOT / 'app' / path.lstrip('/')))
            elif path == '/api/devices':
                request.fulfill(json=devices)
            elif path == '/api/system':
                request.fulfill(json={'sample_rate_hz': 1000, 'sample_period_us': 1000, 'data_dir': 'data'})
            elif path == '/api/measurement-previews' and method == 'POST':
                body = request.request.post_data_json
                calls.append(('preview-start', body))
                if store['delay']:
                    deferred.append(request)
                    return
                if store['engine']:
                    store['engine'].dispose()
                store['engine'] = PreviewEngine(MeasurementPreviewRequest.model_validate(body), 1000)
                store['engine'].append(ticks, current, digital)
                store['paused'] = False
                request.fulfill(json={'preview_id': 'preview-mock'})
            elif path == '/api/measurement-previews/preview-mock/pause':
                store['paused'] = True
                calls.append(('preview-pause', None))
                request.fulfill(json={'paused': True})
                sockets[-1].send(frame())
            elif path == '/api/measurement-previews/preview-mock' and method == 'PATCH':
                body = request.request.post_data_json
                calls.append(('preview-update', body))
                store['engine'].reconfigure(MeasurementPreviewRequest.model_validate(body))
                request.fulfill(json={'revision': store['engine'].revision})
                sockets[-1].send(frame(True))
            elif path == '/api/measurement-previews/preview-mock' and method == 'DELETE':
                calls.append(('preview-stop', None))
                request.fulfill(status=204)
            elif path == '/api/measurements' and method == 'POST':
                body = request.request.post_data_json
                calls.append(('measurement-save', body))
                store['measurement'] = {**body, 'id': 'scheduled-test', 'status': 'scheduled', 'sample_rate_hz': 1000, 'settings': body}
                request.fulfill(json=store['measurement'])
            elif path == '/api/measurements/scheduled-test':
                request.fulfill(json=store['measurement'])
            elif path == '/api/measurements':
                request.fulfill(json=[])
            elif path == '/api/live':
                request.fulfill(json={'running': [], 'scheduled': []})
            else:
                request.fulfill(json={})
        page.route('**/*', route)
        page.goto('http://powerlab.test/')
        page.wait_for_function("typeof Plotly!=='undefined' && state.devices.ppk2.length===1")
        page.evaluate('openMeasurementDialog()')
        page.locator('#wakeThresholdUa').fill('10µA')
        page.locator('#sleepMinS').fill('100ms')
        page.select_option('#detectionMode', 'spectral_compare')
        page.click('#measurementPreviewStart')
        page.wait_for_function("document.getElementById('measurementPreviewScore').data?.[0]?.x.length>0")
        assert store['engine'].snapshot()['spectral_comparison']['reference_s'] == 30
        assert not any(c[0] == 'measurement-save' for c in calls)
        assert page.evaluate("document.getElementById('measurementPreviewCurrent').data.at(-1).text.includes('Wake validiert')")
        # Set a wake threshold by clicking an arbitrary chart position in log scale.
        page.select_option('#measurementPreviewTool', 'wake')
        page.locator('#measurementPreviewCurrent').scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        position = page.evaluate("""() => {
          const el=document.getElementById('measurementPreviewCurrent'),r=el.getBoundingClientRect(),l=el._fullLayout;
          return {x:r.left+l.xaxis._offset+l.xaxis._length*.5,y:r.top+l.yaxis._offset+l.yaxis.d2p(25)};
        }""")
        page.mouse.click(position['x'], position['y'])
        page.wait_for_function('measurementPayload().wake_threshold_ua>24 && measurementPayload().wake_threshold_ua<26')
        page.wait_for_function("document.getElementById('measurementPreviewCurrent').layout.shapes[1].y0>24 && !document.getElementById('measurementPreviewCurrent').data.at(-1).text?.includes('Wake validiert') && document.getElementById('measurementPreviewStatus').textContent.startsWith('Live')")
        assert calls[-1][0] == 'preview-update'
        # Drag the actual Plotly threshold line back down to a qualifying value.
        page.select_option('#measurementPreviewTool', 'navigate')
        page.wait_for_timeout(300)
        position = page.evaluate("""() => {
          const el=document.getElementById('measurementPreviewCurrent'),r=el.getBoundingClientRect(),l=el._fullLayout;
          return {x:r.left+l.xaxis._offset+l.xaxis._length*.8,
            from:r.top+l.yaxis._offset+l.yaxis.d2p(measurementPayload().wake_threshold_ua),to:r.top+l.yaxis._offset+l.yaxis.d2p(12)};
        }""")
        page.mouse.move(position['x'], position['from']);page.mouse.down()
        page.mouse.move(position['x'], position['to'], steps=12);page.mouse.up()
        page.wait_for_function('measurementPayload().wake_threshold_ua>11 && measurementPayload().wake_threshold_ua<13')
        page.wait_for_function("document.getElementById('measurementPreviewCurrent').data.at(-1).text?.includes('Wake validiert')")
        # FFT threshold changes replay exactly the same captured samples.
        page.evaluate("Plotly.relayout('measurementPreviewScore',{'shapes[0].y0':60,'shapes[0].y1':60})")
        page.wait_for_function('measurementPayload().spectral_margin_db===60')
        page.wait_for_function("document.getElementById('measurementPreviewScore').layout.shapes[0].y0===60 && document.getElementById('measurementPreviewStatus').textContent.includes('FFT: SLEEP')")
        page.click('#measurementPreviewPause')
        page.wait_for_function("document.getElementById('measurementPreviewStatus').textContent.startsWith('Angehalten')")
        page.locator('#wakeThresholdUa').fill('18µA')
        page.wait_for_function("document.getElementById('measurementPreviewCurrent').layout.shapes[1].y0===18 && document.getElementById('measurementPreviewStatus').textContent.startsWith('Angehalten')")
        output = ROOT / 'data/measurement-preview-current.png'
        page.locator('#measurementPreviewCurrent').screenshot(path=str(output))
        page.locator('#measurementPreviewFft').screenshot(path=str(ROOT / 'data/measurement-preview-fft.png'))
        # Saving releases the preview before reserving the device for a real measurement.
        page.select_option('#startMode', 'scheduled')
        page.evaluate("document.getElementById('scheduledStartAt').value=localInput(new Date(Date.now()+3600000))")
        page.click('#measurementSubmit')
        page.wait_for_function("state.sessionMeasurement?.id==='scheduled-test'")
        saved = next(body for name, body in calls if name == 'measurement-save')
        assert saved['wake_threshold_ua'] == 18 and saved['spectral_margin_db'] == 60
        assert saved['detection_mode'] == 'spectral_compare' and saved['start_mode'] == 'scheduled'
        assert [name for name, _ in calls][-2:] == ['preview-stop', 'measurement-save']
        # Mobile rendering and close/discard do not create a measurement.
        page.set_viewport_size({'width': 390, 'height': 1000})
        page.evaluate('openMeasurementDialog()')
        page.click('#measurementPreviewStart')
        page.wait_for_function("document.getElementById('measurementDialog').classList.contains('with-preview')")
        page.wait_for_timeout(500)
        page.locator('#measurementDialog').screenshot(path=str(ROOT / 'data/measurement-preview-mobile.png'))
        assert page.evaluate("document.getElementById('measurementDialog').scrollWidth<=document.getElementById('measurementDialog').clientWidth")
        page.keyboard.press('Escape')
        page.wait_for_timeout(300)
        assert [name for name, _ in calls].count('measurement-save') == 1
        assert calls[-1][0] == 'preview-stop'
        # Closing while hardware startup is still pending releases the late session.
        store['delay'] = True
        page.evaluate('openMeasurementDialog()')
        page.click('#measurementPreviewStart')
        page.wait_for_timeout(100)
        page.keyboard.press('Escape')
        deferred[-1].fulfill(json={'preview_id': 'preview-mock'})
        page.wait_for_timeout(300)
        assert calls[-1][0] == 'preview-stop'
        assert errors == [], errors
        store['engine'].dispose()
        browser.close()
    print(f'Preview, real detector replay, click/drag tools, FFT tuning, pause, saved settings, cleanup and mobile layout passed. Preview: {output}')


if __name__ == '__main__':
    main()
