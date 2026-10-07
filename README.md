# PowerLab v1.0.6

## Sleep/Wake-Erkennung

Neue Messungen bestätigen Zustände über **Stromschwellen und Mindestdauer**.
Sleep und Wake werden erst nach der jeweiligen Mindestdauer bestätigt.
Die bisherigen automatischen und adaptiven Modi wurden aus UI und Backend entfernt.
Im Dropdown steht außerdem der experimentelle [Spektralvergleich](SPECTRAL_DETECTION.md)
mit zusätzlichen FFT-Markierungen zur Verfügung. Ereignisse und Auto-Stop
verwenden dabei weiterhin die Stromschwellen.
Historische Messungen bleiben lesbar. Bestehende Planungen mit einem entfernten
Modus müssen über „Planung bearbeiten“ mit gültigen Schwellen neu gespeichert werden;
sie werden nicht stillschweigend auf eine andere Erkennung umgestellt.
Details: [THRESHOLD_DETECTION.md](THRESHOLD_DETECTION.md).

Beim Anlegen oder Bearbeiten einer Planung lässt sich eine
[Messvorschau](MEASUREMENT_PREVIEW.md) starten: Stromkurve und FFT ansehen,
Trigger per Klick oder Ziehen justieren und die Werte anschließend für
eine gestartete oder geplante Messung übernehmen.

PowerLab ist eine lokale Mess- und Analyseanwendung für **Nordic Power Profiler Kit II (PPK2)**. Sie richtet sich an Ultra-Low-Power-Embedded-Entwicklung und erfasst den PPK2-Datenstrom mit nominal **100 kS/s / 10 µs Sampleabstand**.

Die Anwendung arbeitet ausschließlich mit echten PPK2-Geräten. Ein Simulations-/Mock-Treiber ist nicht enthalten.

## History-Analyse in v1.0.6

Abgeschlossene Messungen verwenden eine kompakte Sleep/Wake-Zustands-Timeline. Wake-Marker öffnen weiterhin die exakten 100-kS/s-Rohdaten. Die Statistik berechnet mittlere Periodendauer, gewichteten Sleep- und Wake-Strom, mittlere Wake-Dauer und Wake-Duty-Cycle.

Im Wake-Event-Betrachter steht neben Log/Linear eine Glättung mit den Stufen Aus, Leicht, Mittel und Stark zur Verfügung. Ein adaptiver bilateraler Filter schätzt das Rauschen robust und gewichtet Nachbarpunkte nach Zeitabstand und Stromähnlichkeit, um deutliche Sprünge möglichst zu erhalten. Die Filterung erfolgt vor der logarithmischen Darstellung. Beim Überfahren der geglätteten Kurve werden Rohwert und geglätteter Wert angezeigt. Rohdaten, Kennwerte, digitale Signale und Exporte bleiben unverändert; kleine Signaländerungen im Bereich des Rauschens können durch die Glättung abgeschwächt werden.


## Live-Chart und Level of Detail

Die Live-Ansicht verwendet einen **ereignisgesteuerten WebSocket-Stream**. Neue
Messwertblöcke und Kennzahlen werden vom Server übertragen, sobald Messdaten
vorliegen, gebündelt auf maximal vier Aktualisierungen pro Sekunde. Im Modus
`Live folgen` gibt es keine wiederholten HTTP-Abfragen der Messkurve.
Die Übersicht aktiver und geplanter Messungen wird ebenfalls bei Änderungen
übertragen. Zoomdetails werden gezielt per HTTP geladen; beim Zurückschalten
auf `Live folgen` wird die laufend aktualisierte Stream-Historie verwendet.
Bei einer Wiederverbindung wird die Historie neu synchronisiert; ein fixierter
Zoom bleibt erhalten. Technische Details und die vertikalen Slices:
[LIVE_STREAMING.md](LIVE_STREAMING.md).

