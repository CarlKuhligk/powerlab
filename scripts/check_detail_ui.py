"""Optional Edge/Playwright check of the detail layout, without measurement hardware."""
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT = {
    "id": "detail-ui", "name": "ULP · Sleep / Wake Test", "status": "completed",
    "project": "PowerLab", "device": "Prototype #1", "firmware": "v1.0.6",
    "duration_s": 125, "started_at": "2026-10-06T10:00:00Z",
    "voltage_mv": 3300, "sample_rate_hz": 100000, "meter_mode": "source",
    "total_charge_uc": 25000, "energy_uwh": 23, "ppk2_config": {},
    "settings": {"detection_mode": "threshold", "detection_result": {
        "mode": "threshold", "sleep_threshold_ua": 7, "wake_threshold_ua": 20000,
        "sleep_min_s": 5, "wake_min_ms": 20,
        "wake_sleep_reference": {"ready": True, "mean_ua": 4.392, "std_ua": .1884,
                                 "low_ua": 3.992, "high_ua": 4.828}}},
    "events": [{"id": 7, "sequence": 1, "trigger_at": "2026-10-06T10:00:30Z",
                "duration_us": 20000, "mean_ua": 23000, "peak_ua": 35000,
                "charge_uc": 460, "digital_mask_seen": 0}],
}
OVERVIEW = {
    "analysis": {"average_sleep_current_ua": 4.392, "average_wake_current_ua": 23000,
                 "average_period_s": 30, "average_wake_duration_s": .02,
                 "wake_count": 1, "wake_duty_cycle_pct": .016},
    "timeline": [{"t_s": 0, "value": 0, "state": "sleep"},
                 {"t_s": 30, "value": 1, "state": "wake"},
                 {"t_s": 30.02, "value": 0, "state": "sleep"},
                 {"t_s": 125, "value": 0, "state": "sleep"}],
    "events": MEASUREMENT["events"],
    "state_markers": [{"kind": kind, "t_s": t, "sample_index": int(t * 100000)}
                      for kind, t in [("sleep_start", 0), ("sleep_validated", 5),
                                      ("wake_start", 30), ("wake_validated", 30.02)]],
}
EVENT = {
    "event": MEASUREMENT["events"][0], "t_us": [-1000, 0, 10000, 20000, 30000],
    "current_ua": [4, 23000, 35000, 23000, 4], "downsampled": True,
    "state_markers": [{"kind": "wake_start", "t_us": 0, "current_ua": 23000},
                      {"kind": "sleep_start", "t_us": 30000, "current_ua": 4}],
    "display_notice": "Anzeige verdichtet: Min/Max-Auswahl mit originalen Sample-Zeiten. Zoomen lädt Rohsamples nach.",
}


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, locale="de-DE")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for endpoint, payload in [("devices", {"ppk2": []}), ("live", {"running": [], "scheduled": []})]:
            page.route_web_socket(f"**/ws/{endpoint}", lambda ws, payload=payload: ws.send(json.dumps(payload)))

        def route(request):
            url = urlparse(request.request.url)
            if url.hostname == "cdn.plot.ly":
                request.continue_()
            elif url.path == "/":
                request.fulfill(path=str(ROOT / "app/static/index.html"), content_type="text/html; charset=utf-8")
            elif url.path.startswith("/static/"):
                request.fulfill(path=str(ROOT / "app" / url.path.lstrip("/")))
            elif url.path.endswith("/overview"):
                request.fulfill(json=OVERVIEW)
            elif "/events/" in url.path:
                request.fulfill(json=EVENT)
            elif url.path == "/api/measurements/detail-ui":
                request.fulfill(json=MEASUREMENT)
            elif url.path == "/api/measurements":
                request.fulfill(json=[])
            elif url.path == "/api/live":
                request.fulfill(json={"running": [], "scheduled": []})
            else:
                request.fulfill(json={})

        page.route("**/*", route)
        page.goto("http://powerlab.test/")
        page.wait_for_function("typeof Plotly !== 'undefined'")
        page.evaluate("async () => {await openMeasurement('detail-ui'); await state.historyPlotPromise}")
        assert page.locator(".detection-summary").count() == 4
        assert page.locator("#detailDetection").inner_text().count("Noch nicht bestätigt") == 1
        assert page.locator("#detailSleep").inner_text() == "4.392 µA"
        assert page.locator("#detailStatus").text_content() == "Abgeschlossen"
        assert page.evaluate("document.getElementById('historyOverviewChart').data.every(t => !(t.customdata || []).some(v => String(v).includes('validiert')))")
        assert page.evaluate("document.getElementById('historyOverviewChart').data.every(t => !(t.customdata || []).some(v => String(v).includes('Wake Start')))")
        assert page.evaluate("document.getElementById('historyOverviewChart').data.find(t => t.name === 'Wake event').x.length") == 1
        assert page.evaluate("document.getElementById('historyOverviewChart').layout.shapes.length") == 2
        assert page.locator("#historyOverviewChart").bounding_box()["height"] == 260
        for width in [1440, 900, 390]:
            page.set_viewport_size({"width": width, "height": 1100})
            page.wait_for_timeout(250)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.screenshot(path=str(ROOT / f"data/measurement-detail-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.locator(".event-open").focus()
        page.keyboard.press("Enter")
        page.wait_for_function("state.historyMode === 'event' && state.eventPlotPending === 0")
        assert not page.locator("#wakeEventStatus").is_visible()
        assert page.locator("#wakeEventChart").bounding_box()["height"] == 360
        assert page.evaluate("document.getElementById('wakeEventChart').data.at(-1).type") == "scattergl"
        assert page.evaluate("document.getElementById('wakeEventChart').data.at(-1).text") == ["Wake-Start", "Sleep-Start"]
        assert page.evaluate("document.getElementById('wakeEventChart').layout.xaxis.range[1]") > 30000
        info = page.get_by_role("button", name="Information zur Datendarstellung")
        info.focus()
        assert page.locator("#eventDisplayHint").is_visible()
        assert "Anzeige verdichtet" in page.locator("#eventDisplayHint").inner_text()
        page.wait_for_timeout(180)
        page.screenshot(path=str(ROOT / "data/measurement-detail-event.png"), full_page=True)
        page.locator("#backToOverview").click()
        assert page.locator("#historyOverviewCard").is_visible()
        # Missing detection and malicious metadata stay safe when navigating to another measurement.
        page.evaluate("""async () => {
            const m = {...state.currentMeasurement, name: '<img src=x onerror=alert(1)>', settings: {}};
            renderMeasurementDetail(m, state.currentOverview); await state.historyPlotPromise;
        }""")
        assert not page.locator("#detailDetectionSection").is_visible()
        assert page.locator("#detailName img").count() == 0
        assert not errors, errors
        browser.close()
    print("Detail UI OK: metadata, desktop/mobile, markers, chart height, keyboard event selection and info tooltip")


if __name__ == "__main__":
    main()
