# PowerLab v1.0 – Architektur

## Ziel

Langzeitfähige Ultra-Low-Power-Messung mit Nordic PPK2 bei nominal 100 kS/s, ohne Sleep-Phasen unnötig als Einzelpunkte zu persistieren.

## Laufzeitarchitektur

```text
Browser
  ├─ Live overview WebSocket
  ├─ Measurement WebSocket /{id}
  └─ REST history / setup / export
             │
          FastAPI
             │
      MeasurementManager
       ├─ Scheduler
       ├─ ActiveRun PPK2 #1
       ├─ ActiveRun PPK2 #2
       └─ ActiveRun PPK2 #N
             │
       NordicPPK2Driver
             │
       ThresholdRecorder
        ├─ Sleep segments -> SQLite
        ├─ Overview       -> SQLite
        └─ Wake raw       -> Parquet/Zstd
```

## Mehrgerätebetrieb

`MeasurementManager._active` ist ein Dictionary nach Measurement-ID. Jede Session besitzt einen eigenen Worker, Driver, Recorder und Preview-Ringbuffer. Ein COM-Port darf nur einem laufenden Worker zugeordnet sein.

## Scheduler

Geplante Sessions werden als `status=scheduled` in SQLite persistiert. Ein interner Scheduler prüft regelmäßig fällige Sessions. Beim Start wird die gespeicherte Messkonfiguration erneut geladen und der konkrete PPK2 geöffnet.

Statusfluss:

```text
scheduled -> starting -> recording -> completed
                         \-> failed
scheduled -> cancelled
```

Auto-Stop kann relativ (`duration`) oder absolut (`end`) sein.

## Zeitbasis

Die relative Rohdatenzeit basiert auf dem PPK2-Sample-Raster von 10 µs. Der 6-Bit-Sample-Counter dient zur Erkennung von Datenlücken. Die Host-Zeit dient als UTC-Anker, nicht als Sample-Takt.

## Streaming wake storage and accumulated consumption

Wake samples are written during acquisition in blocks of at most 100,000 samples (Parquet/Zstd row groups). The NPZ fallback writes bounded parts plus a manifest; reading/export combines these parts. The recorder retains only the configured pre-trigger/confirmation tails and incremental event metrics, rather than the entire open wake. Event metrics exclude the final sleep-confirmation interval, while the raw event preserves that bounded tail.

Stop first stops the device and preserves queued samples already received by the PPK2 driver. Those samples are integrated before the final event is closed. Only the remaining file block, footer and event/measurement metadata are finalized; the recorded finish timestamp precedes that file finalization. The REST stop operation runs outside FastAPI's async event loop so other requests and live updates remain responsive.

Total charge is calculated from every received sample before classification or display reduction: `Q[µC] = sum(I[µA]) / sample_rate_hz`. Calibration, sleep, wakes and confirmation tails are included exactly once, even where event pre-trigger windows overlap. Compensated float64 accumulation reduces long-run rounding drift. SQLite saves totals approximately once per second during incoming acquisition and again on stop; metadata and exports use those totals. Sleep raw samples are discarded except bounded wake pre-trigger/confirmation context; sleep segments retain count, mean, extrema, variance and integrated charge. Display-history points are RAM-only.

Energy uses the configured measurement voltage: `E[µWh] = Q[µC] * V[V] / 3600`. Lost USB samples are reported as gaps/coverage and are never synthesized into charge; accuracy applies to the samples actually received.

## UI

`Live` enthält nur laufende und geplante Sessions. Eine Session-Unterseite enthält Setup und – falls aktiv – den Live-Chart. Historische Messungen liegen getrennt unter `Messungen`.

Plotly verwendet eine stabile `uirevision`, sodass ein manueller Zoom bei neuen Live-Daten erhalten bleibt. `Live folgen` schaltet zurück auf Auto-Range.

## Live chart: progressive Level of Detail

The acquisition path remains 100 kS/s. The browser does not receive that full stream. `GET /api/measurements/{id}/series` returns bounded display data. When acquisition summaries are unavailable, the legacy API fallback uses cached Min/Max wake envelopes (up to 2,002 points per event, cached for 128 events) and flat sleep means over each segment's boundaries. Explicit narrow viewports can read original wake samples for finer detail. Independent segment keys and line breaks prevent fabricated diagonal ramps through sparse event summaries; peak markers remain separate. The active UI uses the weighted summary path described below.

`Live folgen` always displays `t=0 ... now`; as the measurement grows, only the display resolution changes. Manual zoom/pan freezes the viewport and triggers a range-scoped LOD request, so zooming progressively reveals the stored high-resolution wake waveform without loading unrelated data.

Running acquisitions maintain mergeable display summaries independently of sleep/wake persistence. Before the first preview reduction, each block records its received sample count, float64 sum, mean, extrema, first/last values and actual sample boundaries. Histories exceeding 80,000 blocks compact to at most 40,000 using aligned time buckets, combining sums/counts instead of averaging already reduced extrema. Blocks never merge across device-tick gaps. If disjoint segments alone exceed the point budget, isolated representative blocks retain the global extrema and endpoints; their statistics describe only those retained blocks.

The active chart initially requests `view=overview`, with a point budget derived from its CSS width (500 to 4,000 summary blocks). It shows sample-weighted means with separate, subtle Min/Max polygons. Means stay in linear current units even on logarithmic axes. This overview reads no wake raw files when acquisition summaries are available, including short measurements. The recent extrema preview remains bounded for zoom detail, while acquisition summaries preserve the full time span even during unfinished wakes.

Manual zoom requests `view=detail`. Ranges of at most 2,000,000 sample ticks with at most 32 overlapping events load stored wake samples; wider ranges retain the overview. Where raw data are unavailable, the chart labels and displays summaries, never inventing recovered samples. An intersecting compacted block keeps its full statistical extent in the tooltip, while its geometry is clipped to the viewport. Summaries are excluded wherever raw wake data or the recent detail preview are drawn. Returning to live follow restores the summary overview. Completed-measurement state history and consumption integration are unchanged.

Current axes select nA, µA, mA, or A from the largest absolute current in the displayed series. Wake-event zoom selects the unit from the visible time range and preserves that range when rerendering. API/export data remain in µA. Wake-event tooltips follow the pointer with an opaque background, and the loading/error status is separate from the Plotly container.

The backend accepts only threshold detection. Sleep and wake require configured current thresholds and continuous confirmation durations; RecorderBase supplies buffering and storage without a detection algorithm.

## Completed measurement history (v1.0.5)

Die primäre History einer abgeschlossenen Messung ist bewusst **keine verdichtete Stromkurve**. Stattdessen wird aus den exakt erkannten Wake-Events eine kompakte Sleep/Wake-Zustands-Timeline erzeugt. Dadurch bleiben auch mehrtägige Messungen exakt und leicht darstellbar. Ein Wake-Marker öffnet die zugehörigen nativen 100-kS/s-Rohdaten.

Die Zyklusstatistik wird serverseitig aus Sleep-Segmenten und Wake-Events berechnet:

- Periodendauer: Trigger-zu-Trigger
- Sleep-Strom: nach Sleep-Samples gewichtetes Mittel
- Wake-Strom: über Wake-Ladung und Wake-Dauer gewichtetes Mittel
- mittlere Wake-Dauer
- Wake-Duty-Cycle

Frontend-Plotoperationen werden für die History serialisiert, damit ein langsamer Overview-Render niemals eine bereits angeforderte Wake-Detailansicht überschreiben kann.
