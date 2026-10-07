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
= Laufzeitverteilung für die gewählte Einstellung
#image("chart.png", width: 100%)
Blau: gegl?ttete Sch?tzung der gewichteten Verteilungsdichte gemessener Verbrauchsszenarien.
Grün: Laufzeit aus mittlerer Leistung. Goldene Linie: empirischer Median.
Helle Fläche: beobachtetes Min/Max; dunklere Fläche: empirisches P5–P95-Intervall.
Die x-Achse zeigt Laufzeit in automatisch gewählten Einheiten, die y-Achse Verteilungsdichte in Prozent pro Laufzeiteinheit.
Die Fl?che unter der Kurve entspricht 100 %. Bei einer einzelnen Laufzeit wird eine Markierung gezeigt.
Die Verteilung reicht nicht über die Min/Max-Grenzen der gemessenen Szenarien hinaus.
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
= Laufzeit-Perzentile für die gewählte Einstellung
#rows-table(report.percentiles, headers: ("Perzentil", "Quantilniveau", "Gemessene Szenarien"), columns: (0.8fr, 0.7fr, 1.3fr))
Die Spalte „Gemessene Szenarien“ beschreibt entsprechend den gewählten Messungsanteilen gewichtete
Szenarien aus den beobachteten Sleep-/Wake-Paaren. Bei mehreren Messungen wird an
den Mittelpunkten der kumulierten Gewichte linear interpoliert.
Jedes Szenario nimmt an, dass sich der Verbrauch seines gemessenen Zyklus wiederholt.
P10 ist das linear interpolierte 10-%-Perzentil dieser Szenariolaufzeiten.
Min/Max sind die Grenzen der beobachteten Szenarien und keine garantierten Grenzen.
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
Das Szenarioband ist kein Konfidenzintervall und keine kalibrierte Wahrscheinlichkeit
der tatsächlichen Batterielaufzeit. Bei nur einem gültigen Zyklus ist keine Streuungsauswertung möglich.
Die Kurve verwendet eine gewichtete Kerndichtesch?tzung mit Randspiegelung und
Normierung innerhalb der gemessenen Min/Max-Grenzen. Die Gl?ttung h?ngt von der
Streuung und der effektiven Stichprobengr??e ab. Besonders bei wenigen Zyklen
h?ngt die Kurvenform stark von der Gl?ttung ab. Es wird keine Normalverteilung
der Laufzeiten vorausgesetzt. P5–P95 sind interpolierte empirische Perzentile; bei wenigen Szenarien
enthält dieses Intervall nicht zwingend genau 90 % des Gewichts.
Die Achse skaliert automatisch. Kapazitätsänderungen verschieben die Laufzeitwerte,
können aber eine ähnliche Kurvenform ergeben. Bei langen Sleep-Dauern dominiert die Sleep-Leistung.
Bei null Streuung oder weniger als zwei gültigen Zyklen wird eine Laufzeitmarkierung gezeigt.
