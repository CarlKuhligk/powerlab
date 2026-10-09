# Changelog

## Unreleased

- Removed the battery lifetime calculator, runtime projections, battery PDF exports, related assets, API schemas, validation tooling and the Matplotlib dependency. Measured Sleep/Wake statistics remain available.

- Measurement creation and editing offer optional DUT serial number, firmware version and hardware version. Values are stored with scheduled and immediate measurements and included in details, search, PDF protocols and JSON/bundle metadata. Existing SQLite databases gain the new fields automatically.

- Docker and PDF reports default to Europe/Berlin, configurable with TZ. Absolute PDF timestamps include the date-specific CET/CEST abbreviation and UTC offset, with automatic daylight-saving changes. Storage and API timestamps retain UTC.

- Storage now uses SQLite and Parquet exclusively; the optional time-series mirror, its service, configuration, dependency, scripts and status display have been removed.
- Active measurements now start with sample-weighted means and a subtle Min/Max band instead of joining alternating extrema. Mergeable sum/count/extrema summaries are computed before preview reduction and compacted in aligned time buckets. The chart budget follows its width; narrow zooms load available wake raw data, and returning to live follow restores the overview. Actual device gaps remain separate polygons and line segments.
- Wake raw files are written in bounded blocks during acquisition; open wakes no longer accumulate a full RAM copy or require bulk compression on stop. Device stop precedes file finalization, queued received samples are preserved, and stop runs off the async API event loop with visible pending status.
- Consumption totals use compensated float64 integration of all received samples before reduction, are saved during acquisition and on stop, and include calibration/sleep/wake/tails without double-counting overlapping raw windows. Regression tests compare streamed event waveforms/metrics, discarded sleep charge, missing samples, saved totals/energy and stop-buffer preservation.
- Running measurements retain a bounded Min/Max acquisition history independently of event persistence, preventing the beginning of long unfinished wakes from vanishing when the recent live buffer rolls over. History and live tail avoid overlapping mean traces, preserve actual sample gaps and endpoints, and reduce the live tail with Min/Max instead of skipping points.
- Wake raw-data loading status clears after Plotly rendering, and errors stay visible in a separate status panel. Mouse-following current tooltips use an opaque dark background; current axes select nA, µA, mA, or A automatically, including wake-event zoom.
- Measurements can specify a fixed sleep-current reference in µA, allowing acquisition to start during a wake without calibrating an active plateau as sleep. The reference is persisted and stays fixed throughout acquisition; leaving it empty retains automatic calibration.
- Hard-trigger wake detection also requires current above the learned sleep band, preventing repeated single-sample wakes when the sleep current itself exceeds the absolute hard-trigger setting. Regression tests cover a 20-mA sleep baseline, changing active plateaus, and genuine sleep returns across acquisition batch sizes.
- Wake closure checks every nested sleep-hold window throughout the minimum sleep gap and starts confirmation inside the sleep band. Short dips can no longer average away intervening elevated active phases and split a wake prematurely.
- Live history retains cached Min/Max wake waveforms beyond 30 seconds, renders sleep means over their actual boundaries, and breaks lines between independent segments instead of drawing artificial ramps through sparse wake summaries.
- A configurable minimum sleep gap (default 200 ms) groups pulses separated by short sleep pauses into one wake event. Longer gaps still separate events. Calibration retains its separate sleep-hold window; trailing confirmation is excluded from event duration.
- Adaptive wakes require a sustained positive rise on adjacent 1-ms means; the new minimum-rise setting defaults to 5 µA/ms and can be disabled with 0. Hard triggers retain immediate full-rate pulse detection.
- Robust sleep-current and noise estimates track live drift with a 0.5-s time constant; updates occur at least every 20 ms of received sleep data, even in large batches.
- Regression coverage includes noise bursts, negative spikes, slow drift, small rising wakes, single-sample hard triggers, and USB sample gaps.
- Sleep reference calibration uses the final sleep-hold window after startup settling, so startup offsets no longer keep subsequent wake events open indefinitely.
- Regression coverage checks two wakes separated by 15 seconds of sleep across multiple acquisition batch sizes, and preserves sustained active plateaus.

## 1.0.6

- Wake-event raw view now has its own dedicated Plotly container; the history overview can no longer overwrite it.
- Event-load failures stay visible in the event panel instead of silently returning to the overview.
- Wake-event table is full-width, has a sticky header, selected-row highlighting, event count, and period column.
- Live full-history rendering separates persisted/LOD history from the recent high-resolution live tail, preventing a false line from t=0 to the recent preview.
- Current open sleep segment is represented by a lightweight mean segment until the next persisted checkpoint.

## 1.0.5

