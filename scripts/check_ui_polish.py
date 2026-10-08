"""Check all UI views with fixture data, without a server or PPK2 hardware.

Run: .venv/Scripts/python.exe scripts/check_ui_polish.py
Requires Playwright and Edge. Screenshots go to .test-tools/ui-polish/.
"""
from copy import deepcopy
import json
import mimetypes
from pathlib import Path
import sys
from urllib.parse import urlparse
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from app.cycle_energy import confirmed_cycle_energy
from test_cycle_energy import fixture

OUTPUT = ROOT / ".test-tools/ui-polish"
OUTPUT.mkdir(parents=True, exist_ok=True)
PLOTLY = ROOT / ".test-tools/plotly-ui-check.js"
if not PLOTLY.exists():
    with urllib.request.urlopen("https://cdn.plot.ly/plotly-4.1.1.min.js", timeout=30) as response:
        PLOTLY.write_bytes(response.read())

cycles = confirmed_cycle_energy(10, 3300, *fixture())
settings = dict(detection_mode="threshold", sleep_threshold_ua=8, wake_threshold_ua=100,
                sleep_min_s=1, wake_min_ms=20, pre_trigger_ms=1000, post_trigger_ms=1000)
settings["detection_result"] = dict(mode="threshold", **{k: settings[k] for k in
    ("sleep_threshold_ua", "wake_threshold_ua", "sleep_min_s", "wake_min_ms")})
measurement = dict(id="sensor-a", name="Umweltsensor · Sleep / Wake", project="Sensorplattform",
    device="Prototyp A", firmware="v2.4.0", status="completed", port="COM4", ppk2_id="PPK2-001",
    started_at="2026-10-08T08:00:00Z", created_at="2026-10-08T07:59:00Z", duration_s=5,
    voltage_mv=3300, sample_rate_hz=100000, sleep_current_ua=5.333, wake_count=2,
    total_charge_uc=1020, energy_uwh=.935, settings=settings, cycle_energy=cycles,
    notes="Prüfung des periodischen Messzyklus bei 3,3 V.",
    sleep_analysis=dict(recorded_segment_count=3, recorded_duration_s=4.4,
        average_current_ua=5.333, charge_uc=23.46, energy_uwh=.0215,
        average_valid_duration_s=1.5, average_valid_energy_uwh=.0073,
        valid_phases=cycles["valid_sleep_phases"]))
measurement["events"] = [dict(id=i + 7, sequence=i + 1, event_kind="wake", trigger_sample=int(p["start_s"] * 100000),
    trigger_at=f"2026-10-08T08:00:0{i + 1}Z", duration_us=int(p["duration_s"] * 1e6),
    mean_ua=p["mean_ua"], peak_ua=2500, charge_uc=p["charge_uc"], digital_mask_seen=1)
    for i, p in enumerate(cycles["valid_wake_phases"])]
second = deepcopy(measurement)
second.update(id="sensor-b", name="Umweltsensor · Vergleich", device="Prototyp B")
for phase in second["cycle_energy"]["valid_sleep_phases"]:
    phase["energy_uwh"] *= 1.5
running = {**measurement, "id": "running", "name": "Umweltsensor · Dauertest", "status": "recording"}
planned = {**measurement, "id": "planned", "name": "Langzeitmessung über Nacht", "status": "scheduled",
           "scheduled_start_at": "2027-01-01T18:00:00Z", "stop_mode": "duration", "requested_duration_s": 3600}
measurements = [measurement, second, running, planned]
overview = dict(timeline=[dict(t_s=t, state=s, value=v) for t, s, v in
    [(0, "sleep", 0), (1, "wake", 1), (1.2, "sleep", 0), (3.2, "wake", 1), (3.6, "sleep", 0), (5, "sleep", 0)]],
    state_markers=[dict(kind=k, t_s=t) for t, k in
        [(0, "sleep_start"), (1, "wake_start"), (1.2, "sleep_start"), (3.2, "wake_start"), (3.6, "sleep_start")]],
    analysis=dict(timeline_duration_s=5), events=measurement["events"])
event_data = dict(event=measurement["events"][0], t_us=[-100000, 0, 20000, 100000, 200000, 250000, 400000],
    current_ua=[6, 6, 1000, 1200, 6, 6, 6], digital=[0, 0, 1, 1, 0, 0, 0],
    state_markers=[dict(kind=k, t_us=t, current_ua=c) for t, k, c in
        [(0, "wake_start", 6), (20000, "wake_validated", 1000), (200000, "sleep_start", 6), (250000, "sleep_validated", 6)]])
live = dict(running=[{**running, "measurement_id": "running", "state": "SLEEP"}], scheduled=[planned])
snapshot = dict(measurement_id="running", running=True, state="SLEEP", started_at=running["started_at"],
    current_ua=5.3, baseline_ua=5.333, peak_current_ua=2500, wake_count=2, total_charge_uc=1020,
    energy_uwh=.935, sample_rate_hz=100000, total_samples=500000,
    detection={**settings, "timeline_samples": 500000})
