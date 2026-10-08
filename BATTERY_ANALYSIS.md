# Batterieanalyse je Gerät

Jede ausgewählte Messung repräsentiert ein anderes Gerät. Standardmäßig zählt
jedes Gerät gleich. Die Optionen nach Zyklusanzahl und eigene Gewichte ändern
diese Geräteanteile ausdrücklich. Gewichte sind Anteile, keine Anzahl zusätzlicher Geräte.

## 1. Verbrauchsmodell

Der Sleep-Strom eines Geräts ist gesamte gültige Sleep-Ladung / gesamte gültige
Sleep-Dauer. Dies entspricht dem nach Abschnittsdauer gewichteten Mittelstrom.
Periodische Peaks bleiben vollständig enthalten. Der Mittelwert wird im
Laufzeitmodell als fester Geräteparameter verwendet; seine Form und Unterschiede
zwischen Abschnittsmittelwerten erzeugen keinen eigenen Zufallsbeitrag.
Die Messung muss genügend Peak-Perioden abdecken. Eine Abhängigkeit der
Peak-Energie von der neu gewählten Sleep-Dauer wird nicht modelliert.

Wake-Energie und Wake-Dauer bleiben als zusammengehörige Beobachtungen erhalten.
Bei unterschiedlichen Messspannungen wird der Sleep-Strom je Gerät aus dessen
Sleep-Leistung / Messspannung bestimmt, bevor Geräte kombiniert werden.

Für die Timing-Regel stehen drei Optionen zur Verfügung:

| Modus | Sleep-Dauer nach einem Wake mit Dauer D |
| --- | --- |
| Feste Sleep-Dauer S | S |
| Feste Periodendauer T, Wake-Beginn zu Wake-Beginn | T − D |
| Fester Wake-Duty-Cycle a in Prozent | D × (100/a − 1) |

Eine Periode unterhalb einer gültigen Wake-Dauer wird abgelehnt. T = D ist
zulässig und bedeutet für diesen Wake keine Sleep-Zeit. API-Werte für `sleep`
und `period` sind Sekunden; für `duty` Prozent.
Der gemessene Periodenmedian verwendet aufeinanderfolgende Wake-Beginne mit
dazwischen vollständig gültigem Sleep. Unterbrochene Intervalle werden ausgelassen.

Für Gerät d seien W_d die mittlere Wake-Energie in µWh, D_d die mittlere
Wake-Dauer in Sekunden und P_d die Sleep-Leistung in µW. Aus der Timing-Regel
ergeben sich S_d, Zyklusenergie A_d = 3600 W_d + P_d S_d in µW·s und
Zyklusdauer L_d = D_d + S_d. Mit Geräteanteilen q_d ist:

```
A = Σ q_d A_d
L = Σ q_d L_d
mittlere Leistung = A / L
zentrale Laufzeitschätzung [h] = Batterieenergie [Wh] × 10⁶ × L / A
```

Insbesondere werden P_d und D_d gemeinsam je Gerät verarbeitet. Das Produkt
der getrennten Gesamtmittelwerte würde im Perioden- und Duty-Modus die
Zusammenhänge zwischen Gerätemittelwerten verlieren.

## 2. Streuung und Unsicherheit

Die Tabelle vergleicht Sleep-Strom und -Leistung zwischen den Gerätemittelwerten.
Ein Gerät liefert einen Mittelwert und Min/Max, jedoch keine Schätzung der
Gerätestreuung. Wake-Dauer und -Energie beschreiben weiterhin Einzelzyklen mit
Gewichten q_d/n_d. Gerätemittelwerte verwenden q_d. Gewichtete Stichprobenvarianzen
verwenden den Nenner 1 − Σ Gewichte².

Der separate Gerätebereich berechnet pro Gerät H_d = Batterieenergie × 10⁶ ×
L_d/A_d. Standardabweichung und gewichtete P5/P95 beschreiben diese beobachteten
Gerätelaufzeiten. Sie sind kein Prognoseintervall für weitere Geräte und keine
Mittelwertunsicherheit. Unterschiede können auch durch ungleiche Betriebsbedingungen entstehen.

Für die Unsicherheit der zentralen Laufzeit wird auf log(H) linearisiert.
Je Gerät ist der Einfluss eines Wake-Zyklus:

```
x_di = (−3600/A) × (W_di − W_d) + g_Dd × (D_di − D_d)
g_Dd = 1/L                       für feste Sleep-Dauer
g_Dd = P_d/A                     für feste Periode
g_Dd = 1/D̄ − (100/a − 1) P_d/A   für festen Duty-Cycle
v_d = Stichprobenvarianz(x_di) / n_d
```

Diese Varianz erhält die Kovarianz von Wake-Dauer und -Energie. Zusätzlich wird
der Gerätebeitrag r_d = −(A_d−A)/A + (L_d−L)/L betrachtet. Sei B_obs dessen
gewichtete Stichprobenvarianz und c = 1 − Σ q_d². Dann:

```
B = max(0, B_obs − Σ q_d(1−q_d) v_d / c)
V_innerhalb = Σ q_d² v_d
V_zwischen = B × Σ q_d²
V_log = V_innerhalb + V_zwischen
Standardunsicherheit [h] = zentrale Laufzeit × √V_log
```

Die Korrektur vermeidet, endliche Wake-Schätzfehler zusätzlich als Geräteunterschiede
zu zählen. Bei nur einem Gerät entfällt der zwischen-Geräte-Beitrag; Aussagen
gelten dann nur für dieses Gerät. Mindestens zwei gültige Zyklen je aktivem Gerät
sind für v_d nötig. Zusätzliche Zyklen reduzieren v_d; zusätzliche unabhängige
Geräte verbessern die Schätzung der Gerätepopulation. Mehr Zyklen desselben
stabilen Geräts beseitigen keine dauerhaften Geräteunterschiede.

Das angezeigte 90-%-Intervall ist die Lognormal-Näherung
H × exp(±1,64485 √V_log). Es beschreibt Mittelwertunsicherheit. Bei wenigen
Geräten, wenigen Zyklen oder großen relativen Schwankungen ist diese Näherung
eingeschränkt. Zeitliche Abhängigkeiten, Batterieunsicherheit, Selbstentladung,
Alterung und systematische Messfehler sind nicht enthalten.

## 3. Anzeige, Export und Prüfung

Rechner und PDF zeigen getrennt die Mittelwertunsicherheit und die beobachtete
Streuung der Gerätelaufzeiten. Sleep-Energie bleibt intern für die Integration
erhalten, wird aber nicht als Vergleichsgröße in deren Statistiktabelle ausgegeben.
Der PDF-Export enthält Sleep-Strom je ausgewähltem Gerät sowie Mittelwert,
Minimum, Maximum, Standardabweichung, Varianz und relative Streuung über die
Gerätemittelwerte. Das Timing wird dokumentiert; Diagramm und Zahlen werden
serverseitig neu berechnet.

Regressionsprüfungen decken periodische Sleep-Schwankungen, stabile Geräte mit
verschiedenen Sleep-Mittelwerten, Wiederholung der Zyklen, Wake-Kovarianz,
verschiedene Spannungen, alle Timing-Regeln und Gewichte einschließlich null ab.
126 erzeugte Fälle verwenden unabhängige NumPy-Referenzen für das Verbrauchsmodell.
Die hierarchische Unsicherheit wird zusätzlich durch numerische Ableitungen und
NumPy-Kovarianzen geprüft. Python und JavaScript müssen übereinstimmen;
PDF-Tests prüfen sichtbare Kennwerte, Seiteninhalt und druckbare Grenzen.
