# Einfache Sleep/Wake-Erkennung

Neue Messungen bestätigen Zustände über „Stromschwellen und Mindestdauer“.
Der optionale [Spektralvergleich](SPECTRAL_DETECTION.md) ergänzt FFT-Markierungen.
Historische Messungen behalten ihre gespeicherten Ergebnisse. Planungen mit einem
entfernten Erkennungsmodus müssen vor dem Start explizit aktualisiert werden.

Vier Einstellungen bestimmen die Erkennung:

Stromfelder bieten einen Dropdown für nA, µA und mA. Eingaben wie `9mA`,
`9ma`, `500 nA` oder `9,5mA` übernehmen Zahl und Einheit automatisch.
API und gespeicherte Einstellungen bleiben in µA; beim Öffnen einer Planung
wird der Wert mit einer passenden Anzeigeeinheit wiederhergestellt.

* Sleep unter [µA], Vorauswahl 5 µA.
* Sleep-Mindestdauer [s], Vorauswahl 5 s.
* Wake über [µA], Vorauswahl 10.000 µA = 10 mA.
* Wake-Mindestdauer [ms], Vorauswahl 20 ms, frei einstellbar.

Sleep verlangt durchgehend Strom **unter** der Sleep-Schwelle, Wake durchgehend
Strom **über** der Wake-Schwelle. Gleichheit erfüllt die jeweilige Bedingung
nicht. Jede Unterbrechung oder Datenlücke startet die betreffende Validierung
neu. Zwischen beiden Schwellen bleibt der letzte bestätigte Zustand bestehen.
Bei 4–5 µA schwankendem Ruhestrom sollte die Sleep-Schwelle oberhalb des oberen
Ruhewerts liegen, beispielsweise 6 µA. 35-µA-Pulse überschreiten eine
10-mA-Wake-Schwelle nicht, unterbrechen aber eine laufende Sleep-Validierung.

Nach Einschalten sucht der Recorder zuerst den ersten bestätigten Sleep.
Bootwerte gehen nicht in die Protokollstatistik ein. Der Protokollstart und
alle späteren Events werden rückdatiert; die Validierung verschiebt den
Event-Zeitpunkt nicht. Ab Verlassen des Sleep-Bereichs wird die zusammenhängende
Aktivität vorläufig gespeichert. Zwischenstufen wie 2 → 8 → 5 → 11 mA erhalten
den Kandidaten; eine Rückkehr in den gelernten Ruhebereich für mindestens 1 ms
verwirft ihn. Kürzere ADC-/Messbereichs-Einbrüche erhalten die ursprüngliche
Flanke, unterbrechen aber die Wake-Bestätigungsdauer.
Nur die Bestätigungsdauer über der Wake-Schwelle startet bei Unterschreitung
dieser höheren Schwelle neu. Eine Datenlücke verwirft den Kandidaten ebenfalls.

Nach bestätigtem Sleep entsteht eine robuste Ruhe-Referenz aus bis zu 4096
Stichproben: Mittelwert, Standardabweichung, 1–99-%-Bereich und obere
Rauschgrenze (Median + sechs MAD-Streuungen, mindestens 2 % des Ruhestroms
und 0,001 µA). Mindestens 32 ms bzw. 32 Originalsamples sind erforderlich.
Die Referenz wird danach eingefroren, damit ein langsamer Wake-Anstieg nicht
seine eigene Sleep-Grenze anheben kann. Nach dem nächsten bestätigten Sleep
wird neu gelernt. Die eingestellten Schwellen und Mindestdauern bestätigen
weiterhin Sleep und Wake; die Referenz bestimmt den rückwirkenden Wake-Beginn.

Verlassene Ruhebereiche werden ab dem ersten abweichenden Sample auf der
Kandidatendatei festgehalten, auch unterhalb der eingestellten Sleep-Schwelle.
Erst eine spätere Wake-Bestätigung macht daraus ein Event. Mindestens drei
aufeinanderfolgende Abweichungen stützen dessen rückwirkenden Start; einzelne
ADC-Ausreißer werden dadurch nicht zum Startpunkt. Die erste abweichende
Position bleibt der Zeitstempel, nicht das dritte Sample.

Abgeschlossene Pulse, die mindestens 1 ms zum Ruhebereich zurückkehren,
lehren ihre Zeitposition, Dauer und einen Verlauf mit 64 Stützstellen.
Mindestens drei Pulse mit ähnlichem Abstand, Dauer und Amplitude sind nötig,
bevor sie als periodisch gelten. Die letzten acht Pulse genügen als begrenzter
Langzeitspeicher, auch wenn ein 10-s-Abstand länger als der Rohdatenring ist.
Nach Wake-Bestätigung wird das eingefrorene Modell mit den Originalsamples des
Kandidaten verglichen. Am erwarteten Peak-Zeitpunkt ist dessen gelernter Verlauf
mit kleinen Toleranzen zulässig. Sobald Stromhöhe, Verlauf oder Dauer dieses
Muster verlassen, beginnt der bestätigte Wake. Ein gleich hoher Peak außerhalb
der erwarteten Periode wird nicht automatisch ausgenommen.

