#let report = json(bytes(sys.inputs.report))
#let accent = rgb("145c72")
#let muted = rgb("526472")
#set document(title: "Batterielaufzeit – " + report.name, author: "PowerLab")
#set text(font: ("Arial", "DejaVu Sans"), size: 9pt, lang: "de", fill: rgb("23323e"))
#set par(leading: 0.65em)
#set page(paper: "a4", fill: white, margin: (x: 18mm, top: 19mm, bottom: 19mm),
  header: [#text(fill: accent, weight: "bold")[POWERLAB] #h(1fr) #text(size: 8pt)[Batterie-Messbericht]],
  footer: context [#text(size: 7pt, fill: muted)[Erstellt: #report.generated] #h(1fr) #counter(page).display("1 / 1", both: true)])
#show heading: set text(fill: accent)
#show heading.where(level: 1): set text(size: 12pt)
#let rows-table(rows, headers: ("Parameter", "Wert"), columns: (1fr, 1.2fr)) = table(
  columns: columns, inset: 5pt, stroke: 0.4pt + rgb("d7e0e5"),
  fill: (x, y) => if y == 0 { rgb("e9f1f4") } else { none },
  table.header(..headers.map(h => text(weight: "bold", h))),
  ..rows.flatten().map(v => [#v]),
)
#let metric(label, value, color: accent) = block(width: 100%, inset: 10pt,
  radius: 4pt, fill: rgb("f2f6f8"))[
  #text(size: 8pt, fill: muted)[#label]
  #parbreak()
  #text(size: 13pt, weight: "bold", fill: color)[#value]
]
#text(size: 23pt, weight: "bold", fill: accent)[Batterielaufzeit]
#parbreak()
#text(size: 12pt, weight: "bold")[#report.name]
#parbreak()
#text(size: 8pt, fill: muted)[Erstellt: #report.generated · Gewichtung: #report.weighting]
#v(3mm)
#grid(columns: (1fr, 1.2fr, 0.85fr), gutter: 3mm,
  metric("Geschätzte mittlere Laufzeit", report.results.at(0).at(1), color: rgb("16713b")),
  metric("90-%-Intervall · Näherung", report.interval, color: rgb("2366ac")),
  metric("Mittlere Leistung", report.results.at(1).at(1)),
)
= Berechnetes Betriebsszenario
#rows-table(report.setup)
= Laufzeitschätzung und Unsicherheit
#image("chart.svg", width: 100%)
#text(size: 8pt, fill: muted)[
  Grüne Linie: zentrale Laufzeitschätzung. Blaue Kurve: angenäherte Unsicherheitsdichte;
  schattierter Bereich: P5 bis P95. Die Fläche unter der Dichtekurve entspricht 100 %.
]
#v(2mm)
#block(inset: 9pt, radius: 3pt, stroke: 0.5pt + rgb("d7e0e5"))[
  *Einordnung:* #report.uncertainty_note
  Das Intervall ist keine garantierte Grenze der tatsächlichen Batterielaufzeit.
]
#pagebreak()
#text(size: 18pt, weight: "bold", fill: accent)[Messgrundlage und Verfahren]
#parbreak()
#text(size: 8pt, fill: muted)[Messbezug: #report.id]
= Einbezogene Messungen
Gewichtung: #report.weighting. Nur Messungen mit positivem Einfluss tragen zum Ergebnis bei.
#if report.sources.len() > 0 {
  set text(size: 7.5pt)
  rows-table(report.sources, headers: ("Messung", "ID", "Zyklen", "Gewicht", "Einfluss", "Spannung"),
    columns: (1.5fr, 1.6fr, 0.65fr, 0.7fr, 0.7fr, 0.8fr))
} else [
  *Messung:* #report.name · #report.id
]
= Messstatistik der gültigen Zyklen
#block[
  #set text(size: 7.5pt)
  #rows-table(report.statistics,
    headers: ("Kennwert", "Mittelwert", "Minimum", "Maximum", "Standardabw.", "Varianz"),
    columns: (1.45fr, 1fr, 1fr, 1fr, 1fr, 1fr))
]
Standardabweichung und Varianz beschreiben die Streuung der Einzelzyklen.
Die Standardunsicherheit der mittleren Laufzeitschätzung beträgt
*#report.results.at(2).at(1)*. Sie beschreibt die Unsicherheit des geschätzten Mittelwerts.
= Rechenmodell
Die mittlere Leistung ergibt sich aus der Wake-Energie und der auf die gewählte
Sleep-Dauer skalierten Sleep-Energie, geteilt durch die gesamte Zyklusdauer.
Die Sleep-Leistung wird als gemessene Sleep-Energie / gemessene Sleep-Dauer bestimmt.

#block(inset: 10pt, fill: rgb("f2f6f8"), radius: 3pt)[
  *Laufzeit = nutzbare Batterieenergie / mittlere Leistung*
]

Bei mehreren Messungen werden die Kennwerte entsprechend ihrem festen Anteil kombiniert.
Die Unsicherheit wird durch lineare Fortpflanzung auf der logarithmischen Laufzeitskala
bestimmt (Delta-Methode); Zusammenhänge innerhalb eines Sleep-/Wake-Paars werden berücksichtigt.
Die Dichte und P5/P95 beruhen auf einer lognormalen Näherung, deren Median die zentrale
Laufzeitschätzung ist.
= Gültigkeit und Grenzen
Vorausgesetzt werden unabhängige, repräsentative Zyklen und unabhängige Messungen.
Mindestens zwei gültige Zyklen je Messung mit positivem Einfluss sind erforderlich.
Bei wenigen Zyklen oder großer Streuung ist die Näherung eingeschränkt.

Batterieenergie, Timing und Messungsanteile gelten als fest.
Selbstentladung, Alterung, Zustandsänderungen, zeitliche Abhängigkeiten,
systematische Messfehler und Batterieunsicherheit sind nicht enthalten.

#text(size: 8pt, fill: muted)[
  Ausführliche Erläuterungen zu Gewichtung, Stichprobenvarianz und Unsicherheitsfortpflanzung
  stehen im Batterierechner unter „Berechnung und Annahmen“.
]
