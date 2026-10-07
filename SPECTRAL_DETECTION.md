# Spektralvergleich (experimentell)

Im Messdialog „Spektralvergleich (experimentell)“ auswählen. Stromschwellen
und Mindestdauern bleiben für die bestätigten Zustände, Rohdatenaufzeichnung,
Ereignisse und Auto-Stop maßgeblich. FFT Sleep (Türkis) und FFT Wake (Pink)
erscheinen als zusätzliche gestrichelte Markierungen in Stromverlauf,
Spektrogramm und Messprotokoll. Die FFT funktioniert auch bei ausgeblendeter
Spektrogrammanzeige. Gespeicherte Planungen behalten Modus und Parameter.

## Slice 1: Spektrum → Referenz → Vergleichszustand

Nach dem ersten bestätigten Sleep sammelt der Vergleich 30 Sekunden
zusammenhängende Spektralfenster, während der Stromdetektor Sleep meldet.
Ein Wake oder eine Datenlücke startet diese Lernphase neu. Das 95-%-Quantil
pro Frequenzband bildet die anschließend eingefrorene Referenz. Ein Boden
von −100 dB re 1 µA²/Hz verhindert extreme Verhältnisse bei numerisch ruhigen
Signalen. Die Referenz wird während der Messung nicht an Wake angepasst.

Verglichen werden Bänder von 8 Hz bis maximal 10 kHz bzw. Nyquist. Der Score
ist der Median der Abweichungen in dB über die Bänder: Er reagiert auf
breitbandige Veränderungen, einzelne Frequenzlinien genügen nicht.
Die einstellbare Wake-Schwelle beträgt zunächst 12 dB. Wake verlangt eine
anhaltende Überschreitung über 0,5 s; die Rückkehr unter die halbe dB-Schwelle
über 2 s bestätigt FFT Sleep. Dazwischen bleibt der bisherige Zustand bestehen.
Kurze Pulse verwerfen die laufende Bestätigung, erzeugen aber keinen Wake.
Datenlücken verwerfen Kandidaten; nach dem Lernen wird der Vergleichszustand
UNKNOWN, bis erneut genügend zusammenhängende Fenster vorliegen.

## Slice 2: Vergleich → Speicherung → Stream

Übergänge liegen als `fft_sleep_start` und `fft_wake_start` in den bestehenden
OverviewPoint-Datensätzen. Sie gehören zu den Zustandsmarkierungen, nicht
zu Strommittelwerten oder WakeEvent-Datensätzen. Die letzte Diagnose liegt
als `spectral_result` in den Messungseinstellungen; live wird
`spectral_comparison` übertragen. Eine neue Markierung synchronisiert die
WebSocket-Metadaten. Die bestehenden JSON-/Bundle-Exporte enthalten dadurch
die zusätzlichen Markierungen und die zuletzt gespeicherte Diagnose.

## Slice 3: Messdialog → Live-Vergleich → Messprotokoll

Der Modus ist ausdrücklich wählbar, Standard bleibt die Stromerkennung.
Der Messdialog erklärt Lernphase, Farben und Zuständigkeit für Ereignisse.
Live stehen Lernfortschritt, FFT-Zustand und spektrale Abweichung unter dem
Wake-Zähler. Das Messprotokoll enthält zusätzlich die FFT-Diagnose.

Die Markierung wird auf den Beginn des ersten abweichenden FFT-Fensters
zurückgesetzt; die 250-ms-Fenster und Überlappung begrenzen die Genauigkeit.
Das ist kein samplegenauer Firmware-Zustandswechsel. Weil der Mittelwert
abgezogen wird, kann ein reiner Wechsel zwischen zwei konstanten Strompegeln
ohne veränderte Schwankungen unentdeckt bleiben. Sehr langsame Sleep-Zyklen
werden mit diesen kurzen Fenstern nicht als Frequenz aufgelöst.

Validierung: `tests/test_spectral_detection.py`, `tests/test_spectrogram.py`,
`tests/test_detection_modes.py`, `scripts/check_threshold_ui.py` und
`scripts/check_live_summary_ui.py`. Vergleich an Hardwaremessungen steht aus;
12 dB ist ein Startwert für Experimente, kein belegter universeller Grenzwert.
