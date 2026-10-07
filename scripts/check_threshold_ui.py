"""Optional browser check: installed Playwright and local Edge, no hardware needed."""
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1100}, locale="de-DE")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route_web_socket("**/ws/devices", lambda ws: ws.send(json.dumps({"ppk2": [{"port": "MOCK", "device_id": "TEST"}]})))
        page.route_web_socket("**/ws/live", lambda ws: ws.send(json.dumps({"running": [], "scheduled": []})))

        def route(request):
            path = urlparse(request.request.url).path
            if path == "/":
                request.fulfill(path=str(ROOT / "app/static/index.html"), content_type="text/html; charset=utf-8")
            elif path.startswith("/static/"):
                request.fulfill(path=str(ROOT / "app" / path.lstrip("/")), content_type="application/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
            elif path == "/api/system":
                request.fulfill(json={"sample_rate_hz": 100000, "sample_period_us": 10, "data_dir": "data"})
            elif path == "/api/measurements":
                request.fulfill(json=[])
            elif path == "/api/live":
                request.fulfill(json={"running": [], "scheduled": []})
            else:
                request.fulfill(body="", content_type="application/javascript")

        page.route("**/*", route)
        page.goto("http://powerlab.test/")
        page.click("#newMeasurementBtn")
        page.wait_for_selector("#measurementDialog[open]")
        page.wait_for_function("document.getElementById('portSelect').value==='MOCK'")
        assert page.locator("#detectionMode option").count() == 2
        assert page.locator("#detectionMode").input_value() == "threshold"
        assert page.locator("#automaticSettings, #legacySettings").count() == 0
        assert page.locator("#preTriggerMs, #postTriggerMs").count() == 2
        payload = page.evaluate("measurementPayload()")
        assert payload["detection_mode"] == "threshold"
        assert payload["wake_threshold_ua"] == 10000
        assert page.locator(".detection-group").first.get_attribute("aria-labelledby") == "wakeGroupTitle"
        for field, key, base_factor in [("wakeMinMs", "wake_min_ms", 1000), ("sleepMinS", "sleep_min_s", 1), ("preTriggerMs", "pre_trigger_ms", 1000), ("postTriggerMs", "post_trigger_ms", 1000)]:
            assert page.locator(f"#{field}Unit option").all_text_contents() == ["ns", "µs", "ms", "s"]
            for text, unit, seconds in [(" 1s", "s", 1), ("20us", "µs", 20e-6), ("20µs", "µs", 20e-6), ("20μs", "µs", 20e-6), ("1,5 ms", "ms", .0015), ("500ns", "ns", 5e-7)]:
                page.locator(f"#{field}").fill(text)
                assert page.locator(f"#{field}Unit").input_value() == unit
                assert page.locator(f"#{field}").evaluate("el=>el.checkValidity()")
                assert abs(page.evaluate("measurementPayload()")[key] - seconds * base_factor) < 1e-12
            page.locator(f"#{field}").fill("1s")
            page.locator(f"#{field}Unit").select_option("ms")
            assert page.locator(f"#{field}").input_value() == "1000"
            assert page.evaluate("measurementPayload()")[key] == base_factor
            page.locator(f"#{field}").fill("invalid")
            assert not page.locator(f"#{field}").evaluate("el=>el.checkValidity()")
        page.locator("#preTriggerMs").fill("6s")
        assert not page.locator("#preTriggerMs").evaluate("el=>el.checkValidity()")
        page.locator("#sleepMinS").fill("61s")
        assert not page.locator("#sleepMinS").evaluate("el=>el.checkValidity()")
        page.locator("#wakeMinMs").fill("0ns")
        assert not page.locator("#wakeMinMs").evaluate("el=>el.checkValidity()")
        page.evaluate("closeMeasurementDialog(); openMeasurementDialog()")
        assert page.evaluate("measurementPayload()")["pre_trigger_ms"] == 1000
        assert page.locator("#sleepMinSUnit").input_value() == "s"
        assert "background_detection" not in payload and "startup_confirm_s" not in payload
        assert "Wake wird oberhalb" not in page.locator("#measurementForm").inner_text()
        assert "Blau hinterlegt" not in page.locator("#thresholdPreview").text_content()
        assert "20 ms über Schwelle" in page.locator("#wakeValidationLegend").inner_text()
        page.locator("#wakeMinMs").fill("35")
        page.locator("#preTriggerMs").fill("250")
        assert "35 ms über Schwelle" in page.locator("#wakeValidationLegend").inner_text()
        assert "250 ms vor Wake-Beginn" in page.locator("#preTriggerLegend").inner_text()
        page.locator("button[aria-describedby='wakeDetectionInfo']").focus()
        assert page.locator("#wakeDetectionInfo").is_visible()
        page.locator("#wakeMinMs").fill("20")
        page.locator("#preTriggerMs").fill("1000")
        page.locator("#wakeThresholdUaUnit").select_option("µA")
        page.locator("#wakeThresholdUa").fill("4")
        assert not page.locator("#wakeThresholdUa").evaluate("el=>el.checkValidity()")
        page.locator("#wakeThresholdUa").fill("10000")
        assert page.locator("#wakeThresholdUa").evaluate("el=>el.checkValidity()")
        page.evaluate("closeMeasurementDialog(); openMeasurementDialog({id:'old',name:'Old plan',port:'MOCK',meter_mode:'source',voltage_mv:3300,settings:{detection_mode:'automatic'},scheduled_start_at:'2027-01-01T12:00:00Z'})")
        assert page.locator("#detectionMode").input_value() == "threshold"
        assert page.evaluate("measurementPayload()")["detection_mode"] == "threshold"
        page.select_option('#detectionMode', 'spectral_compare')
        assert page.locator('#spectralSettings').is_visible()
        page.locator('[name="spectral_margin_db"]').fill('15')
        assert page.evaluate('measurementPayload()')['spectral_margin_db'] == 15
        page.evaluate("closeMeasurementDialog(); openMeasurementDialog({id:'fft',name:'FFT plan',port:'MOCK',meter_mode:'source',voltage_mv:3300,settings:{detection_mode:'spectral_compare',spectral_margin_db:15},scheduled_start_at:'2027-01-01T12:00:00Z'})")
        assert page.locator('#detectionMode').input_value() == 'spectral_compare'
        assert page.locator('[name="spectral_margin_db"]').input_value() == '15'
        assert page.locator('#spectralSettings').is_visible()
        page.locator("#wakeMinMs").fill("1s")
        page.evaluate("closeMeasurementDialog(); openMeasurementDialog({id:'units',name:'Units plan',port:'MOCK',settings:{sleep_min_s:0.00002,wake_min_ms:0.02,pre_trigger_ms:250,post_trigger_ms:500}})")
        edited = page.evaluate("measurementPayload()")
        assert edited["sleep_min_s"] == .00002 and edited["wake_min_ms"] == .02
        assert edited["pre_trigger_ms"] == 250 and edited["post_trigger_ms"] == 500
        page.locator("#wakeMinMs").fill("20ms")
        page.locator("#sleepMinS").fill("5s")
        page.locator("#preTriggerMs").fill("1s")
        page.locator("#postTriggerMs").fill("1s")
        page.locator("#measurementDialog").screenshot(path=str(ROOT / "data/measurement-dialog-threshold.png"))
        page.locator("#postTriggerMs").scroll_into_view_if_needed()
        page.locator(".event-settings").screenshot(path=str(ROOT / "data/measurement-dialog-detection.png"))
        page.set_viewport_size({"width": 390, "height": 1000})
        assert page.locator("#measurementForm").evaluate("el=>el.scrollWidth<=el.clientWidth+1")
        page.locator("#postTriggerMs").scroll_into_view_if_needed()
        page.locator(".event-settings").screenshot(path=str(ROOT / "data/measurement-dialog-detection-mobile.png"))
        assert not errors, errors
        browser.close()
        print("Threshold dialog, units, validation, dynamic legend, keyboard tooltip, old-plan editing and mobile layout passed.")


if __name__ == "__main__":
    main()