Der Live-Chart zeigt standardmäßig die **gesamte Messhistorie seit Messbeginn**. PowerLab sendet dabei niemals die vollständigen 100.000 Samples/s an den Browser. Stattdessen bleibt die Darstellung auf ein festes Punktbudget begrenzt und erhält lokale Minima/Maxima. Beim Hineinzoomen fordert die Weboberfläche nur den sichtbaren Zeitbereich erneut an. Für kurze Bereiche (bis 30 s) werden vorhandene Wake-Event-Rohdaten aus dem Event Store automatisch mit einbezogen; bei ausreichend engem Zoom sind dadurch wieder Daten bis zur nativen 10-µs-Zeitbasis sichtbar.

`Live folgen` bedeutet: Die X-Achse zeigt immer `0 … jetzt` und wächst nach rechts. Wird der Modus ausgeschaltet oder manuell gezoomt, bleibt der Ausschnitt fixiert, während die Messung im Hintergrund weiterläuft.

## Highlights

Im Zeitplaner stehen unter **Stop** auch **Nach Wake-Ereignissen** und
**Nach Sleep-Ereignissen** zur Verfügung. Es zählen nur bestätigte Zustandswechsel,
keine Kandidaten oder Sleep-Checkpoints. Nach beispielsweise fünf Wakes wartet
PowerLab auf den anschließend bestätigten Sleep und beendet die Messung rückwirkend
an dessen Start. Bei fünf Sleeps erfolgt der Stop entsprechend am nächsten
bestätigten Wake-Start; der erste bestätigte Sleep zählt mit. Die Bestätigungszeit
geht nicht in Messdauer oder Statistik ein. Die Anzahl bleibt bei geplanten
Messungen und beim Bearbeiten der Planung gespeichert.

- mehrere PPK2 gleichzeitig erkennen und parallel verwenden
- genau eine aktive Messung pro PPK2
- Source-Meter- und Ampere-Meter-Betrieb
- Source Meter: DUT-Versorgung und Strommessung gleichzeitig
- nominal 100 kS/s mit Sample-Counter-Prüfung
- bestätigte Sleep/Wake-Aufzeichnung mit Stromschwellen, Mindestdauer und Vor-/Nachlauf
- hochauflösende Wake-Events in Parquet/Zstd
- SQLite für Messungsverwaltung und Metadaten
- Plotly für Live- und History-Charts mit Zoom
- Zoom bleibt bei Live-Updates erhalten; `Live folgen` reaktiviert Auto-Range
- vollständiger PPK2-Konfigurations-/Kalibrier-Snapshot pro Messung
- Export als PDF-Messprotokoll, JSON, CSV und PowerLab-Bundle
- **Zeitplanung**: sofort oder fester Startzeitpunkt
- **Auto-Stop**: manuell, nach Dauer oder zu einem festen Endzeitpunkt
- geplante Messungen bleiben persistent in SQLite und werden nach einem Neustart wieder berücksichtigt

## UI-Konzept

### Live

`Live` ist die Statusübersicht. Dort erscheinen nur:

- laufende Messungen
- geplante / ausstehende Messungen
- Zuordnung zum jeweiligen PPK2
- Start- und Stopplan
- kompakter Status

Der große Messchart liegt bewusst **nicht** auf dieser Übersichtsseite.

Ein Klick auf eine laufende oder geplante Session öffnet deren eigene Unterseite.

### Live → Messungsseite

Die Session-Seite zeigt das konkrete Setup:

- PPK2-ID und COM-Port
- Source/Ampere Meter
- Versorgungsspannung
- Sample-Rate
- Trigger- und Pre-Trigger-Konfiguration
- Zeitplanung / Auto-Stop

Bei einer geplanten Messung kann die Planung bis zum Start bearbeitet oder abgebrochen werden.

Wenn die Session läuft, erscheinen zusätzlich:

- Live-Stromchart
- Current / Sleep Baseline / Peak
- Wake Events
- Charge / Energy
- Marker und Stop-Steuerung

Nach Abschluss verschwindet die Session aus `Live` und ist unter `Messungen` verfügbar.

