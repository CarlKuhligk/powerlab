# PowerLab

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

## History-Analyse

Die Statistik lässt sich mit einem separat erzeugten Referenzdatensatz prüfen. Der Generator importiert keinen Anwendungscode; seine Sollwerte stammen aus NumPy und `NormalDist`. Ausführen aus dem Projektverzeichnis:

```powershell
.venv\Scripts\python.exe tests\battery_statistics_generator.py --output .test-tools\battery-statistics-cases.json
.venv\Scripts\python.exe tests\verify_battery_statistics.py --cases .test-tools\battery-statistics-cases.json --report .test-tools\battery-statistics-verification.md
```

Der Prüflauf umfasst 126 Fälle und vergleicht die Verarbeitung gültiger Messphasen, Varianzen, Messungsgewichte, Laufzeiten und Perzentile mit den Referenzwerten. Er prüft zusätzlich das JavaScript-Modell und vergleicht die Histogrammanteile mit NumPy und prüft die Min/Max-Grenzen. Die Prüfung bestätigt die Rechenwege, keine kalibrierte Wahrscheinlichkeit der tatsächlichen Batterielaufzeit. Für andere reproduzierbare Daten kann beim Generator `--seed` angegeben werden. Erforderlich sind die Python-Testumgebung und Node.js (alternativ `.test-tools/node.exe`).

Der Menüpunkt **Batterielaufzeit** übernimmt eine oder mehrere abgeschlossene Messungen mit gültigen Sleep-/Wake-Zyklen. Jede Messung repräsentiert ein anderes Gerät. **Mittel** gibt jedem Gerät denselben Einfluss, **Gewichtet** richtet ihn nach der Anzahl gültiger Zyklen, **Eigene Gewichte** erlaubt relative Anteile (z. B. 7 und 3 für 70 % und 30 %). Gewicht 0 hat keinen Einfluss und erweitert keine Grenzen. Batterieenergie und Timing sind frei einstellbar: **Periodendauer** von Wake-Beginn zu Wake-Beginn, **Sleep-Dauer** (auch 24 Stunden) oder **Wake-Duty-Cycle**. Bei fester Periode verkürzt ein längerer Wake die anschließende Sleep-Zeit. Eine Periode unterhalb der längsten gültigen Wake-Dauer wird abgelehnt. Änderungen an Auswahl und Gewichtung behalten die Timing-Einstellung bei.

Das Chart zeigt die statistische Unsicherheit der geschätzten mittleren Laufzeit, nicht dauerhaft wiederholte Einzelzyklen. Die grüne Linie steht bei Batterieenergie / mittlerer Leistung. Eine lognormale Näherung auf Basis linearer Unsicherheitsfortpflanzung auf der logarithmischen Laufzeitskala zeigt die Dichte; P5–P95 begrenzen ein näherungsweises 90-%-Intervall. Die x-Achse zeigt Tage, Wochen, Monate oder Jahre, die y-Achse Dichte in Prozent pro Laufzeiteinheit. Der gezeichnete Bereich umfasst ±4 logarithmische Standardunsicherheiten, keine harten Min/Max-Grenzen. Der Median der Näherung entspricht der zentralen Laufzeitschätzung.

Je Gerät ist der Sleep-Strom gesamte gültige Sleep-Ladung / gesamte gültige Sleep-Dauer. Periodische Peaks bleiben im Verbrauch enthalten; Unterschiede zwischen einzelnen Sleep-Abschnitten erzeugen im Laufzeitmodell keinen eigenen Unsicherheitsbeitrag. Wake-Dauer und Wake-Energie bleiben gepaart. Verbrauch und Dauer werden zunächst je Gerät unter der gewählten Timing-Regel berechnet und dann gewichtet kombiniert. So bleiben auch Zusammenhänge zwischen dem Sleep-Verbrauch eines Geräts und seiner Wake-Dauer erhalten.

Die Mittelwertunsicherheit enthält getrennte Beiträge: Wake-Schätzunsicherheit innerhalb eines Geräts (Varianz der linearisierten Beiträge / Zyklusanzahl) und Unterschiede zwischen Gerätemittelwerten. Die beobachtete zwischen-Geräte-Varianz wird um bereits enthaltene Wake-Schätzunsicherheit korrigiert und bei null begrenzt; beide Beiträge werden mit quadrierten Geräteanteilen fortgepflanzt. Mehr Zyklen reduzieren den Wake-Schätzfehler, nicht dauerhafte Geräteunterschiede. Ein separater Bereich zeigt die Streuung und beobachtete gewichtete P5/P95 der aus den Gerätemittelwerten berechneten Laufzeiten. Diese Perzentile sind kein Prognoseintervall für weitere Geräte. Details und Formeln: [BATTERY_ANALYSIS.md](BATTERY_ANALYSIS.md).

