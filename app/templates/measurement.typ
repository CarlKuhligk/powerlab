#let report = json(bytes(sys.inputs.report))
#let accent = rgb("145c72")
#set document(title: "Messprotokoll – " + report.name, author: "PowerLab")
#set text(font: ("Arial", "DejaVu Sans"), size: 9pt, lang: "de")
#set page(paper: "a4", margin: (x: 18mm, top: 20mm, bottom: 20mm),
  header: [#text(fill: accent, weight: "bold")[POWERLAB] #h(1fr) Messprotokoll],
  footer: context [#text(size: 7pt)[#report.id] #h(1fr) #counter(page).display("1 / 1", both: true)])
#set heading(numbering: "1.")
#set par(leading: 0.65em)
#show heading: set text(fill: accent)
#let rows-table(rows, headers: ("Parameter", "Wert"), columns: (1fr, 1.6fr)) = table(
  columns: columns, inset: 5pt, stroke: 0.4pt + rgb("d7e0e5"),
  fill: (x, y) => if y == 0 { rgb("e9f1f4") } else { none },
  table.header(..headers.map(h => text(weight: "bold", h))),
  ..rows.flatten().map(v => [#v]),
)
#text(size: 23pt, weight: "bold", fill: accent)[Messprotokoll]
#parbreak()
#text(size: 15pt, weight: "bold")[#report.name]
#parbreak()
Status: #report.status #h(1fr) Erstellt: #report.generated
#if report.status != "completed" {
  block(fill: rgb("fff2d9"), inset: 8pt, width: 100%)[
    Diese Messung ist nicht regulär abgeschlossen. Ergebnisse können unvollständig sein.
  ]
}
#if report.error != "" { block(inset: 8pt, fill: rgb("fff2d9"))[Abbruchgrund: #report.error] }
= Messkontext
#rows-table(report.context)
== Notizen
#report.notes
= Messaufbau und Erkennung
#rows-table(report.setup)
#pagebreak()
= Messergebnisse
#rows-table(report.results)
Die Kennwerte entsprechen der gespeicherten History-Auswertung. Bei Schwellen-Erkennung
beziehen sich die Sleep/Wake-Mittelwerte, Periodendauer und Duty-Cycle auf vollständig
bestätigte Zyklen. Fehlende Kennwerte sind mit „—“ gekennzeichnet.
Die Energie wird aus der Gesamtladung und der konfigurierten Spannung berechnet;
eine separate Spannungsmessung erfolgt nicht.
= Datenqualität
#rows-table(report.quality)
Alle absoluten Zeitangaben sind in UTC. Ereignis- und Markerzeiten sind relativ zum
Messbeginn. Rohdaten und Darstellungsfilter werden durch den Export nicht verändert.
= Ereignisübersicht
#if report.events.len() == 0 { [Keine Ereignisse gespeichert.] } else {
  set text(size: 7.5pt)
  rows-table(report.events, headers: ("Nr.", "Art", "Beginn", "Dauer", "Ø Strom", "Peak", "Ladung"),
    columns: (0.4fr, 0.9fr, 1fr, 1fr, 1fr, 1fr, 1fr))
}
= Marker
#if report.markers.len() == 0 { [Keine Marker gespeichert.] } else {
  rows-table(report.markers, headers: ("Zeit ab Messbeginn", "Beschriftung"))
}
#pagebreak()
= Geräte- und Kalibrierkonfiguration
Gespeicherter PPK2-Snapshot zur Nachvollziehbarkeit des Messaufbaus:
#set text(size: 7.5pt)
#raw(report.config, lang: "json", block: true)