Unter `Messungen` lassen sich mehrere Einträge über Checkboxen auswählen. Die Checkbox im Tabellenkopf wählt alle sichtbaren Suchtreffer aus; bereits ausgewählte, ausgeblendete Messungen bleiben in der Auswahl und werden im Zähler ausgewiesen. `Auswahl exportieren` lädt eine gemeinsame ZIP-Datei mit JSON-Metadaten, CSV-Übersichten oder vollständigen PowerLab-Bundles inklusive Rohdaten herunter. `Auswahl löschen` entfernt die ausgewählten Messungen nach einer gemeinsamen Bestätigung. Fehlgeschlagene Löschungen bleiben ausgewählt.

## PDF-Messprotokoll

In der Detailansicht einer beendeten Messung lädt **Export → Messprotokoll (PDF)**
ein einheitliches A4-Protokoll herunter. Für mehrere Messungen steht unter
**Auswahl exportieren** das Format **Messprotokolle (PDF im ZIP)** bereit.

Das Protokoll enthält Messkontext und Notizen, Messaufbau und Erkennungsschwellen,
Kennwerte der History-Auswertung, Datenqualität, Ereignisse, Marker sowie den
PPK2-Geräte- und Kalibrier-Snapshot. Zeitangaben sind ausdrücklich in UTC;
Ereignisse und Marker werden relativ zum Messbeginn angegeben. Fehlgeschlagene
Messungen sind als unvollständig gekennzeichnet. Laufende und geplante Messungen
können erst nach ihrem Ende als PDF exportiert werden.

Die PDF-Erstellung läuft lokal über das Python-Paket `typst`, das mit
`python -m pip install -r requirements.txt` installiert wird. Eine separate
Typst-CLI oder ein Cloud-Dienst ist nicht erforderlich. Das zentrale Layout liegt
in `app/templates/measurement.typ`, die Datenaufbereitung in `app/report.py`.
Namen und Notizen werden als JSON-Daten übergeben, nicht als Typst-Quelltext.
Der Einzelabruf ist auch über `GET /api/measurements/{id}/export/pdf` möglich.

## Messplanung

Beim Anlegen einer Messung stehen zur Verfügung:

**Start**
- Sofort
- Zeitpunkt

**Stop**
- Manuell
- Nach Dauer
- Endzeitpunkt

Beispiele:

- sofort starten und 30 Minuten messen
- heute um 23:00 starten und 8 Stunden messen
- morgen 08:00 starten und exakt um 17:00 stoppen
- geplante Session ohne Auto-Stop starten und später manuell beenden

Für geplante Messungen muss das zugewiesene PPK2 zum Startzeitpunkt angeschlossen und frei sein. Ist das Gerät nicht verfügbar, wird die Session als `failed` protokolliert.

## Mehrere PPK2

PowerLab erkennt Nordic PPK2 anhand von VID/PID und dem Messinterface `MI_01`. Mehrere Geräte können parallel laufen:

```text
PPK2 COM4 -> Measurement A
PPK2 COM7 -> Measurement B
PPK2 COM9 -> frei
```

Ein PPK2 kann nicht gleichzeitig von zwei laufenden Messungen verwendet werden. Überlappende geplante Sessions desselben PPK2 werden ebenfalls blockiert, soweit deren Zeitfenster bekannt sind.

## PPK2-Messlog

Jede Session speichert unter anderem:

- PPK2-ID
- COM-Port
- USB Serial / VID / PID / Interface / Location / HWID
- PPK2 HW / IA / Calibration Flag
- komplette Kalibrierkoeffizienten
- `ppk2-api` Version
- Source/Ampere Meter
- konfigurierte Spannung
- DUT-Power-Status
- Sample-Rate / Sample-Periode
- Reader-Buffer-Konfiguration
- erkannte verlorene Samples
- Schedule-Konfiguration

Der Snapshot liegt in SQLite, im Measurement-`metadata.json` und im Bundle-Export.

## Wake-Erkennung einstellen

Es stehen vier Erkennungsparameter zur Verfügung: Sleep-Schwelle und
Sleep-Mindestdauer sowie Wake-Schwelle und Wake-Mindestdauer. Es zählen nur
kontinuierlich bestätigte Zustandswechsel. Die Schwellen bleiben fest;
die Ruhe-Referenz hilft, den Beginn eines bestätigten Wakes rückwirkend
zuzuordnen. Vor- und Nachlauf legen den gespeicherten Rohdatenkontext fest.
Details und Beispiele: [THRESHOLD_DETECTION.md](THRESHOLD_DETECTION.md).

