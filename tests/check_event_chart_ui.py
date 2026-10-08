"""Browser check: .venv/Scripts/python.exe tests/check_event_chart_ui.py."""
from pathlib import Path
import re
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.test-tools'
ARTIFACTS.mkdir(exist_ok=True)
plotly = ARTIFACTS / 'plotly-ui-check.js'
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
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content(html)
    page.add_style_tag(path=str(ROOT / 'app/static/styles.css'))
    page.add_script_tag(path=str(plotly))
    page.add_script_tag(content=app)
    page.evaluate('''async () => {
      const event={id:7,sequence:1,event_kind:'wake',trigger_sample:200000,
        trigger_at:'2026-10-08T08:00:02Z',duration_us:400000,
        mean_ua:1000,peak_ua:1500,charge_uc:400,digital_mask_seen:1};
      const m={id:'event-check',events:[event],voltage_mv:3300,sample_rate_hz:100000,
        background_events:[{...event,id:8,sequence:2,event_kind:'background',trigger_sample:100000}],duration_s:5};
      state.currentMeasurement=m;setView('detail');
      renderEventProtocol(m,{state_markers:[{kind:'sleep_start',t_s:0},{kind:'wake_start',t_s:2},{kind:'sleep_start',t_s:2.4}]});
      document.getElementById('eventProtocol').open=true;
      window.eventFixture={event,t_us:[-100000,0,20000,100000,200000,250000,400000],
        current_ua:[6,6,1000,1200,6,6,6],digital:[0,0,1,1,0,0,0],
        state_markers:[{kind:'wake_start',t_us:0,current_ua:6},
          {kind:'wake_validated',t_us:20000,current_ua:1000},
          {kind:'sleep_start',t_us:200000,current_ua:6},
          {kind:'sleep_validated',t_us:250000,current_ua:6}]};
      await renderEventChart(eventFixture);
    }''')

    # Sleep, Wake and background use the same typography and no badge surface.
    labels = page.locator('#eventRows .event-phase')
    assert labels.count() == 4
    styles = labels.evaluate_all('''els => els.map(el => {
      const s=getComputedStyle(el);
      return {color:s.color,font:s.font,padding:s.padding,border:s.borderWidth,background:s.backgroundColor};
    })''')
    assert {s['font'] for s in styles} == {styles[0]['font']}
    assert [s['color'] for s in styles] == ['rgb(96, 165, 250)', 'rgb(174, 189, 205)', 'rgb(245, 189, 91)', 'rgb(96, 165, 250)']
    assert all(s['padding'] == '0px' and s['border'] == '0px' and s['background'] == 'rgba(0, 0, 0, 0)' for s in styles)
    page.locator('#eventRows button').first.focus()
    assert page.locator('#eventRows button').first.evaluate("el => getComputedStyle(el).outlineStyle") == 'solid'

    chart = page.locator('#wakeEventChart')
    expected = ['Current', 'D0', 'Wake-Start', 'Wake bestätigt', 'Sleep-Start', 'Sleep bestätigt']
    assert chart.locator('.legendtext').all_text_contents() == expected
    rings = chart.locator('.scatterlayer .trace').filter(has=page.locator('.points path.point'))
    assert rings.count() == 4
    for trace_index in [1, 3]:
        ring = rings.nth(trace_index).locator('.points path.point')
        assert ring.evaluate("el => getComputedStyle(el).fill") == 'none'
        assert ring.evaluate("el => getComputedStyle(el).strokeWidth") == '2px'
        assert ring.evaluate("el => el.getAttribute('d').includes('A')"), 'Ring must use vector arcs'
    assert chart.locator('canvas').count() == 0

    # Each marker still gives a phase tooltip after splitting the legend entries.
    chart.scroll_into_view_if_needed()
    for index, label in enumerate(expected[2:]):
        point = chart.evaluate('''(el, i) => {
          const m=eventFixture.state_markers[i], l=el._fullLayout, box=el.getBoundingClientRect();
          return {x:box.x+l.xaxis._offset+l.xaxis.d2p(m.t_us)+(m.kind.endsWith('validated')?4:0),
            y:box.y+l.yaxis._offset+l.yaxis.d2p(m.current_ua*.001)};
        }''', index)
        page.mouse.move(point['x'], point['y'])
        page.wait_for_timeout(150)
        tooltip = page.locator('#currentChartTooltip').inner_text()
        assert label in tooltip, (label, tooltip, point)
    chart.screenshot(path=str(ARTIFACTS / 'event-chart-desktop.png'))

    page.locator('[data-history-scale="linear"]').click()
    page.wait_for_function("document.getElementById('wakeEventChart')._fullLayout.yaxis.type === 'linear' && state.eventPlotPending === 0")
    assert chart.locator('.legendtext').all_text_contents() == expected

    # Maximum supported point budget: exercise a noisy waveform, not just a flat line.
    render_ms = page.evaluate('''async () => {
      const count=50000,t_us=Array.from({length:count},(_,i)=>i*10-100000);
      const current_ua=t_us.map((t,i)=>t>=0&&t<200000?1000+100*Math.sin(i*1.7):6+.2*Math.sin(i));
      const started=performance.now();
      await renderEventChart({...eventFixture,t_us,current_ua,digital:t_us.map(t=>t>=0&&t<200000?1:0)});
      return Math.round(performance.now()-started);
    }''')
    assert chart.locator('.legendtext').all_text_contents() == expected
    assert chart.evaluate('el => el.data[0].x.length') == 50000

    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate("Plotly.Plots.resize(document.getElementById('wakeEventChart'))")
    page.wait_for_timeout(350)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    chart_box = chart.bounding_box()
    legend_box = chart.locator('.legend').bounding_box()
    assert legend_box['x'] >= chart_box['x'] and legend_box['y'] >= chart_box['y']
    assert legend_box['x'] + legend_box['width'] <= chart_box['x'] + chart_box['width'] + 1
    assert legend_box['y'] + legend_box['height'] <= chart_box['y'] + chart_box['height'] + 1
    chart.screenshot(path=str(ARTIFACTS / 'event-chart-mobile.png'))
    assert not errors, errors
    browser.close()

print(f'PASS: phase text, complete legend, SVG rings, marker hover, both scales, mobile layout; 50,000 points rendered in {render_ms} ms')