Ohne ausreichende Ruhe-Referenz bleibt die bisherige Suche nach der ersten
steilen Flanke als Rückfall erhalten; deren Grenzen gelten nur in diesem Fall.
Ohne gelerntes Peak-Muster wird zusammenhängende Aktivität vor einem
bestätigten Wake ab der ersten Abweichung zugeordnet, auch bei geringem Strom.
Ein unbestätigter, bei Stop noch offener Kandidat lehrt kein Peak-Muster.
Datenlücken verwerfen Kandidat und Muster. Diagnose und JSON-Export enthalten
`sleep_pattern`, `wake_sleep_reference` und `wake_onset_method`.
Wenn Wake und Sleep-Peak anfänglich identisch aussehen, ist nur die erste
nachweisbare Abweichung zuordenbar, kein verborgener Firmware-Zustandswechsel.

Kandidaten samt Rohdaten liegen auf einer temporären Datei, mit begrenztem
Analyse- und Vorlaufpuffer im RAM. Dessen Kapazität ist unabhängig vom Export-
Vorlauf: Vorlauf + Sleep-Bestätigung + Wake-Bestätigung, mindestens 20 ms.
Mit 1000 ms Vorlauf, 5 s Sleep und 20 ms Wake sind das bei 100 kS/s 602.000
unveränderte Samples (6,02 s). Die verfügbaren Werte werden als
`analysis_buffer_samples` und `analysis_buffer_s` protokolliert. Datenlücken
begrenzen die Rückwärtssuche; Flanken davor werden nicht zugeordnet.
Beim Verwerfen gehen ihre Messwerte weiterhin
in Sleep-Statistik und Gesamtladung ein. Bei Bestätigung zählen die Werte ab
der ausgewählten Flanke zum Wake, frühere Kandidatenwerte weiterhin zum Sleep.
Die temporäre Datei wird nach Verwerfen, Bestätigung oder Stop geschlossen.
Wird ein bestätigter Wake bei Messende noch nicht durch Sleep beendet, bleibt
er bis zum letzten erfassten Sample offen.

Vorlauf und Nachlauf betragen bei neuen Schwellen-Messungen jeweils 1000 ms.
Sie beziehen sich auf die bestätigte Wake-Flanke und die bestätigte
Sleep-Flanke, nicht auf den späteren Validierungszeitpunkt. Eine fünfsekündige
Sleep-Validierung verlängert den Nachlauf daher nicht automatisch auf fünf
Sekunden. Bei Protokollbeginn oder Messende können Ausschnitte kürzer sein.
Überlappende Rohdatenausschnitte beeinflussen die Gesamtladung nicht.

Die Beispielgrafik im Formular reagiert auf alle sechs Einstellungen.
Gefüllte kleine Punkte und transparente Linien kennzeichnen bestätigte Flanken,
offene Punkte und gestrichelte Linien deren Validierung. Die Marker stehen in
Live-Chart, Zustands-History und Rohdaten-Chart zur Verfügung; Rohdaten-Charts
zeigen nur Marker innerhalb ihres aufgezeichneten Ausschnitts.

Die Event-Anzeige öffnet den gesamten Mitschnitt vom Vorlauf bis zum Nachlauf
als schnelle Min/Max-Übersicht. Das Punktebudget ist rund ein Hundertstel der
Samplezahl, auf Tausender gerundet (mindestens 1000, maximal 50.000 Punkte).
Das Feld „Punkte / Ansicht“ erlaubt eigene Werte zwischen 100 und 50.000.
Jeder Zeitabschnitt liefert seine Extremwerte mit originalen Sample-Zeiten;
Anfang und Ende bleiben erhalten. Event-Marker werden unabhängig davon mit
ihrer exakten Zeitposition angezeigt. Energie und Detektion nutzen weiterhin
die Originaldaten.