## Docker-Container

PowerLab bauen und starten:

```powershell
docker compose up -d --build
```

Mit Podman lautet der entsprechende Befehl `podman compose up -d --build`.
Die Weboberfläche ist unter http://localhost:8889 erreichbar, die API-Dokumentation
unter http://localhost:8889/docs. PowerLab hört im Container auf `0.0.0.0:8889`.

Die Compose-Datei speichert SQLite, Messungen und Exporte im Volume `powerlab_data`.
Vorhandene Dateien aus dem lokalen Ordner `data` werden nicht automatisch übernommen.
Um diese weiterzuverwenden, kann unter `powerlab.volumes` stattdessen `./data:/data`
eingetragen werden.

Für PPK2-Messungen muss das USB-Messinterface im Container verfügbar sein.
Unter Linux den kommentierten `devices`-Eintrag in `docker-compose.yml` aktivieren
und den Gerätepfad anpassen. Unter Windows muss das USB-Gerät zunächst an die
Linux-VM von Docker Desktop beziehungsweise Podman weitergereicht werden;
Windows-COM-Ports sind im Linux-Container nicht direkt verfügbar.

Stoppen mit `docker compose down`. Mit `down -v` werden auch die Daten-Volumes gelöscht.

## Windows-Setup

Getesteter Zielpfad: Windows + Python 3.14.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
```

PowerLab starten:

```powershell
.\scripts\run_windows.ps1
```

Danach:

```text
http://127.0.0.1:8000
```

Swagger/API:

```text
http://127.0.0.1:8000/docs
```

## Podman

Direkt möglich:

```powershell
podman machine start
podman compose up -d --build
```

Stop:

```powershell
podman compose down
```

`down -v` löscht zusätzlich das PowerLab-Datenvolume.

## PPK2-Verbindung unter Windows

PowerLab verwendet das PPK2-Mess-/Control-Interface. Bei dem validierten Windows-Aufbau war dies beispielsweise:

```text
COM4  USB\VID_1915&PID_C00A&MI_01\...
```

Andere PPK2-CDC-Interfaces werden nicht als Messgerät angeboten.

## Source Meter

Im Source-Meter-Modus stellt der PPK2 die konfigurierte DUT-Spannung bereit und misst gleichzeitig den Strom. Beispiel 3,3 V:

```text
PPK2 VOUT -> DUT VCC
PPK2 GND  -> DUT GND
```

Beim Start schaltet PowerLab den DUT-Ausgang ein. Beim Stop wird die Messung beendet und der DUT-Ausgang wieder ausgeschaltet.

## Datenmodell

```text
Measurement
├─ Setup + PPK2 snapshot
├─ Schedule
├─ SleepSegments
├─ WakeEvents
│  └─ 100-kS/s raw event data
├─ OverviewPoints
├─ Markers
└─ Statistics
```

Sleep-Phasen werden verdichtet in SQLite gespeichert; Wake-Events bleiben als Parquet-Dateien hochauflösend erhalten.

## Überwachung der Aufzeichnung

### Speichergrenzen für Wake-Events

Große Parquet-Events werden beim Öffnen anhand ihrer Blockstatistiken dargestellt,
ohne sämtliche Rohdaten zu dekodieren. Die Anzeige kennzeichnet diese Vorschau:
Minima und Maxima sind gespeicherte Extremwerte, ihre Zeitpositionen innerhalb
der zusammengefassten Blöcke sind angenähert. Digitale Signale und Glättung sind
in dieser Vorschau deaktiviert. Zoom allein lädt im Event-Betrachter noch keine
zusätzlichen Rohdaten nach.

Ein vollständiger Rohdaten-Lesevorgang ist auf zwei Millionen Samples begrenzt
(bei 100 kS/s etwa 20 Sekunden). Der Event-Endpunkt unterstützt `start_s` und
`end_s` relativ zum Trigger für kurze, exakt gelesene Zeitfenster. Die Live-Serie
liest ebenfalls nur passende Parquet-Blöcke. NPZ-Dekompressionsgröße,
Parquet-Blockgröße und Footergröße werden vor dem Dekodieren geprüft; gleichzeitig
läuft höchstens ein Rohdaten-Lesevorgang pro Datenspeicher.

Raw-CSV ist auf 250.000 Samples begrenzt, weil Textformatierung zusätzliche
Speicherkopien benötigt. Größere Anfragen erhalten HTTP 413 mit einem Hinweis.
Das Bundle enthält die vollständigen Rohdateien und komprimiert sie direkt von
der Festplatte. Große alte NPZ-Events ohne Blockstatistik erhalten ebenfalls einen
Größenhinweis statt eines unbeschränkten Ladevorgangs. Nach dem Update PowerLab
neu starten und die Browserseite neu laden.

### Abbrucherkennung

Ein unerwartet beendeter PPK2-Lesethread wird beim nächsten Leseaufruf erkannt.
Ein unabhängiger Watchdog bricht die Messung nach drei Sekunden ohne verwertbare
Samples ab, auch bei einem blockierten Leseaufruf. Blockaden beim Start, bei der
Verarbeitung oder beim Abschluss werden nach 30 Sekunden erkannt. Die Fristen
lassen sich über `POWERLAB_ACQUISITION_TIMEOUT_S` und `POWERLAB_WORKER_TIMEOUT_S`
konfigurieren. Der Status wechselt auf `failed`; der Abbruchgrund erscheint in der
Messungsansicht und wird in SQLite und den Metadaten gespeichert. Bereits erfasste
Daten werden beim Aufräumen abgeschlossen, soweit der fehlerhafte Treiber dies
zulässt. Solange ein blockierter Arbeitsthread noch lebt, bleibt sein COM-Port
für weitere Messungen gesperrt. Nach Änderungen PowerLab neu starten.

## Tests ausführen

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Zusätzlicher Hardware-Streamtest:

```powershell
.\.venv\Scripts\python.exe .\scripts\test_ppk2_stream.py
```

Der Hardwaretest prüft unter anderem den 6-Bit-Sample-Counter auf Datenlücken.

Abbrucherkennung mit echtem PPK2 prüfen (PowerLab vorher beenden):

```powershell
.\.venv\Scripts\python.exe .\scripts\test_acquisition_health.py --port COM4 --duration 60
```

Dieser Test verwendet den Ampere-Meter-Modus, schaltet die DUT-Versorgung nicht
ein und zeichnet in einer separaten Testdatenbank auf. Nach dem echten
Datenstrom simuliert er ausbleibende Samples und prüft Fehlerstatus,
Erkennungszeit, Live-Anzeige und gespeicherte Metadaten. Der Bericht liegt unter
`data/hardware-health-*/health-report.json`. Ein physisch abgezogenes USB-Kabel
und ein mehrstündiger Betrieb sind damit noch nicht geprüft.

## Verzeichnisstruktur

```text
app/
  main.py
  manager.py
  recorder.py
  db.py
  ppk/
  storage/
  static/
data/
  measurements/
  exports/
scripts/
tests/
docker-compose.yml
```

## Hinweis zur Messgenauigkeit

PowerLab verwendet derzeit `ppk2-api` für die PPK2-Kalibrierung und konvertiert/überwacht den Messstream in einer eigenen Adapter-Schicht. Für kalibrierpflichtige Laboranwendungen sollte die absolute Genauigkeit gegen eine bekannte Last und die aktuelle Nordic Power Profiler App verifiziert werden.

## 1.0.6 UI/History fixes

Wake-Event-Detail und Zustandsübersicht verwenden getrennte Chart-Container. Die Wake-Event-Liste bleibt sichtbar und zeigt zusätzlich die Periode zum vorherigen Wake. Im Live-Chart werden persistierte LOD-Historie und der aktuelle Live-Tail getrennt gerendert, damit keine künstliche Verbindungslinie über nicht dargestellte Zeitbereiche entsteht.
