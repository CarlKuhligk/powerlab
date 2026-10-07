#let report = json(bytes(sys.inputs.report))
#let accent = rgb("145c72")
#set document(title: "Batterielaufzeit – " + report.name, author: "PowerLab")
#set text(font: ("Arial", "DejaVu Sans"), size: 9pt, lang: "de")
#set page(paper: "a4", margin: (x: 18mm, top: 18mm, bottom: 18mm),
  header: [#text(fill: accent, weight: "bold")[POWERLAB] #h(1fr) Batterielaufzeit],
  footer: context [#text(size: 7pt)[#report.id] #h(1fr) #counter(page).display("1 / 1", both: true)])
#set heading(numbering: "1.")
#show heading: set text(fill: accent)
#let rows-table(rows, headers: ("Parameter", "Wert"), columns: (1fr, 1.4fr)) = table(
  columns: columns, inset: 5pt, stroke: 0.4pt + rgb("d7e0e5"),
  fill: (x, y) => if y == 0 { rgb("e9f1f4") } else { none },
  table.header(..headers.map(h => text(weight: "bold", h))),
  ..rows.flatten().map(v => [#v]),
)
#text(size: 22pt, weight: "bold", fill: accent)[Batterielaufzeit]
#parbreak()
#text(size: 14pt, weight: "bold")[#report.name]
#parbreak()
Erstellt: #report.generated
= Einstellung und Ergebnis
#rows-table(report.setup + report.results)
= Mittlere Laufzeit und ihre Unsicherheit
#image("chart.png", width: 100%)
Grün: Laufzeitschätzung aus mittlerer Leistung. Blau: angenäherte Unsicherheitsdichte.
Schattierte Fläche: näherungsweises 90-%-Intervall (P5 bis P95).
Die x-Achse zeigt Laufzeit, die y-Achse Dichte in Prozent pro Laufzeiteinheit.
Die Kurve zeigt die Unsicherheit der Schätzung, keine Verteilung dauerhaft wiederholter Einzelzyklen.
Bei null Streuung oder nicht schätzbarer Unsicherheit wird eine Markierung gezeigt.
#pagebreak()
= Übernommene Messstatistik
Gewichtung: #report.weighting.
#if report.sources.len() > 0 {
  heading(level: 2)[Einbezogene Messungen]
  set text(size: 7.5pt)
  rows-table(report.sources, headers: ("Messung", "ID", "Zyklen", "Gewicht", "Einfluss", "Spannung"),
    columns: (1.4fr, 1.4fr, 0.6fr, 0.7fr, 0.7fr, 0.7fr))
}
Die Werte stammen aus den gültigen Zyklen der ausgewählten Messungen mit positivem Einfluss.
Bei mehreren Messungen erhält jeder Zyklus seinen Messungsanteil / Zyklusanzahl.
Die Varianz enthält auch Unterschiede zwischen Messungen und verwendet die
Korrektur 1 − Summe der quadrierten Zyklusgewichte (bei gleichen Gewichten: n − 1).
Varianz und Standardabweichung verwenden diese gewichtete Stichprobenvarianz;
mindestens zwei gültige Zyklen sind erforderlich.
#block[
  #set text(size: 7.5pt)
  #rows-table(report.statistics,
    headers: ("Kennwert", "Mittelwert", "Minimum", "Maximum", "Standardabw.", "Varianz"),
    columns: (1.5fr, 1fr, 1fr, 1fr, 1fr, 1fr))
]
= Unsicherheitsintervall der mittleren Laufzeit
#rows-table(report.percentiles, headers: ("Perzentil", "Quantilniveau", "Näherung"), columns: (0.8fr, 0.7fr, 1.3fr))
P5 und P95 begrenzen das näherungsweise 90-%-Intervall der mittleren Laufzeitschätzung.
Sie sind keine garantierten Grenzen der tatsächlichen Batterielaufzeit.
= Rechenmodell und Annahmen
Wake-Energie und Wake-Dauer stammen aus der Messung. Die Sleep-Energie wird über die
gemessene Sleep-Leistung proportional zur gewählten Sleep-Dauer skaliert.
Zugehörige Sleep-/Wake-Werte bleiben paarweise erhalten.
Die erwartete Sleep-Leistung ist nach gemessener Sleep-Dauer gewichtet.
Bei mehreren Messungen werden zunächst die Kennwerte je Messung bestimmt
und anschließend nach den gewählten Messungsanteilen kombiniert.
Die erwartete mittlere Leistung ergibt sich aus Zyklusenergie / Zyklusdauer;
Laufzeit = nutzbare Batterieenergie / mittlere Leistung.

Die Batterieenergie ist die an der Messspannung nutzbare Energie. Selbstentladung,
Alterung und Änderungen des Gerätezustands sind nicht modelliert.
Die Unsicherheit wird durch lineare Fortpflanzung auf der logarithmischen Laufzeitskala
berechnet (Delta-Methode mit Kovarianzen). Die Kurve verwendet eine lognormale Näherung,
deren Median die Laufzeitschätzung aus mittlerer Leistung ist; sie ist keine empirische
Häufigkeitsverteilung. Der Chart zeigt den Bereich von minus vier bis plus vier
Standardunsicherheiten auf der logarithmischen Skala, keine harten Laufzeitgrenzen.
Die Mittelwertunsicherheit wird je Messung aus der Stichprobenvarianz geteilt durch
die Zyklusanzahl bestimmt und mit dem Quadrat des Messungsanteils kombiniert.
Sleep-Leistung ist ein Verhältnis aus gemessener Sleep-Energie und Sleep-Dauer;
deren Kovarianzen und die Zusammenhänge mit Wake-Energie und Wake-Dauer bleiben erhalten.
Unabhängige, repräsentative Zyklen und unabhängige Messungen werden vorausgesetzt.
Mindestens zwei gültige Zyklen je Messung mit positivem Einfluss sind erforderlich.
Bei wenigen Zyklen oder großer Streuung ist die Näherung eingeschränkt.
Messungsanteile, Batterieenergie und Timing gelten als fest. Zeitliche Abhängigkeiten,
systematische Messfehler und Batterieunsicherheit sind nicht enthalten.
Das Intervall ist keine garantierte Laufzeitprognose.
