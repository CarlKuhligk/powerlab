"""Structured measurement reports compiled locally with Typst's Python binding."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path


TEMPLATE = Path(__file__).parent / "templates" / "measurement.typ"


# Conversion factors relative to the units stored by the measurement API.
TIME_UNITS = ((1e-6, "µs"), (1e-3, "ms"), (1, "s"),
              (60, "min"), (3600, "h"), (86400, "d"))
DISPLAY_UNITS = {
    "µA": ((1e-3, "nA"), (1, "µA"), (1e3, "mA"), (1e6, "A")),
    "µC": ((1e-3, "nC"), (1, "µC"), (1e3, "mC"), (1e6, "C"), (1e9, "kC")),
    "µWh": ((1e-3, "nWh"), (1, "µWh"), (1e3, "mWh"), (1e6, "Wh"), (1e9, "kWh")),
    "s": TIME_UNITS,
    "ms": tuple((factor * 1000, unit) for factor, unit in TIME_UNITS),
}


def number(value, unit="", digits=3):
    if value is None or not math.isfinite(float(value)):
        return "—"
    value = float(value)
    if value and unit in DISPLAY_UNITS:
        choices = DISPLAY_UNITS[unit]
        factor, display_unit = choices[0]
        for candidate_factor, candidate_unit in choices[1:]:
            # Promote values that would round up to the next unit as well.
            if round(abs(value) / factor, digits) < candidate_factor / factor:
                break
            factor, display_unit = candidate_factor, candidate_unit
        value, unit = value / factor, display_unit
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",") + (f" {unit}" if unit else "")


def date(value):
    if not value:
        return "—"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC")


def report_data(measurement: dict, overview: dict) -> dict:
    m, a = measurement, overview["analysis"]
    settings, ppk = m.get("settings", {}), m.get("ppk2_config", {})
    sleep = a.get("average_sleep_current_ua")
    if sleep is None and settings.get("detection_mode") != "threshold":
        sleep = m.get("sleep_current_ua")
    return {
        "name": m["name"], "id": m["id"], "status": m["status"],
        "generated": date(datetime.now(timezone.utc).isoformat()),
        "notes": m.get("notes") or "Keine Notizen hinterlegt.",
        "error": m.get("error") or "",
        "context": [[label, str(m.get(key) or "—")] for label, key in [
            ("Projekt", "project"), ("Prüfling", "device"), ("Firmware", "firmware")]] + [
            ["Beginn", date(m.get("started_at"))], ["Ende", date(m.get("finished_at"))],
            ["Messdauer (Zeitstempel)", number(m.get("duration_s"), "s")],
            ["Zeitachse (Samples inkl. Lücken)", number(a.get("timeline_duration_s"), "s")]],
        "setup": [
            ["Messgerät", "Nordic PPK2"], ["PPK2-ID", m.get("ppk2_id") or "—"],
            ["Port", m.get("port") or "—"], ["USB-Seriennummer", str(ppk.get("serial") or "—")],
            ["Betriebsart", {"source": "Source Meter", "ampere": "Ampere Meter"}.get(m["meter_mode"], m["meter_mode"])],
            ["Konfigurierte Spannung", number(m["voltage_mv"] / 1000, "V")],
            ["Abtastrate", number(m["sample_rate_hz"], "S/s", 0)],
            ["Erkennungsmodus", str(settings.get("detection_mode") or "—")],
        ] + [[label, number(settings.get(key), unit)] for label, key, unit in [
            ("Sleep-Schwelle", "sleep_threshold_ua", "µA"), ("Sleep-Mindestdauer", "sleep_min_s", "s"),
            ("Wake-Schwelle", "wake_threshold_ua", "µA"), ("Wake-Mindestdauer", "wake_min_ms", "ms"),
            ("Vorlauf", "pre_trigger_ms", "ms"), ("Nachlauf", "post_trigger_ms", "ms")]],
        "results": [
            ["Mittlerer Gesamtstrom", number(m.get("average_current_ua"), "µA")],
            ["Spitzenstrom", number(m.get("peak_current_ua"), "µA")],
            ["Mittlerer Sleep-Strom", number(sleep, "µA")],
            ["Mittlerer Wake-Strom", number(a.get("average_wake_current_ua"), "µA")],
            ["Mittlere Periodendauer", number(a.get("average_period_s"), "s")],
            ["Mittlere Wake-Dauer", number(a.get("average_wake_duration_s"), "s", 6)],
            ["Wake-Duty-Cycle", number(a.get("wake_duty_cycle_pct"), "%")],
            ["Wake-Ereignisse", number(a.get("wake_count"), digits=0)],
            ["Vollständige Zyklen", number(a.get("cycle_count"), digits=0)],
            ["Gesamtladung", number(m.get("total_charge_uc"), "µC")],
            ["Energie (konfigurierte Spannung)", number(m.get("energy_uwh"), "µWh", 6)],
        ],
        "quality": [
            ["Erfasste Samples", number(m.get("total_samples"), digits=0)],
            ["Erkannte verlorene Samples", number(m.get("detected_lost_samples"), digits=0)],
            ["Datenabdeckung", number(m.get("data_coverage_pct") if m.get("total_samples") else None, "%", 6)],
        ],
        "events": [[str(e["sequence"]), "Hintergrund" if e.get("event_kind") == "background" else "Wake",
                    number(e["trigger_sample"] / m["sample_rate_hz"], "s", 6),
                    number(e["duration_us"] / 1000, "ms"), number(e["mean_ua"], "µA"),
                    number(e["peak_ua"], "µA"), number(e["charge_uc"], "µC")]
                   for e in sorted(m.get("events", []) + m.get("background_events", []), key=lambda e: e["sequence"])],
        "markers": [[number(x["sample_index"] / m["sample_rate_hz"], "s", 6), x["label"]]
                    for x in m.get("markers", [])],
        "config": json.dumps(ppk, ensure_ascii=False, indent=2),
    }


def render_report(measurement: dict, overview: dict) -> bytes:
    import typst

    # User text remains JSON data; it is never interpreted as Typst source.
    return typst.compile(str(TEMPLATE), root=str(TEMPLATE.parent),
                         sys_inputs={"report": json.dumps(report_data(measurement, overview), ensure_ascii=False)})