- Abgeschlossene Messungen zeigen jetzt eine exakte Sleep/Wake-Zustands-Timeline statt einer über lange Zeiträume ungenauen Stromkurve.
- Neue Zyklusstatistik: mittlere Periodendauer (Wake→Wake), gewichteter Sleep-Strom, gewichteter Wake-Strom, mittlere Wake-Dauer und Wake-Duty-Cycle.
- Wake-Event-Ansicht wird über eine serialisierte Plotly-Render-Queue stabil gehalten und kann nicht mehr von einem älteren History-Render überschrieben werden.
- Plotly rendert die Messdetailseite erst nachdem die View sichtbar ist.
- Wake-Event-Tabelle zeigt zusätzlich den mittleren Wake-Strom.
- Live-Chart behält die progressive Level-of-Detail-Historie aus v1.0.4.
- 11 automatisierte Tests bestanden, inklusive Zustands-Timeline und Zyklusstatistik.

## 1.0.4

- Live-Chart zeigt im Modus **Live folgen** jetzt die komplette Messdauer von `t=0` bis zum aktuellen Sample statt nur eines kurzen Sliding Windows.
- Neues `/series`-API für eine begrenzte Level-of-Detail-Zeitreihe mit festem Plot-Punktbudget.
- Min/Max-basierte Verdichtung bewahrt kurze Stromspitzen, ohne den Browser mit 100 kS/s zu überlasten.
- Zoom/Pan deaktiviert Live-Follow und lädt nur den sichtbaren Zeitbereich neu.
- Bei Zoomfenstern bis 30 s werden gespeicherte 100-kS/s-Wake-Rohdaten automatisch eingeblendet und erneut auf das Plot-Budget verdichtet.
- Wake-Events bleiben als eigene Marker sichtbar.
- 10 automatisierte Tests bestanden, inklusive LOD-Gesamtansicht und Raw-Wake-Zoom.

## 1.0.3

- Sidebar-Icon entfernt; Branding zeigt nur noch `PowerLab` und die Versionsnummer.
- Favicon/PWA-App-Icon bleibt unverändert erhalten.

## 1.0.2

- Wake-Event-Detailansicht stabilisiert: die History-Ansicht überschreibt einen geöffneten Wake-Event-Plot nicht mehr.
- Ungültigen Plotly-Hovermodus in der Wake-Event-Ansicht korrigiert und Render-Fehler sichtbar gemacht.
- Wake-Events erhalten in der History eine eigene gelbe Marker-Spur und bleiben damit auch bei stark verdichteter Langzeit-History sichtbar und anklickbar.
- Zeitplanung im Dialog „Messung anlegen“ überarbeitet: Startzeit, Dauer und Endzeit bleiben sichtbar und werden abhängig vom gewählten Modus eindeutig aktiviert/deaktiviert.
- Browserseitige Validierung für geplanten Start, Dauer und Endzeit ergänzt.
- Dark-Theme-Darstellung von `datetime-local` verbessert; Kalender-/Picker-Icon ist deutlich sichtbar.
- Statische CSS-/JS-Dateien mit Versionsparameter versehen, damit Browser nach Updates nicht versehentlich alte UI-Dateien aus dem Cache verwenden.

## 1.0.1

- Live-Follow ist jetzt ein echter Toggle.
- Bei deaktiviertem Live-Follow zeigt der Chart die komplette bisherige Messung.
- Ältere Bereiche verwenden die adaptive History; die letzten Live-Daten werden höher aufgelöst ergänzt.
- Zoom und Pan bleiben bei laufender Messung erhalten, während neue Daten im Hintergrund weiterlaufen.
- Umschalten auf Live-Follow springt wieder auf das aktuelle Messfenster.

## 1.0.0

- Pilot-Status entfernt; Anwendung heißt jetzt PowerLab v1.0
- eigenes PowerLab-App-Icon und Favicons
- ausschließlich echte Nordic PPK2-Messungen
- mehrere PPK2 und mehrere parallele aktive Messungen
- Live-Seite zu kompakter Statusübersicht umgebaut
- eigene Live-Unterseite pro Messung mit Setup, KPIs und Chart
- geplante Messungen mit persistentem Scheduler
- Start sofort oder zu festem Zeitpunkt
- Stop manuell, nach Dauer oder zu festem Endzeitpunkt
- geplante Sessions können vor Start angepasst oder abgebrochen werden
- laufende/geplante Sessions verschwinden nach Abschluss aus Live und wechseln in Historie
- PPK2-ID und vollständiger Config-/Calibration-Snapshot pro Messung
- Source-Meter-Versorgung + Strommessung
- 100-kS/s-Reader mit Sample-Counter-Integritätsprüfung
- Plotly-Achsenbeschriftungen
- Live-Zoom bleibt bei Aktualisierungen erhalten
- Python 3.14 / Podman Windows-Workflow

## 0.2.3

- globale PPK2-Pill aus Header entfernt