series = dict(measurement_id="running", phase="sleep", latest_s=5, max_points=1000,
    events=measurement["events"], state_markers=overview["state_markers"],
    valid_wake_phases=cycles["valid_wake_phases"], points=[], summary_points=[
        dict(sample_index=i * 10000, end_sample=(i + 1) * 10000 - 1, t_s=i / 10,
             end_s=(i + 1) / 10, current_ua=5 if i < 10 or 12 <= i < 32 or i >= 36 else 1500,
             min_ua=4, max_ua=2500, sample_count=10000, line_key="fixture", kind="summary")
        for i in range(50)])


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(locale="de-DE")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route_web_socket("**/ws/devices", lambda ws: ws.send(json.dumps({"ppk2": [dict(port="COM4", device_id="PPK2-001")]})))
        page.route_web_socket("**/ws/live", lambda ws: ws.send(json.dumps(live)))
        page.route_web_socket("**/ws/live/*", lambda ws: ws.send(json.dumps(dict(reset=True, snapshot=snapshot, series=series))))

        def route(request):
            url = urlparse(request.request.url)
            path = url.path
            if url.hostname == "cdn.plot.ly":
                request.fulfill(path=str(PLOTLY), content_type="application/javascript")
            elif path == "/":
                request.fulfill(path=str(ROOT / "app/static/index.html"), content_type="text/html; charset=utf-8")
            elif path.startswith("/static/"):
                file = ROOT / "app" / path.lstrip("/")
                content_type = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
                request.fulfill(path=str(file), content_type=content_type)
            elif path == "/api/version":
                request.fulfill(json={"version": "1.0.6"})
            elif path == "/api/system":
                request.fulfill(json={"sample_rate_hz": 100000, "sample_period_us": 10})
            elif path == "/api/live":
                request.fulfill(json=live)
            elif path == "/api/measurements":
                request.fulfill(json=measurements)
            elif path.endswith("/cycle-energy"):
                request.fulfill(json=next(m["cycle_energy"] for m in measurements if m["id"] == path.split("/")[-2]))
            elif path.endswith("/overview"):
                request.fulfill(json=overview)
            elif "/events/" in path:
                request.fulfill(json=event_data)
            elif path.startswith("/api/measurements/"):
                request.fulfill(json=next(m for m in measurements if m["id"] == path.split("/")[-1]))
            else:
                request.fulfill(status=404, json={"detail": path})

        page.route("**/*", route)
        checks = 0

        def check(view, width, dialog=False):
            nonlocal checks
            page.wait_for_timeout(150)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (view, width, "page overflow")
            if dialog:
                assert page.locator("dialog[open]").evaluate("el => el.scrollWidth <= el.clientWidth"), (view, width, "dialog overflow")
            assert page.locator(".nav[aria-current=page]").count() == 1
            assert not errors, errors
            if width in (390, 1440):
                if not dialog:
                    page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(OUTPUT / f"{view}-{width}.png"), full_page=not dialog)
            checks += 1

        for width in (320, 390, 768, 1024, 1440, 1920):
            page.set_viewport_size(dict(width=width, height=1000 if width > 780 else 844))
            page.goto("http://powerlab.test/")
            page.wait_for_selector("#runningCards .session-card")
            if width == 320:
                page.keyboard.press("Tab")
                assert page.locator(".skip-link").evaluate("el => el === document.activeElement && getComputedStyle(el).clipPath === 'none'")
                page.keyboard.press("Enter")
                assert page.locator("#mainContent").evaluate("el => el === document.activeElement")
            check("active", width)
            page.locator("#runningCards .session-card").first.focus()
            page.keyboard.press("Enter")
            page.wait_for_function("state.view === 'session' && document.getElementById('liveChart').data?.length > 0")
            check("live", width)
            page.locator("#backToLive").click()
            page.locator("#scheduledCards .session-card").first.focus()
            page.keyboard.press("Space")
            page.wait_for_selector("#sessionPendingInfo:not(.hidden)")
            check("planned", width)
            page.locator(".nav[data-view=measurements]").click()
            page.wait_for_selector(".measurement-open")
            check("measurements", width)
            page.locator(".measurement-open").first.focus()
            page.keyboard.press("Enter")
            page.wait_for_function("state.view === 'detail' && document.getElementById('historyOverviewChart').data?.length > 0")
            check("detail", width)
            page.locator("#exportBtn").click()
            assert page.locator("#exportBtn").get_attribute("aria-expanded") == "true"
            page.keyboard.press("Escape")
            assert page.locator("#exportBtn").get_attribute("aria-expanded") == "false"
            assert page.locator("#exportBtn").evaluate("el => el === document.activeElement")
            page.locator("#editMeasurement").click()
            check("edit", width, dialog=True)
            page.keyboard.press("Escape")
            page.locator("#eventProtocol summary").click()
            page.locator("#eventRows .event-open").first.click()
            page.wait_for_function("document.getElementById('wakeEventChart').data?.length > 0")
            check("event", width)
            page.locator(".nav[data-view=battery]").click()
            page.wait_for_function("document.getElementById('batteryChart').data?.length > 0")
            page.locator('[data-battery-select="sensor-b"]').check()
            page.wait_for_function("document.getElementById('batterySelectionCount').textContent.startsWith('2 ')")
            check("battery", width)
            page.locator("#newMeasurementBtn").click()
            page.wait_for_selector("#measurementDialog[open]")
            check("new", width, dialog=True)
            page.locator("#measurementSubmit").scroll_into_view_if_needed()
            assert page.locator("#measurementSubmit").is_visible()
            page.keyboard.press("Escape")
            page.locator(".nav[data-view=live]").click()
            page.evaluate("renderLiveOverview({running:[],scheduled:[]})")
            check("empty", width)

        page.emulate_media(reduced_motion="reduce")
        assert page.locator("#newMeasurementBtn").evaluate("el => getComputedStyle(el).transitionDuration") == "0s"
        ids = page.locator("[id]").evaluate_all("els => els.map(el => el.id)")
        assert len(ids) == len(set(ids)), "Duplicate IDs"
        assert not errors, errors
        browser.close()
        print(f"PASS: {checks} view/viewport checks, keyboard navigation, export menu, dialog scrolling and reduced motion.")
        print(f"Screenshots: {OUTPUT}")


if __name__ == "__main__":
    main()
