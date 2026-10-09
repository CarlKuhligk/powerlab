"""Browser regression check for editable measurement fields; no hardware needed."""
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(locale="de-DE")
        errors = []
        saved = {}
        writes = []
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
            elif path == "/api/measurement-profiles":
                request.fulfill(json=[])
            elif path == "/api/measurements":
                if request.request.method == "POST":
                    payload = request.request.post_data_json
                    writes.append(payload)
                    saved.update(payload, id="metadata-ui", status="scheduled", settings=payload,
                                 sample_rate_hz=100000)
                    request.fulfill(json=saved)
                else:
                    request.fulfill(json=[])
            elif path == "/api/measurements/metadata-ui/scheduled":
                payload = request.request.post_data_json
                writes.append(payload)
                saved.update(payload, settings=payload)
                request.fulfill(json=saved)
            elif path == "/api/measurements/metadata-ui":
                request.fulfill(json=saved)
            elif path == "/api/live":
                request.fulfill(json={"running": [], "scheduled": []})
            else:
                request.fulfill(body="", content_type="application/javascript")

        page.route("**/*", route)
        for width, height in [(1280, 900), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            page.goto("http://powerlab.test/")
            for _ in range(2):
                page.locator("#newMeasurementBtn").click()
                page.wait_for_selector("#measurementDialog[open]")
                for name in ["name", "notes"]:
                    field = page.locator(f"#measurementForm [name='{name}']")
                    assert field.input_value() == "", name
                    assert field.evaluate("el=>el.required") == (name == "name"), name
                for selector in ["#startAtLabel", "#durationLabel", "#endAtLabel", "#eventCountLabel"]:
                    assert not page.locator(selector).is_visible(), f"Inactive field visible: {selector}"
                for name in ["name", "notes"]:
                    field = page.locator(f"#measurementForm [name='{name}']")
                    field.fill("")
                    field.click()
                    field.press_sequentially("Test 123")
                    assert field.input_value() == "Test 123", name
                    assert field.evaluate("el=>document.activeElement===el"), name
                    page.evaluate("""() => {
                        renderDevices({ppk2:[{port:'MOCK',device_id:'TEST',busy:true}]});
                        renderLiveOverview({running:[],scheduled:[]});
                    }""")
                    field.press_sequentially(" weiter")
                    assert field.input_value() == "Test 123 weiter", name
                    assert field.evaluate("el=>document.activeElement===el"), name
                for field_id in ["sleepThresholdUa", "wakeThresholdUa", "sleepMinS", "wakeMinMs", "preTriggerMs", "postTriggerMs"]:
                    field = page.locator(f"#{field_id}")
                    field.fill("")
                    field.click()
                    field.press_sequentially("1,25")
                    assert field.input_value() == "1,25", field_id
                page.locator("#startMode").select_option("scheduled")
                assert page.locator("#scheduledStartAt").is_visible()
                assert page.locator("#scheduledStartAt").is_enabled()
                page.locator("#scheduledStartAt").fill("2027-01-01T12:00")
                for mode, field_id in [("duration", "durationValue"), ("end", "scheduledEndAt"), ("wake_count", "eventCount"), ("sleep_count", "eventCount")]:
                    page.locator("#stopMode").select_option(mode)
                    field = page.locator(f"#{field_id}")
                    assert field.is_visible() and field.is_enabled(), mode
                    field.fill("2027-01-01T13:00" if mode == "end" else "12")
                    for selector in ["#durationValue", "#scheduledEndAt", "#eventCount"]:
                        if selector != f"#{field_id}":
                            assert not page.locator(selector).is_visible(), (mode, selector)
                page.locator("#measurementForm .dialog-actions [data-close-dialog]").click()
            # Submit through the real form and restore the fields when editing a plan.
            page.locator("#newMeasurementBtn").click()
            page.locator("#measurementName").fill("Metadata test")
            values = {"Seriennummer": "0000123", "Firmware-Version": "v1.2.3", "Hardware-Version": "Rev. B"}
            for name, value in values.items():
                page.locator('[data-add-measurement-field="measurementForm"]').click()
                row = page.locator('#measurementFormCustomFields .custom-field-row').last
                row.locator('[data-field-label]').fill(name)
                row.locator('[data-field-value]').fill(value)
            page.locator("#startMode").select_option("scheduled")
            page.locator("#scheduledStartAt").fill("2027-01-01T12:00")
            page.locator("#measurementSubmit").click()
            page.wait_for_selector("#measurementDialog", state="hidden")
            page.wait_for_selector("#sessionView:not(.hidden)")
            assert writes[-1]['custom_fields'] == [{'label': name, 'value': value} for name, value in values.items()]
            assert "Seriennummer: 0000123" in page.locator("#sessionMeta").inner_text()
            page.locator("#editScheduledBtn").click()
            for index, (name, value) in enumerate(values.items()):
                row = page.locator('#measurementFormCustomFields .custom-field-row').nth(index)
                assert row.locator('[data-field-label]').input_value() == name
                assert row.locator('[data-field-value]').input_value() == value
            page.locator('#measurementFormCustomFields .custom-field-row').last.locator('[data-field-value]').fill('Rev. C')
            page.locator("#measurementSubmit").click()
            page.wait_for_selector("#measurementDialog", state="hidden")
            values['Hardware-Version'] = 'Rev. C'
            assert writes[-1]['custom_fields'] == [{'label': name, 'value': value} for name, value in values.items()]
        assert not errors, errors
        browser.close()
    print("Name/Notiz defaults, custom metadata submission/restoration, focus during device/live updates, scheduling visibility and reopening passed on desktop and mobile.")


if __name__ == "__main__":
    main()