Zoomen und Verschieben laden den sichtbaren Bereich mit demselben Budget neu.
Passen dessen Samples in das Budget, werden alle Rohsamples angezeigt, ohne
Verdichtung. Andernfalls entstehen feinere Min/Max-Abschnitte. API:
`adaptive_view=true`, `start_s`, `end_s`, `max_points`. Große Bereiche werden
serverseitig in kleinen Blöcken gelesen; es gibt keine Voll-Event-Allokation.
Der Browser hält maximal acht zuletzt geladene Ansichten im Cache. Veraltete
Anfragen werden abgebrochen; Zurücksetzen stellt die volle Übersicht wieder her.
Der Chart kennzeichnet Übersicht und Rohsamples samt angezeigter Punktzahl.
`tests/test_event_memory_limits.py` und `scripts/check_threshold_ui.py` prüfen
Budget, Peak-Erhalt, Originalzeiten, Rohdaten-Zoom, Cache und Navigationsabbruch.
Die API garantiert mit `raw_only=true`, `start_s` und `end_s`, dass jedes
Sample des angefragten Fensters erhalten bleibt; zu große Anfragen werden
abgelehnt und nicht durch eine verdichtete Vorschau ersetzt. Die allgemeinen
API-Vorschauen bleiben für andere Aufrufer verfügbar und als `block-extrema`
oder `sample-extrema` gekennzeichnet. Datei-Kompression verändert keine Samples.

Eine Legende und Tooltips unterscheiden gefüllten Wake-/Sleep-Start und offene
Bestätigung. Vorlauf endet am Wake-Start, Nachlauf beginnt am Sleep-Start.

Die eingegebenen Bestätigungsgrenzen bleiben fest. Die interne Ruhe-Referenz
und Peak-Muster benötigen keine weiteren Eingaben und ordnen den Beginn eines
bereits bestätigten Wake zu. Vorhandene Messungen werden nicht neu klassifiziert;
die Änderung gilt für neue Aufzeichnungen.
`tests/test_sleep_pattern.py` prüft gekrümmte Anstiege, wiederkehrende Peaks,
Muster-Unterbrechung, Datenlücken, isolierte Ausreißer, Rohdaten und Energie.

API: `detection_mode="threshold"`, `sleep_threshold_ua`, `wake_threshold_ua`,
`sleep_min_s`, `wake_min_ms`, `pre_trigger_ms`, `post_trigger_ms`.
Die Wake-Schwelle muss größer als die Sleep-Schwelle sein. Bestätigungen werden
auf ganze Samples aufgerundet. Bestätigungszeiten sind auf 60 s begrenzt.

Prüfung: `tests/test_threshold.py` nutzt den eigenen Signalgenerator und
Mock-PPK, verschiedene USB-Batchgrößen, exakte Grenzen, kurze Fehltrigger,
Datenlücken, Stop sowie den vollständigen SQLite-/Rohdaten-Pfad.
`scripts/check_threshold_ui.py` prüft Formular, Einheiten, Schwellengrenzen und Planungen
im Headless-Browser. Hardwaretests bleiben erforderlich.

`tests/test_wake_onset.py` ergänzt Treppen, Sleep-Peaks, ADC-Ausreißer, Rampen,
lange Kandidaten, wiederholte Bestätigungsversuche und die rückwirkende Zuordnung
von Rohdaten, Ladung und Markern. Mit
`python scripts/generate_wake_onset_example.py` entstehen reproduzierbare CSV-,
JSON- und SVG-Beispieldateien unter `data/wake-onset-example/`.

Das Messprotokoll berechnet Wake- und Sleep-Energie nur aus vollständigen,
bestätigten Sleep → Wake → Sleep-Zyklen mit lückenloser Sample-Abdeckung.
Der zugeordnete Sleep liegt vor dem Wake; der bestätigte Rückkehr-Sleep
schließt den Zyklus. Die letzte offene Sleep-Phase und ein noch nicht durch
Sleep abgeschlossener Wake gehen nicht in Mittelwerte oder Hochrechnungen ein.
Vorlauf und Nachlauf werden nicht doppelt integriert. Hintergrundpulse zählen
zum Sleep-Verbrauch. Ohne vollständige Zyklen stehen Striche statt Nullwerten.

Wake- und Sleep-Energie werden je Zyklus gemittelt; Sleep-Strom wird nach
gemessener Zeit gewichtet. Energie nutzt die eingestellte Versorgungsspannung.
Die Hochrechnung wiederholt das beobachtete Verhältnis von Wake und Sleep für
Tag, Woche, Monat (30 Tage), Jahr (365 Tage) und eigene positive Zeitspannen.
Wake-, Sleep- und Gesamtenergie werden getrennt ausgewiesen. Die Auswertung
ist im Messprotokoll und im JSON-Export verfügbar.
API: `/api/measurements/{id}/cycle-energy`, optional `duration_s`.
`tests/test_cycle_energy.py` prüft Formeln, offene Phasen, fehlende Bestätigungen,
Datenlücken und den vollständigen Mock-PPK-Pfad mit dem eigenen Signalgenerator.