Das Modell setzt unabhängige, repräsentative Wakes und unabhängige Geräte unter vergleichbaren Bedingungen voraus. Sleep-Messungen müssen genügend Peak-Perioden für einen repräsentativen Mittelwert enthalten. Mindestens zwei gültige Zyklen je aktivem Gerät sind für die Mittelwertunsicherheit nötig; mindestens zwei aktive Geräte für Gerätestreuung. Mit einem Gerät gilt das Unsicherheitsintervall nur für dieses Gerät. Bei wenigen Geräten oder großer relativer Unsicherheit ist die Delta-/Lognormal-Näherung eingeschränkt; das 90-%-Intervall ist keine garantierte Prognose. Zeitliche Abhängigkeiten, unsichere Messungsanteile, systematische Messfehler, Batterieunsicherheit, Selbstentladung, Alterung und Zustandsänderungen sind nicht enthalten. In der Tabelle beschreiben Wake-Varianzen Einzelzyklen und Sleep-Varianzen Gerätemittelwerte. Grundlage: [NIST TN 1297, Unsicherheitsfortpflanzung mit Kovarianzen](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-appendix-law-propagation-uncertainty).

**PDF-Bericht herunterladen** erstellt ein druckfertiges Laufzeitprotokoll. Die erste A4-Seite zeigt Laufzeit, näherungsweises 90-%-Intervall, mittlere Leistung, Batterie- und Timing-Einstellungen sowie ein weißes Vektordiagramm. Messgrundlage, Gerätegewichte, Sleep-Strom je Gerät sowie Tabellen zu Wake-Dauer, Wake-Energie, Sleep-Strom und Sleep-Leistung folgen auf der zweiten Seite. Sleep-Mittelwert, Minimum, Maximum, Standardabweichung, Varianz und relative Streuung beziehen sich auf die Gerätemittelwerte; Sleep-Energie wird nicht als Vergleichsgröße ausgegeben. Gerätestreuung der Laufzeiten und Mittelwertunsicherheit sind getrennt dokumentiert. Umfangreiche Messungslisten erhalten weitere Seiten. Diagramm und Zahlen werden gemeinsam serverseitig aus den gespeicherten Messungen berechnet (Matplotlib und Typst). Der UTC-Erstellungszeitpunkt steht auf jeder Seite und im Dateinamen, beispielsweise `battery_life_combined_2026-10-07_13-14-15_123456Z.pdf`. Ausführliche Erläuterungen sind im Tool über aufklappbare Hilfen erreichbar, insbesondere „Berechnung und Annahmen“. API: `POST /api/battery-report`, Modi `period`, `sleep` und `duty`; der Einzelmessungs-Export unter `POST /api/measurements/{id}/battery-report` bleibt verfügbar. Ein Browser-PNG ist nicht mehr erforderlich; bestehende Clients dürfen es weiterhin mitsenden, es wird validiert und durch das berechnete Druckdiagramm ersetzt.

Ein eigener Bereich **Sleep-Auswertung** in den Messungsdetails und im PDF-Messprotokoll zeigt erfasste Sleep-Zeit, Strom, Ladung und Energie einschließlich zugeordneter Hintergrundereignisse. Diese Kennwerte berücksichtigen gespeicherte Sleep-Daten auch ohne vollständigen Wake-Zyklus und ergänzen keine Datenlücken. Eine separate Tabelle zeigt die Sleep-Phasen aus gültigen Zyklen samt Kennzeichnung ergänzter Datenlücken. Gespeicherte Sleep-Abschnitte sind Speicher-Checkpoints und werden nicht als eigene Phasen gezählt. JSON-Exporte und Bundle-Metadaten enthalten denselben Bereich unter `sleep_analysis`. Das PDF enthält außerdem die Varianz, Standardabweichung und relative Streuung der gültigen Wake-Zustände.

Bereits während der Live-Messung steht unter dem Stromverlauf eine automatisch aktualisierte Tabelle der gültigen Wake-Phasen mit Startzeit seit Protokollbeginn, Dauer, mittlerem und maximalem Strom, Ladung und Energie. Sie verwendet dieselben vollständig bestätigten Zyklen wie die Varianzberechnung. Nach bestätigter Rückkehr in Sleep und Speicherung des Wake-Events erscheint die Phase; kleine ergänzte Datenlücken werden gekennzeichnet. Beim erneuten Öffnen oder Verbinden werden die bereits erfassten gültigen Phasen geladen.

