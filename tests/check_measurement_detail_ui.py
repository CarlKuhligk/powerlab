"""Browser check: .venv/Scripts/python.exe tests/check_measurement_detail_ui.py.

Requires Playwright and Microsoft Edge; uses the same Plotly version as the UI.
"""
from pathlib import Path
import re
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.test-tools').mkdir(exist_ok=True)
plotly = ROOT / '.test-tools/plotly-ui-check.js'
if not plotly.exists():
    with urllib.request.urlopen('https://cdn.plot.ly/plotly-4.1.1.min.js', timeout=30) as response:
        plotly.write_bytes(response.read())

html = (ROOT / 'app/static/index.html').read_text(encoding='utf-8')
html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)
html = re.sub(r'<link\b[^>]*>', '', html)
app = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
app = re.sub(r'^init\(\);\s*$', '', app, flags=re.M)
app = app[:app.index('CurrentInputs.install();')]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='msedge')
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content(html)
    page.add_style_tag(path=str(ROOT / 'app/static/styles.css'))
    page.add_script_tag(path=str(plotly))
    page.add_script_tag(path=str(ROOT / 'app/static/wake-analysis.js'))
    page.add_script_tag(content=app)
    page.evaluate('''async () => {
      WakeAnalysisUI.install();
      const sleep={following_wake_sequence:1,start_s:0,duration_s:2,mean_ua:6.4,charge_uc:12.8,energy_uwh:.0117,interpolated_samples:0};
      const wake={sequence:1,start_s:2,duration_s:.4,mean_ua:1000,peak_ua:1500,charge_uc:400,energy_uwh:.367,interpolated_samples:0};
      const cycles={cycle_count:1,excluded_wake_count:0,average_wake_duration_s:.4,average_wake_current_ua:1000,average_wake_energy_uwh:.367,valid_wake_phases:[wake],valid_sleep_phases:[sleep]};
      const m={id:'browser-check',name:'Sleep / Wake UI',status:'completed',started_at:'2026-10-08T08:00:00Z',duration_s:5,voltage_mv:3300,sample_rate_hz:100000,
        cycle_energy:cycles,sleep_analysis:{recorded_segment_count:2,recorded_duration_s:4.6,average_current_ua:6.4,charge_uc:29.44,energy_uwh:.027,average_valid_duration_s:2,average_valid_energy_uwh:.0117,valid_phases:[sleep]},
        events:[{id:7,sequence:1,event_kind:'wake',trigger_sample:200000,trigger_at:'2026-10-08T08:00:02Z',duration_us:400000,mean_ua:1000,peak_ua:1500,charge_uc:400}],
        settings:{detection_result:{mode:'threshold',sleep_pattern:{ready:true,mean_ua:6.4,std_ua:1,low_ua:4,high_ua:10,period_s:1,pulse_count:3}}}};
      const overview={timeline:[{t_s:0,state:'sleep',value:0},{t_s:2,state:'wake',value:1},{t_s:2.4,state:'sleep',value:0},{t_s:5,state:'sleep',value:0}],
        state_markers:[{kind:'sleep_start',t_s:0},{kind:'wake_start',t_s:2},{kind:'sleep_start',t_s:2.4}],analysis:{timeline_duration_s:5},events:[]};
      state.currentMeasurement=m;state.currentOverview=overview;setView('detail');
      renderMeasurementDetail(m,overview);await state.historyPlotPromise;
    }''')
    assert not errors, errors
    assert page.locator('.wake-analysis-card h3').inner_text() == 'Wake-Auswertung'
    assert 'Hochrechnung' not in page.locator('.wake-analysis-card').inner_text()
    assert 'Sleep-Peaks' not in page.locator('#detailDetection').inner_text()
    assert page.locator('#detailSleepPhaseRows').count() == 0
    assert not page.locator('#eventProtocol').evaluate('(el) => el.open')
    page.locator('#eventProtocol summary').click()
    assert page.locator('#eventRows tr').count() == 3
    assert page.locator('#eventRows tr').nth(0).inner_text().startswith('Sleep #1')
    assert page.locator('#eventRows tr').nth(1).inner_text().startswith('Wake #1')
    page.locator('#eventProtocol summary').click()
    page.evaluate('window.scrollTo(0,0)')
    chart = page.locator('#historyOverviewChart')
    chart.scroll_into_view_if_needed()
    box = chart.bounding_box()
    page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
    range_before = chart.evaluate('(el) => el._fullLayout.xaxis.range.slice()')
    scroll_before = page.evaluate('window.scrollY')
    page.mouse.wheel(0,160)
    page.wait_for_timeout(350)
    assert page.evaluate('window.scrollY') > scroll_before, 'Wheel should scroll the page before activation'
    assert chart.evaluate('(el) => el._fullLayout.xaxis.range.slice()') == range_before
    chart.scroll_into_view_if_needed()
    box = chart.bounding_box()
    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
    assert chart.evaluate('(el) => document.activeElement === el')
    scroll_before = page.evaluate('window.scrollY')
    page.mouse.wheel(0,-180)
    page.wait_for_timeout(350)
    assert chart.evaluate('(el) => el._fullLayout.xaxis.range.slice()') != range_before, 'Wheel should zoom after activation'
    assert page.evaluate('window.scrollY') == scroll_before, 'Active zoom should not scroll the page'
    page.keyboard.press('Escape')
    assert not chart.evaluate('(el) => document.activeElement === el')
    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
    page.locator('#historyOverviewTitle').click()
    assert not chart.evaluate('(el) => document.activeElement === el')
    assert 'anklicken' in page.locator('#historyOverviewZoomHint').inner_text()
    page.set_viewport_size({'width':390,'height':844})
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(ROOT / '.test-tools/detail-cleanup-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile layout should fit viewport'
    page.set_viewport_size({'width':1440,'height':900})
    page.wait_for_timeout(350)
    page.screenshot(path=str(ROOT / '.test-tools/detail-cleanup-desktop.png'), full_page=True)
    ids=page.locator('[id]').evaluate_all('(els) => els.map(el=>el.id)')
    assert len(ids)==len(set(ids)), 'Duplicate element IDs'
    assert not errors, errors
    browser.close()
print('PASS: page scrolling, click-to-zoom, Escape/outside reset, collapsed protocol, phase order, wake card, mobile layout and unique IDs')
