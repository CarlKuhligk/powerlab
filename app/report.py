"""Structured measurement reports compiled locally with Typst's Python binding."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


TEMPLATE = Path(__file__).parent / "templates" / "measurement.typ"


# Conversion factors relative to the units stored by the measurement API.
TIME_UNITS = ((1e-6, "µs"), (1e-3, "ms"), (1, "s"),
              (60, "min"), (3600, "h"), (86400, "d"))
DISPLAY_UNITS = {
    "µW": ((1e-3, "nW"), (1, "µW"), (1e3, "mW"), (1e6, "W")),
    "µA": ((1e-3, "nA"), (1, "µA"), (1e3, "mA"), (1e6, "A")),
    "µC": ((1e-3, "nC"), (1, "µC"), (1e3, "mC"), (1e6, "C"), (1e9, "kC")),
    "µWh": ((1e-3, "nWh"), (1, "µWh"), (1e3, "mWh"), (1e6, "Wh"), (1e9, "kWh")),
    "s": TIME_UNITS,
    "ms": tuple((factor * 1000, unit) for factor, unit in TIME_UNITS),
}
for base_unit in ('µA', 'µWh', 'µW', 's'):
    DISPLAY_UNITS[base_unit + '²'] = tuple((factor**2, unit + '²')
                                          for factor, unit in DISPLAY_UNITS[base_unit])


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


def date(value, timezone_name="Europe/Berlin", *, microseconds=False):
    if not value:
        return "—"
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(ZoneInfo(timezone_name))
    offset = local.strftime("%z")
    offset = offset[:3] + ":" + offset[3:]
    timestamp = local.strftime("%d.%m.%Y %H:%M:%S" + (".%f" if microseconds else ""))
    return f"{timestamp} {local.tzname()} (UTC{offset})"


def report_data(measurement: dict, overview: dict, *, timezone_name="Europe/Berlin") -> dict:
    m, a = measurement, overview["analysis"]
    settings, ppk = m.get("settings", {}), m.get("ppk2_config", {})
    sleep = a.get("average_sleep_current_ua")
    if sleep is None and settings.get("detection_mode") != "threshold":
        sleep = m.get("sleep_current_ua")
    sleep_data = m.get('sleep_analysis', {})
    variability = m.get('cycle_energy', a.get('confirmed_cycles', {})).get('wake_variability', {})
    return {
        "name": m["name"], "id": m["id"], "status": m["status"],
        "generated": date(datetime.now(timezone.utc), timezone_name),
        "timezone": timezone_name,
        "notes": m.get("notes") or "Keine Prüfbedingungen oder Anmerkungen hinterlegt.",
        "error": m.get("error") or "",
        "context": [[label, str(m[key])] for label, key in [
            ("Projekt", "project"), ("Prüfling", "device"), ("Seriennummer", "serial_number"),
            ("Firmware-Version", "firmware"), ("Hardware-Version", "hardware_version")]
            if m.get(key)] + [[field["label"], field["value"] or "—"]
                             for field in m.get("custom_fields", [])] + [
            ["Beginn", date(m.get("started_at"), timezone_name)],
            ["Ende", date(m.get("finished_at"), timezone_name)],
            ["Messdauer (Zeitstempel)", number(m.get("duration_s"), "s")],
            ["Messzeitachse einschließlich Datenlücken", number(a.get("timeline_duration_s"), "s")]],
        "setup": [
            ["Messgerät", "Nordic PPK2"], ["PPK2-ID", m.get("ppk2_id") or "—"],
            ["Port", m.get("port") or "—"], ["USB-Seriennummer", str(ppk.get("serial") or "—")],
            ["Betriebsart", {"source": "Source Meter", "ampere": "Ampere Meter"}.get(m["meter_mode"], m["meter_mode"])],
            ["Konfigurierte Spannung", number(m["voltage_mv"] / 1000, "V")],
            ["Abtastrate", number(m["sample_rate_hz"], "S/s", 0)],
            ["Erkennungsmodus", {"threshold": "Stromschwellen und Mindestdauer",
                                "spectral_compare": "Spektralvergleich (experimentell)"}
             .get(settings.get("detection_mode"), str(settings.get("detection_mode") or "—"))],
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
            ["Wake-Zeitanteil", number(a.get("wake_duty_cycle_pct"), "%")],
            ["Wake-Ereignisse", number(a.get("wake_count"), digits=0)],
            ["Vollständige Zyklen", number(a.get("cycle_count"), digits=0)],
            ["Gesamtladung", number(m.get("total_charge_uc"), "µC")],
            ["Energie (konfigurierte Spannung)", number(m.get("energy_uwh"), "µWh", 6)],
        ],
        "quality": [
            ["Erfasste Messwerte", number(m.get("total_samples"), digits=0)],
            ["Erkannte Messwertverluste", number(m.get("detected_lost_samples"), digits=0)],
            ["Datenabdeckung", number(m.get("data_coverage_pct") if m.get("total_samples") else None, "%", 6)],
        ],
        "sleep_results": [[label, number(sleep_data.get(key), unit, digits)] for label, key, unit, digits in [
            ('Gespeicherte Sleep-Abschnitte', 'recorded_segment_count', '', 0),
            ('Erfasste Sleep-Zeit', 'recorded_duration_s', 's', 6),
            ('Mittlerer Sleep-Strom · erfasste Daten', 'average_current_ua', 'µA', 6),
            ('Sleep-Ladung', 'charge_uc', 'µC', 6),
            ('Sleep-Energie (konfigurierte Spannung)', 'energy_uwh', 'µWh', 6),
            ('Hintergrundereignisse im Sleep', 'background_count', '', 0),
            ('Davon Hintergrundladung', 'background_charge_uc', 'µC', 6),
            ('Sleep-Phasen aus gültigen Zyklen', 'valid_phase_count', '', 0),
            ('Mittlere Sleep-Dauer · gültige Zyklen', 'average_valid_duration_s', 's', 6),
            ('Mittlere Sleep-Energie · gültige Zyklen', 'average_valid_energy_uwh', 'µWh', 6),
        ]],
        "sleep_phases": [[str(p['following_wake_sequence']), number(p['start_s'], 's', 6),
                          number(p['duration_s'], 's', 6), number(p['mean_ua'], 'µA', 6),
                          number(p['charge_uc'], 'µC', 6), number(p['energy_uwh'], 'µWh', 6),
                          'Ergänzt' if p['interpolated_samples'] else 'Vollständig']
                         for p in sleep_data.get('valid_phases', [])],
        "wake_variability_count": str(variability.get('count', 0)),
        "wake_variability": [[label, number(metric.get('mean'), unit, 6),
                              number(metric.get('stddev'), unit, 6),
                              number(metric.get('variance'), unit + '²', 9), number(metric.get('cv_pct'), '%')]
                             for key, label, unit in [('duration_s', 'Wake-Dauer', 's'),
                                                       ('current_ua', 'Strom je Wake-Phase', 'µA'),
                                                       ('energy_uwh', 'Wake-Energie', 'µWh')]
                             for metric in [variability.get(key, {})]],
        "events": [[str(e["sequence"]), "Hintergrund" if e.get("event_kind") == "background" else "Wake",
                    number(e["trigger_sample"] / m["sample_rate_hz"], "s", 6),
                    number(e["duration_us"] / 1000, "ms"), number(e["mean_ua"], "µA"),
                    number(e["peak_ua"], "µA"), number(e["charge_uc"], "µC")]
                   for e in sorted(m.get("events", []) + m.get("background_events", []), key=lambda e: e["sequence"])],
        "markers": [[number(x["sample_index"] / m["sample_rate_hz"], "s", 6), x["label"]]
                    for x in m.get("markers", [])],
        "config": json.dumps(ppk, ensure_ascii=False, indent=2),
    }


def render_report(measurement: dict, overview: dict, *, timezone_name="Europe/Berlin") -> bytes:
    import typst

    # User text remains JSON data; it is never interpreted as Typst source.
    return typst.compile(str(TEMPLATE), root=str(TEMPLATE.parent),
                         sys_inputs={"report": json.dumps(report_data(measurement, overview, timezone_name=timezone_name), ensure_ascii=False)})
