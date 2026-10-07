"""Browser verification of live overview/zoom, with synthetic data and no PPK2."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    requests = []
    errors = []
    sockets = []
    blocks = []
    for i in range(100):
        start = i * 1000 if i < 50 else (i + 10) * 1000
        mean = 4 if i < 50 else 80
        blocks.append({"sample_index": start, "end_sample": start + 999,
                       "t_s": start / 1000, "end_s": (start + 999) / 1000,
                       "current_ua": mean, "min_ua": mean - .5,
                       "max_ua": 1500 if i == 30 else mean + .5,
                       "sample_count": 1000, "kind": "mean", "line_key": "a" if i < 50 else "b"})
    overview = {"measurement_id": "mock", "summary_points": blocks, "points": blocks,
                "history_points": blocks, "live_points": [], "events": [], "state_markers": [],
                "display_mode": "summary", "latest_s": 110, "phase": "protocol", "point_count": 100}
    overview["state_markers"] = [{"kind": "sleep_start", "sample_index": 100000, "t_s": 100, "current_ua": 4}]
    overview["state_markers"].append({"kind": "fft_wake_start", "sample_index": 100500, "t_s": 100.5, "current_ua": 80})
    overview["spectrogram"] = {"frequencies_hz": [4, 8, 40, 100], "window_s": .25,
        "hop_s": .125, "history_s": 120, "reset": True,
        "frames": [{"sample_index": 100000 + i * 125, "end_sample": 100249 + i * 125,
                    "t_s": 100.1245 + i * .125, "psd_db": [-80, -70, -20, -60]} for i in range(8)]}
    snapshot = {"measurement_id": "mock", "running": True, "state": "SLEEP",
                "started_at": "2026-10-06T10:00:00Z", "sample_rate_hz": 1000,
                "current_ua": 4, "baseline_ua": 4, "threshold_ua": 10,
                "peak_current_ua": 1500, "wake_count": 0, "total_charge_uc": 440,
                "energy_uwh": 1}
    snapshot['spectral_comparison'] = {'state': 'WAKE', 'score_db': 16, 'wake_count': 1}
    def frame(series, reset=False):
        return json.dumps({"type": "live", "snapshot": snapshot, "series": series, "reset": reset})
    detail_points = [{"sample_index": i, "t_s": i / 1000, "current_ua": 100 if i == 30_500 else 4,
                      "kind": "raw", "line_key": "wake-1"} for i in range(30_000, 31_001)]
    detail = {**overview, "display_mode": "detail", "detail": "raw+wake+separate-live",
              "summary_points": [], "points": detail_points, "history_points": detail_points,
              "point_count": len(detail_points)}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900}, locale="de-DE")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route_web_socket("**/ws/devices", lambda ws: ws.send(json.dumps({"ppk2": []})))
        page.route_web_socket("**/ws/live", lambda ws: ws.send(json.dumps({"running": [], "scheduled": []})))
        def connect_session(ws):
            sockets.append(ws)
            ws.send(frame(overview, True))
        page.route_web_socket("**/ws/live/mock?*", connect_session)

        def route(request):
            url = urlparse(request.request.url)
            if url.hostname == "cdn.plot.ly":
                request.continue_()
            elif url.path == "/":
                request.fulfill(path=str(ROOT / "app/static/index.html"), content_type="text/html; charset=utf-8")
            elif url.path.startswith("/static/"):
                path = ROOT / "app" / url.path.lstrip("/")
                request.fulfill(path=str(path))
            elif url.path.endswith("/series"):
                query = parse_qs(url.query)
                requests.append(query)
                request.fulfill(json=detail if query.get("view") == ["detail"] else overview)
            elif url.path == "/api/system":
                request.fulfill(json={"sample_rate_hz": 100000, "sample_period_us": 10, "data_dir": "data"})
            elif url.path == "/api/measurements":
                request.fulfill(json=[])
            elif url.path == "/api/live":
                request.fulfill(json={"running": [], "scheduled": []})
            else:
                request.fulfill(json={})

        page.route("**/*", route)
        page.goto("http://powerlab.test/")
        page.wait_for_function("typeof Plotly !== 'undefined'")
        page.evaluate("""() => {
            setView('session');
            document.getElementById('sessionLiveArea').classList.remove('hidden');
            state.sessionMeasurement={id:'mock',status:'recording'};
            connectSessionLive('mock');
        }""")
        page.wait_for_function("state.liveLastSeries?.display_mode==='summary' && state.livePlotPending===0")
        page.wait_for_timeout(1200)
        assert requests == [], "Live follow must not fetch /series"
        traces = page.evaluate("document.getElementById('liveChart').data.map(t=>t.name)")
        assert traces == ["Min/Max-Bereich", "Mittlerer Strom", "Sleep / Wake"]
        assert not page.locator('#spectrogramPanel').is_visible()
        page.check('#spectrogramToggle')
        page.wait_for_function("document.getElementById('spectrogramChart').data?.[0]?.x.length===8")
        assert page.evaluate("document.getElementById('spectrogramChart').layout.yaxis.type") == 'log'
        assert page.evaluate("document.getElementById('spectrogramChart').layout.shapes.length") == 2
        assert page.evaluate("document.getElementById('spectrogramChart').layout.shapes[1].line.color") == '#f472b6'
        assert page.evaluate("document.getElementById('spectrogramChart').layout.shapes[1].line.dash") == 'dash'
        assert 'FFT: WAKE' in page.locator('#kpiBackground').inner_text()
        assert page.evaluate("document.getElementById('spectrogramChart').data[0].z[2]") == [-20] * 8
        page.locator('#spectrogramChart').screenshot(path=str(ROOT / 'data/live-spectrogram.png'))
        # The band consists of separate polygons around the two measured segments.
        assert page.evaluate("document.getElementById('liveChart').data[0].x.filter(x=>x===null).length") == 2
        assert page.evaluate("document.getElementById('liveChart').data[1].y.filter(y=>y!==null).every(y=>y<100)")
        output = ROOT / "data" / "live-summary-overview.png"
        page.locator("#liveChart").screenshot(path=str(output))
        new_block = {**blocks[-1], "sample_index": 110_000, "end_sample": 110_999,
                     "t_s": 110, "end_s": 110.999, "max_ua": 2000}
        delta = {**overview, "summary_points": [new_block], "latest_s": 110.999, "max_points": 500}
        delta['spectrogram'] = {**overview['spectrogram'], 'reset': False, 'frames': [
            {'sample_index': 101000, 'end_sample': 101249, 't_s': 101.1245, 'psd_db': [-80, -70, -10, -60]}]}
        sockets[-1].send(frame(delta))
        page.wait_for_function("state.liveLastSeries?.latest_s===110.999 && state.livePlotPending===0")
        assert page.evaluate("state.liveStreamSeries.summary_points.length") == 101
        page.wait_for_function("document.getElementById('spectrogramChart').data[0].x.length===9")
        assert requests == []
        page.wait_for_timeout(300)
        page.evaluate("Plotly.relayout('liveChart', {'xaxis.range':[30,31]})")
        page.wait_for_function("state.liveLastSeries?.display_mode==='detail' && state.livePlotPending===0")
        assert requests[-1]["view"] == ["detail"]
        assert requests[-1]["start_s"] == ["30"]
        assert page.evaluate("document.getElementById('liveChart').data[0].name") == "Historie"
        assert page.evaluate("Math.max(...document.getElementById('liveChart').data[0].y)") == 100
        # Data continues to accumulate while the zoomed view stays fixed.
        next_block = {**new_block, "sample_index": 111_000, "end_sample": 111_999,
                      "t_s": 111, "end_s": 111.999}
        sockets[-1].send(frame({**delta, "summary_points": [next_block], "latest_s": 111.999}))
        page.wait_for_function("state.liveStreamSeries.latest_s===111.999")
        assert page.evaluate("state.liveLastSeries.display_mode") == "detail"
        assert len(requests) == 1
        gap_frame={**delta, 'summary_points': [], 'spectrogram': {
            **delta['spectrogram'], 'frames': [{'sample_index': 105000, 'end_sample': 105249,
                't_s': 105.1245, 'psd_db': [-80, -70, -5, -60]}]}}
        sockets[-1].send(frame(gap_frame))
        page.wait_for_function("document.getElementById('spectrogramChart').data[0].x.length===11")
        assert page.evaluate("document.getElementById('spectrogramChart').data[0].z[0].filter(v=>v===null).length") == 1
        assert page.evaluate("state.liveFullRange") == [30, 31]
        page.uncheck('#spectrogramToggle')
        assert not page.locator('#spectrogramPanel').is_visible()
        page.check('#spectrogramToggle')
        # Reconnection resynchronizes the cache without changing the user's zoom.
        sockets[-1].close()
        page.wait_for_function("state.liveStreamSeries?.latest_s===110 && !state.liveFollow")
        assert len(sockets) == 2
        page.wait_for_function("document.getElementById('spectrogramChart').data[0].x.length===8")
        assert page.evaluate("state.liveFullRange") == [30, 31]
        # Resume following: the overview must return without stale detail traces.
        page.click("#liveFollowBtn")
        page.wait_for_function("state.liveLastSeries?.display_mode==='summary' && state.livePlotPending===0")
        assert len(requests) == 1, "Resume must use the stream cache"
        assert page.evaluate("document.getElementById('liveChart').data.length") == 3
        page.click('[data-live-scale="linear"]')
        page.wait_for_function("document.getElementById('liveChart').layout.yaxis.type==='linear'")
        assert len(requests) == 1
        # Client compaction preserves weighting and extrema over a long run.
        stats = page.evaluate("""() => {
          const points=Array.from({length:10000},(_,i)=>({sample_index:i*10,end_sample:i*10+9,
            t_s:i/100,end_s:(i*10+9)/1000,line_key:'a',sample_count:10,sum_ua:i===5000?1000:40,
            current_ua:i===5000?100:4,min_ua:3,max_ua:i===5000?12345:5}));
          const result=compactLiveSummaries(points,500);
          return {count:result.length,samples:result.reduce((s,p)=>s+p.sample_count,0),
            sum:result.reduce((s,p)=>s+p.sum_ua,0),peak:Math.max(...result.map(p=>p.max_ua))};
        }""")
        assert stats == {"count": stats["count"], "samples": 100_000, "sum": 400_960, "peak": 12345}
        assert stats["count"] <= 500
        assert errors == [], errors
        browser.close()
    print(f"Stream, no polling, gaps, zoom, reconnect, compaction and resume verified. Preview: {output}")


if __name__ == "__main__":
    main()