Die Wake-Auswertung zeigt Wake-Dauer, zeitgewichteten Wake-Strom und mittlere Wake-Energie ohne Hochrechnung sowie die Varianz von Wake-Dauer, mittlerem Strom je Wake und Wake-Energie aus gültigen, vollständig bestätigten Sleep → Wake → Sleep-Zyklen. Jeder Wake zählt gleich; berechnet werden Mittelwert, Stichprobenvarianz (Division durch n − 1), Standardabweichung und relative Streuung (Standardabweichung / Betrag des Mittelwerts in Prozent). Bei weniger als zwei gültigen Wakes bleiben die Streuungswerte leer; bei Mittelwert null ist die relative Streuung nicht definiert. Die bestehenden zeitgewichteten Strommittelwerte bleiben erhalten. Kleine, bereits zugelassene Datenlücken werden wie in der Energieauswertung ergänzt und weiterhin ausgewiesen.

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

### USB-Freigabe unter Ubuntu/Linux

Das PPK2 muss am Ubuntu-Host angeschlossen sein. Läuft Ubuntu in einer VM,
muss das USB-Gerät zuerst an diese VM durchgereicht werden.

Auf dem Host die seriellen USB-Ports anzeigen:

```bash
ls -l /dev/serial/by-id/
```

Für PowerLab das PPK2-Mess-/Control-Interface `01` verwenden. Der passende
Eintrag enthält üblicherweise `if01`; sein Linkziel zeigt beispielsweise auf
`/dev/ttyACM0`. Die Portnummer ist nicht fest vorgegeben.

Unter `services` → `powerlab` in `docker-compose.yml` ergänzen beziehungsweise
den vorhandenen kommentierten Eintrag aktivieren:

```yaml
    devices:
      - "/dev/ttyACM0:/dev/ttyACM0:rwm"
```

