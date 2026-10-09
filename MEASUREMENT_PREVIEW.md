# Messvorschau und Triggerabstimmung

Im Dialog „Messung anlegen“ bzw. „Planung bearbeiten“ zuerst Gerät, Modus und
Spannung auswählen. Unter „Messvorschau & Triggerabstimmung“ startet
„Vorschau starten“ einen vorübergehenden PPK2-Datenstrom. Es entsteht dabei
kein Measurement-Datensatz, keine Messhistorie und kein Exportverzeichnis.
Im Source-Modus wird die gewählte Versorgungsspannung wie bei einer Messung
am PPK2 eingestellt.

## Datenerfassung und Zustandserkennung

Die Vorschau reserviert den PPK2 gegenüber weiteren Vorschauen und laufenden
Messungen. `PreviewEngine` behält bis zu 60 Sekunden Originalsamples für
Neuberechnungen sowie eine begrenzte Mittelwert-/Min-/Max-Übersicht und STFT.
`ThresholdRecorder` und `SpectralComparison` sind die bestehenden Detektoren;
die Vorschau ersetzt deren Speicher-Callbacks durch temporäre Markierungen.
Die Analyse beginnt mit der bestätigten Sleep-Suche, nicht mit einer
angenommenen Klassifikation aus der verdichteten Stromanzeige.

Strom und FFT verwenden die Zeit seit Vorschau-Start. Beginn und Bestätigung
werden separat angezeigt. Ein offener Wake-Kandidat wird auf die verfügbare
Vorschauhistorie begrenzt; Vorlauf, Nachlauf und Mindestdauern stammen aus
denselben Eingabefeldern wie die später gespeicherte Messung.

## Triggerparameter und erneute Auswertung

Stromschwellen lassen sich als Zahlen einstellen, als Linien ziehen oder mit
„Wake-Schwelle setzen“ / „Sleep-Schwelle setzen“ per Klick in der Stromkurve
festlegen. Zoom und Verschieben stehen als eigenes Werkzeug bereit.
Logarithmische und lineare Stromskala verändern keine Detektorwerte.

Im Spektralvergleich erscheinen zusätzlich Spektrogramm und Verlauf der
spektralen Abweichung. Die pinke FFT-Wake-Schwelle lässt sich ziehen; der Wert
landet im Feld „Spektrale Abweichung [dB]“. Stromereignisse und Auto-Stop
verwenden weiterhin die Stromerkennung. FFT-Markierungen bleiben separat.

Änderungen werden nach kurzer Eingabepause validiert und seriell angewendet.
Die noch vorhandenen Rohdaten werden mit den neuen Einstellungen neu
ausgewertet. Währenddessen blendet die Oberfläche alte Trigger aus und
kennzeichnet die Neuberechnung. Sleep und FFT-Referenz werden dabei neu
gelernt; enthält der Ausschnitt keinen ausreichend langen Sleep, bleibt die
Analyse in der Lernphase. Aus den Diagramm-Mittelwerten werden keine
samplegenauen Trigger abgeleitet.

„Vorschau anhalten“ beendet die Geräteaufnahme und behält die letzten Daten für
weitere Parameteränderungen. „Vorschau neu starten“ startet einen neuen
Datenstrom mit den aktuellen Werten. Änderungen von Gerät, Messmodus oder
Spannung schließen die Vorschau; ein neuer Start verwendet die neuen Werte.

## Übernahme der Messparameter

Die Vorschau schreibt direkt in die vorhandenen Formulareingaben. Beim
Anlegen oder Speichern einer Planung wartet die Oberfläche auf laufende
Parameteränderungen, beendet die Vorschau und gibt den PPK2 frei, bevor die
reguläre Messung angelegt wird. Dieselben Detektorwerte werden mit der
Messung gespeichert und beim Bearbeiten wiederhergestellt.
Vorschau-Rohdaten und gelernte Referenzen werden nicht übernommen; die neue
Messung beginnt ihre eigene Sleep-Suche und Kalibrierung.

Dialogschließen, Escape und WebSocket-Abbruch beenden die Vorschau. Ein Start,
der erst nach dem Schließen beantwortet wird, wird ebenfalls aufgeräumt.
Ohne WebSocket-Verbindung läuft die Geräteaufnahme spätestens nach der
15-Sekunden-Anschlussfrist aus. Datenstromfehler werden in der Vorschau
gemeldet und schließen das Gerät. Die Vorschau erzeugt keine WakeEvent- oder
SleepSegment-Datensätze und beeinflusst keine Messungszähler.

## Schnittstellen und Prüfung

- `POST /api/measurement-previews`: temporäre Vorschau starten.
- `PATCH /api/measurement-previews/{id}`: Detektoreinstellungen prüfen und neu auswerten.
- `POST /api/measurement-previews/{id}/pause`: Aufnahme anhalten, Daten behalten.
- `DELETE /api/measurement-previews/{id}`: Gerät und temporäre Daten freigeben.
- `/ws/measurement-previews/{id}`: Übersicht, Zustände, Scores und Spektral-Deltas;
  höchstens vier Aktualisierungen pro Sekunde, ein Betrachter pro Vorschau.

`tests/test_measurement_preview.py` prüft die Übereinstimmung mit dem regulären
Detektor, Rohdaten-Replay, Speicherschranken, Lücken, Gerätekonflikte, Pause,
Speicherung, Abbruch, Anschlussfrist und Hardwarefehler. Der Browsercheck
`python scripts/check_measurement_preview_ui.py` verwendet echte Detektoren
mit synthetischen Daten und prüft Klick-/Ziehwerkzeuge, FFT-Justierung, Pause,
Übernahme der Werte beim Planen, verspätete Starts und die Handyansicht.
Ein Dauertest mit einem echten PPK2 steht noch aus.
