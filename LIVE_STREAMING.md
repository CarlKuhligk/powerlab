# Live-Streaming: vertikale Slices

## Slice 1: Messwert-Eingang → WebSocket → Live-Kurve

`MeasurementManager._append_preview` berechnet weiterhin gewichtete
Messwertblöcke mit Summe, Sampleanzahl, Mittelwert und Min/Max. Nach einem neuen
Block benachrichtigt der Mess-Thread die verbundenen Betrachter. Jeder Betrachter
hält einen Cursor und genau eine ausstehende Benachrichtigung; Messwertpakete
werden nicht pro Browser aufgestaut.

`/ws/live/{measurement_id}?max_points=1500` überträgt beim Verbindungsaufbau die
gesamte verdichtete Historie, danach nur neue Blöcke. Das Punktbudget liegt
zwischen 500 und 4000. Nachrichten haben diese Form:

```json
{
  "type": "live",
  "reset": false,
  "snapshot": {"measurement_id": "…", "running": true},
  "series": {
    "measurement_id": "…",
    "summary_points": [],
    "latest_s": 1.25,
    "max_points": 1500,
    "display_mode": "summary",
    "phase": "protocol",
    "events": [],
    "state_markers": []
  }
}
```

`snapshot` enthält die Live-Kennzahlen. `summary_points` enthält Blöcke mit
Samplegrenzen, Zeitgrenzen, Sampleanzahl, Summe und Stromstatistik. Bei
`reset: true` ersetzt der Browser seine Stream-Historie einschließlich der
Ereignis- und Zustandsmarker; ansonsten ergänzt er die neuen Blöcke.
Die Darstellung wird auf maximal vier Aktualisierungen pro Sekunde begrenzt.
Ohne neue Daten oder Statusänderung wartet der Server auf eine Benachrichtigung.

## Slice 2: Stream → Historie → Zoom und Rückkehr

Der Browser ergänzt die Blöcke in einer begrenzten Übersicht und verdichtet
bei Bedarf zusammenhängende Blöcke. Mittelwerte werden aus Summe und
Sampleanzahl berechnet; Min/Max bleiben erhalten. Über fehlende Geräteticks
werden keine Linien gezogen. Bei mehr getrennten Abschnitten als verfügbaren
Darstellungsplätzen werden repräsentative isolierte Blöcke samt globalen
Extrema behalten.

Nur ein angeforderter Zoom-/Detailbereich wird über den bestehenden
`/api/measurements/{id}/series`-Endpunkt geladen. Währenddessen aktualisiert
der Stream die Übersicht im Hintergrund. `Live folgen` und der Wechsel
zwischen Log/Linear verwenden den vorhandenen Stream-Cache. Diagrammänderungen
werden serialisiert und schnelle Stream-Updates für langsame Darstellungen
zusammengefasst.

## Slice 3: Verbindungsabbruch und Messungslebenszyklus

Jede neue Verbindung erhält einen vollständigen Reset. Der Wechsel von der
Einschaltphase zur Protokollzeit löst ebenfalls einen Reset aus. Ändern sich
Ereignis-/Zustandsmarker oder überschneidet ein verdichteter Serverblock den
bereits übertragenen Cursor, wird die Übersicht neu synchronisiert.

Stop, Aufzeichnungsfehler und Abschluss benachrichtigen die Betrachter auch
ohne weitere Samples. Ein terminaler Frame enthält `snapshot.running: false`
und `series: null`; die Oberfläche öffnet das gespeicherte Messprotokoll.
Abgebrochene Verbindungen geben ihre Subscription und Hintergrundaufgaben frei.
Die Übersicht `/ws/live` erhält Änderungen von Messungen und Planungen über
dieselbe Benachrichtigungstechnik.

Die automatische Wiederverbindung erhält einen fixierten Zoom. Die
vollständige Aufzeichnung und die Berechnung der Energie erfolgen weiterhin
unabhängig von der Verdichtung für die Anzeige.

## Live-Spektrogramm: drei vertikale Slices

1. **Messdaten → Spektralanalyse:** `app/spectrogram.py` berechnet eine STFT
   direkt aus den Originalsamples, vor der Verdichtung für die Stromanzeige.
   Hann-Fenster von 250 ms, 50 % Überlappung, Abzug des Fenstermittelwerts.
   Die einseitige Leistungsdichte wird auf logarithmische Frequenzbänder
   gemittelt und als dB bezogen auf 1 µA²/Hz übertragen. Die DC-Komponente
   wird ausgelassen. Bei 100 kS/s beträgt der FFT-Binabstand 4 Hz; obere
   Frequenzen werden für die Darstellung in breiteren Bändern zusammengefasst.
2. **Analyse → WebSocket → begrenzter Cache:** Frames liegen höchstens
   120 Sekunden bzw. 960 Fenster im Speicher. Jede Subscription besitzt
   einen eigenen Spektralcursor. Neue Frames werden als Deltas übertragen;
   Wiederverbindung, Zeitursprungswechsel und veraltete Cursor synchronisieren
   den Cache neu. Datenlücken verwerfen unvollständige Fenster, damit keine
   FFT über fehlende Samples berechnet wird. Beim ersten bestätigten Sleep
   beginnt die Spektralanalyse mit dem neuen Protokoll-Zeitursprung neu.
3. **Stream → zuschaltbare Live-Ansicht:** Unter der Stromkurve aktiviert
   „Anzeigen“ die Heatmap mit logarithmischer Frequenzachse und fester
   Farbskala von −100 bis +60 dB. Sleep-/Wake-Marker stammen aus der bestehenden
   Erkennung. Das Spektrogramm folgt stets den letzten 120 Sekunden, auch wenn
   die Stromkurve auf einen Zoom fixiert ist. Messlücken bleiben leer.

Die Analyse läuft unabhängig davon, ob die Anzeige eingeblendet ist, damit
beim Einschalten sofort die jüngste Historie verfügbar ist. Die Daten werden
nicht dauerhaft gespeichert. Die FFT beeinflusst keine Zustandserkennung.
250-ms-Fenster eignen sich nicht zur Auflösung langsamer Zyklen, etwa Pulse
alle zehn Sekunden; dafür wäre eine weitere Analyse mit längeren Fenstern nötig.

Verifikation: `tests/test_spectrogram.py` prüft Frequenz, integrierte
Signalstärke, DC-Unterdrückung, USB-Chunkgrenzen, Lücken, Speichergrenzen und
Stream-Cursor. `scripts/check_live_summary_ui.py` prüft außerdem den Schalter,
Heatmap, Marker, Spektral-Deltas, Lücken während fixiertem Zoom und Reconnect.

## Verifikation

- `tests/test_live_stream.py`: initiale Synchronisierung, Deltas, mehrere
  Betrachter, Leerlauf, langsame Betrachter, Messlücken, Zeitwechsel,
  WebSocket-Übertragung, Abschluss und Freigabe der Subscriptions.
- `scripts/check_live_summary_ui.py`: Browserprüfung mit Plotly für den
  tatsächlichen WebSocket-Datenweg, ausbleibende HTTP-Abfragen im Live-Modus,
  Messlücken, Zoom, Stream während fixierter Ansicht, Wiederverbindung,
  Rückkehr, Skalenwechsel und gewichtete Verdichtung.
- Bestehende Tests für Live-LOD, Min/Max-Verdichtung und Aufzeichnungsfehler.