Den tatsächlichen Gerätepfad auf beiden Seiten eintragen. Den ursprünglichen
Portnamen im Container beibehalten, damit die USB-Metadaten zur Geräteerkennung
zugeordnet werden können. Die Compose-Option ist in der
[Docker-Dokumentation zu devices](https://docs.docker.com/reference/compose-file/services/#devices)
beschrieben.

Nach der Änderung den Container neu erstellen und die Weboberfläche neu laden:

```bash
docker compose up -d --force-recreate
```

Die Erkennung im Container lässt sich unabhängig von der Weboberfläche prüfen:

```bash
docker compose exec powerlab python -m serial.tools.list_ports -v
```

Wenn bereits auf dem Host kein serieller Eintrag vorhanden ist, mit
`lsusb -d 1915:c00a` prüfen, ob das PPK2 überhaupt am Host erkannt wird.
Nach Abziehen und erneutem Einstecken kann sich die Portnummer ändern.
Dann die Zuordnung erneut prüfen, gegebenenfalls anpassen und den Container
neu erstellen.

### SerialException: Permission denied

Der Fehler `could not open port /dev/ttyACM0: [Errno 13] Permission denied`
bedeutet, dass der Prozess den Geräteport nicht öffnen darf. Läuft der
Container unter einem normalen Benutzer, benötigt dieser die Gruppe des
Geräteports. Auf dem Ubuntu-Host die Gruppe, ihre numerische ID und die Rechte
ermitteln (Gerätepfad gegebenenfalls anpassen):

```bash
stat -c 'Gruppe=%G GID=%g Rechte=%A' /dev/ttyACM0
```

Wenn beispielsweise `Gruppe=dialout GID=20 Rechte=crw-rw----` ausgegeben wird,
unter `services` → `powerlab` ergänzen:

```yaml
    devices:
      - "/dev/ttyACM0:/dev/ttyACM0:rwm"
    group_add:
      - "20"
```

Die `20` durch die tatsächlich ausgegebene GID ersetzen. Die numerische ID
vermeidet eine Abhängigkeit von Gruppennamen im Image.
[`group_add`](https://docs.docker.com/reference/compose-file/services/#group_add)
fügt die Gruppe dem Benutzer im Container hinzu. Voraussetzung ist, dass die
Gruppe am Geräteport Lese- und Schreibrechte hat.

Danach `docker compose up -d --force-recreate` ausführen und die Messung erneut
starten. Falls der Fehler bestehen bleibt, Benutzer und Portrechte im
Container prüfen:

```bash
docker compose exec powerlab id
docker compose exec powerlab ls -ln /dev/ttyACM0
```

Das Dockerfile dieses Projekts startet standardmäßig als `root`. Zeigt `id`
bereits `uid=0`, reicht eine zusätzliche Gruppe als Diagnose nicht aus.
Dann die wirksame Compose-Konfiguration (`docker compose config`), die
Gerätefreigabe und eine mögliche Rootless-/User-Namespace-Konfiguration des
Docker-Daemons prüfen.

### USB-Zugriff mit Rootless Podman

Bei Podman ohne `sudo` gelten zusätzlich die Rechte des Benutzers auf dem Host.
`root` im Container hat dabei keine Root-Rechte auf dem Host. Eine numerische
Host-GID unter `group_add` allein erhält wegen der Benutzerzuordnung nicht die
Host-Gruppenrechte.

Zuerst auf dem Ubuntu-Host prüfen:

Die Befehle in der Sitzung des normalen Benutzers ausführen, der PowerLab mit
Podman betreibt. `/dev/ttyACM0` in allen Beispielen durch den zuvor ermittelten
PPK2-Messport ersetzen.

```bash
ls -l /dev/ttyACM0
id
podman info --format 'Rootless={{.Host.Security.Rootless}} Runtime={{.Host.OCIRuntime.Name}}'
```

Ein typisches Ergebnis ist:

```text
crw-rw---- 1 root dialout 166, 0 ... /dev/ttyACM0
uid=1000(benutzer) gid=1000(benutzer) groups=1000(benutzer),27(sudo),...
Rootless=true Runtime=crun
```

Hier dürfen nur der Host-Benutzer `root` und Mitglieder von `dialout` den Port
lesen und schreiben. Fehlt `dialout` in der Ausgabe von `id`, hat der aktuelle
Benutzer keinen Zugriff. `Rootless=true Runtime=crun` bestätigt die
Voraussetzungen für die folgende Compose-Einstellung.

Gehört der Port beispielsweise `dialout`, muss der Benutzer, der Podman startet,
Mitglied dieser Gruppe sein. Falls die Gruppe fehlt:

```bash
sudo usermod -aG dialout "$USER"
```

`$USER` steht für den aktuell angemeldeten Benutzer; ein konkreter Benutzername
muss nicht eingetragen werden. Den Befehl aus dessen normaler Sitzung ausführen,
nicht aus einer Root-Shell. Falls der Port einer anderen Gruppe gehört, `dialout`
durch diese Gruppe ersetzen. `-aG` ergänzt die Gruppe und behält bestehende
Gruppenzugehörigkeiten bei.

Danach vollständig abmelden und neu anmelden (bei SSH die Verbindung neu
aufbauen). Die aktive Gruppenzugehörigkeit erneut prüfen:

```bash
id
```

Die Geräte-Gruppe, beispielsweise `dialout`, muss jetzt in der Ausgabe stehen.
Eine erfolgreiche Prüfung sieht beispielsweise so aus:

```text
uid=1000(benutzer) gid=1000(benutzer) groups=1000(benutzer),20(dialout),...
```

Bleibt `dialout` nach `usermod` in der Ausgabe aus, ist die neue Gruppe in
dieser Sitzung noch nicht aktiv. Ein weiteres Terminal in einer bestehenden
Anmeldung genügt nicht unbedingt; bei SSH die Verbindung vollständig trennen
und neu verbinden.

Erst danach den Container neu erstellen, damit Podman die neue Gruppe übernimmt.

Für Rootless Podman mit der OCI-Runtime `crun` die bisherigen numerischen
`group_add`-Einträge durch `keep-groups` ersetzen:

```yaml
    devices:
      - "/dev/ttyACM0:/dev/ttyACM0:rwm"
    group_add:
      - keep-groups
```

`keep-groups` erhält die zusätzlichen Gruppen des aufrufenden Host-Prozesses.
Es benötigt `crun` und darf nicht mit weiteren `group_add`-Einträgen kombiniert
werden; siehe [Podman-Dokumentation](https://docs.podman.io/en/latest/markdown/podman-create.1.html#group-add-group-keep-groups).

`group_add` muss unter `services` → `powerlab` auf derselben Einrückungsebene
wie `devices` stehen und darf nicht auskommentiert sein. Beide Voraussetzungen
sind erforderlich: Die aktive Host-Sitzung enthält die Geräte-Gruppe und die
Compose-Konfiguration erhält diese über `keep-groups`.

Den Container im Verzeichnis der Compose-Datei als derselbe Host-Benutzer
**ohne `sudo`** neu erstellen. Liegt das Projekt beispielsweise in
`~/powerlab`, zuerst dorthin wechseln. Mit `config` die tatsächlich verwendete
Konfiguration prüfen; im Abschnitt `powerlab` muss `group_add: [keep-groups]`
stehen (gegebenenfalls als mehrzeilige YAML-Liste):

```bash
cd ~/powerlab
podman compose config
podman compose up -d --force-recreate
podman inspect powerlab --format 'GroupAdd={{json .HostConfig.GroupAdd}}'
```

`~/powerlab` durch das eigene Projektverzeichnis ersetzen. Die letzte Ausgabe
dient zur Kontrolle der gespeicherten Container-Konfiguration. Zeigt sie
`GroupAdd=[]`, obwohl `keep-groups` in der aufgelösten Compose-Konfiguration
steht, die Ausgabe des Neuerstellens und die Provider-Version zur weiteren
Diagnose festhalten. Die Host-Mitgliedschaft in `dialout` allein bestätigt
noch nicht den Zugriff des Containers.

Anschließend die PowerLab-Weboberfläche neu laden und die Messung erneut starten.

Falls der Fehler bestehen bleibt, die oben genannten Host-Ausgaben sowie
`podman exec powerlab ls -ln /dev/ttyACM0` zur Diagnose verwenden. Meldet der
Compose-Provider einen Fehler zu `keep-groups`, diesen ebenfalls festhalten.
Die verwendeten Versionen lassen sich mit `podman --version` und, beim
Provider `podman-compose`, mit `podman-compose --version` ermitteln.

## Docker-Image auf GitHub bauen

Der Workflow [.github/workflows/docker.yml](.github/workflows/docker.yml) baut
das Linux-Image (`linux/amd64`) auf einem GitHub-Runner. Ein Container-Starttest
prueft den Healthcheck und die Geraete-API ohne angeschlossenes PPK2.

- Pull Requests nach `main`: Image bauen und testen.
- Push nach `main`: nach erfolgreichem Test nach GHCR hochladen, mit `latest`
  und einem Commit-Tag (`sha-...`).
- Versionstags mit dem Präfix `v`: nach erfolgreichem Test als Release-Tag und mit
  Commit-Tag hochladen. `latest` bleibt beim Stand von `main`.
- Manuell unter GitHub **Actions > Docker image > Run workflow** starten.
  Hochgeladen wird nur von der Standardbranch oder einem `v`-Tag.

Der Image-Name wird automatisch aus dem Repository abgeleitet. Fuer
`CarlKuhligk/powerlab` lautet er `ghcr.io/carlkuhligk/powerlab`.
Der Workflow nutzt den bereitgestellten `GITHUB_TOKEN`; zusaetzliche Secrets
oder ein CI-Tool auf dem Windows-PC sind nicht erforderlich.

Jeder Workflow-Lauf erhält eine automatisch gezählte Build-Version nach dem
Schema `build-<Laufnummer>.<Versuch>`. Die Laufnummer steigt bei jedem neuen
Lauf dieses Workflows; bei einer Wiederholung steigt die Versuchsnummer.
Auch Pull-Request-Builds zählen mit. Veröffentlichte Images erhalten diese
Version als zusätzlichen Tag. Sie ist außerdem im OCI-Label
`org.opencontainers.image.version`, in der Container-Umgebungsvariable
`POWERLAB_VERSION`, unter `/api/version`, in der API-Dokumentation und in der
Weboberfläche hinterlegt. Die Build-Version steht auch in der Actions-Zusammenfassung.
Lokale Builds verwenden standardmäßig `dev`; über `POWERLAB_VERSION` lässt sich
bei Docker Compose eine eigene Version für Image-Tag und Build festlegen.

Auf einem anderen PC mit Docker das fertige Image starten:

```powershell
docker run -d --name powerlab --restart unless-stopped -p 8889:8889 -v powerlab_data:/data ghcr.io/carlkuhligk/powerlab:latest
```

Die Weboberflaeche ist danach unter http://localhost:8889 erreichbar.
Bei einem privaten GHCR-Paket ist vorher eine Registry-Anmeldung erforderlich;
fuer einen Download ohne Anmeldung muss das Paket auf GitHub oeffentlich sein.
Das PPK2-Messinterface muss wie oben beschrieben an den Container durchgereicht
werden. Das Image enthaelt keine vorhandenen Messdaten.

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

## UI/History fixes

Wake-Event-Detail und Zustandsübersicht verwenden getrennte Chart-Container. Die Wake-Event-Liste bleibt sichtbar und zeigt zusätzlich die Periode zum vorherigen Wake. Im Live-Chart werden persistierte LOD-Historie und der aktuelle Live-Tail getrennt gerendert, damit keine künstliche Verbindungslinie über nicht dargestellte Zeitbereiche entsteht.
