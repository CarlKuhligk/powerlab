# Befund vom 6. Oktober 2026

Untersucht wurde `b3a6f7fb-aae4-4c12-8f57-7bef31bb0a47`
(Cloudmeter Sleep / Wake Test). SQLite wurde nur lesend geöffnet; vom großen
Parquet-Event wurden nur Footer und Blockstatistiken gelesen. Die ursprüngliche
Messung wurde anschließend vom Benutzer gelöscht.

## Gesicherte Beobachtungen

- SQLite und metadata.json: Status `failed`, kein gespeicherter Fehlergrund.
- Start: 2. Oktober 2026, 16:27:05 Uhr Europe/Berlin.
- Gespeicherter Abschluss: 6. Oktober 2026, 08:49:17 Uhr Europe/Berlin.
- Ein Wake-Event mit 6.204.647.000 gespeicherten Rohsamples und 62.047 Parquet-Blöcken.
- Dateigröße: 22.515.086.805 Bytes; Footer: 21.444.674 Bytes.
- Allein int64-Sampleindex, float32-Strom und uint8-Digitaldaten ergeben
  80.660.411.000 Bytes (80,7 GB / 75,1 GiB) nach dem vollständigen Dekodieren.
- Windows-Systemereignis 2004 am 6. Oktober 2026 um 08:51:14 Uhr meldet niedrigen
  virtuellen Speicher. `python.exe` (PID 27084) belegte 99.940.933.632 Bytes.

## Einordnung

Der bisherige Event-Endpunkt, die Erstellung der Live-Hüllkurve und der
Raw-CSV-Export luden das gesamte Event mit `pq.read_table` oder vollständiger
NPZ-Konkatenation, bevor sie Daten reduzierten. Deshalb schützte `max_points`
den RAM nicht. Das erklärt den gemeldeten Speicherüberlauf beim Öffnen des
mehrstündigen Events und passt zum Windows-Ereignis.

Die Ursache des ursprünglichen Aufzeichnungsabbruchs ist nicht nachweisbar:
Der ältere Code bewahrte seinen Fehlergrund nicht auf. Das Windows-Ereignis liegt
nach dem gespeicherten Abschluss und belegt keine Ursache für den ursprünglichen
Messabbruch. Die Samplemenge entspricht rund 17 Stunden 14 Minuten; der deutlich
spätere gespeicherte Abschluss ist kein Beweis für durchgehend empfangene Daten.

## Umgesetzte Absicherung

- Große Parquet-Vorschauen verwenden ausschließlich Footer-Blockstatistiken.
- Echte Rohdaten werden auf zwei Millionen Samples pro Lesevorgang begrenzt;
  Zeitfenster überspringen nicht passende Parquet-Blöcke vor dem Dekodieren.
- NPZ-Dekompressionsgröße, Footergröße und Parquet-Blockgröße sind begrenzt.
- Nur ein Raw-Lesevorgang läuft gleichzeitig pro RawStore.
- Raw-CSV ist vor dem Laden auf 250.000 Samples begrenzt.
- Rohdaten-Endpunkte arbeiten außerhalb der asynchronen WebSocket-Schleife.
- Die Oberfläche kennzeichnet die aggregierte Vorschau und deaktiviert dafür
  Glättung und digitale Spuren, statt angenäherte Daten als Rohsamples auszugeben.

Die Speichergrenzen und die Vorschau mit Metadaten für 6,2 Milliarden Samples
wurden durch Regressionstests geprüft. Ein abschließender Speichertest an der
Originaldatei war nach deren Löschung nicht mehr möglich. Die Abbruchüberwachung
mit persistiertem Fehlergrund aus der vorherigen Änderung bleibt aktiv.
