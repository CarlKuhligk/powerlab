"""Generate reviewable synthetic data and an SVG using the production detector."""
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

from app.recorder import RecorderSettings
from app.threshold import ThresholdRecorder
from signal_generator import SignalGenerator


def main():
    output = ROOT / 'data' / 'wake-onset-example'
    output.mkdir(parents=True, exist_ok=True)
    rate = 1000
    signal = (SignalGenerator(rate=rate).plateau(.2, 500).plateau(6, 4)
              .plateau(.02, 35).plateau(.2, 4)
              .staircase([(1.2, 2000), (.4, 8000), (.4, 5000), (.1, 11000)])
              .plateau(6, 4))
    markers, events = [], []
    rec = ThresholdRecorder(RecorderSettings(
        sample_rate_hz=rate, detection_mode='threshold', sleep_threshold_ua=6,
        pre_trigger_ms=1000, post_trigger_ms=1000),
        on_sleep_segment=lambda _: None, on_wake_event=events.append, on_overview=markers.append)
    for batch in signal.batches(137):
        rec.process(batch)
    rec.finish()
    values = signal.values()
    origin = rec.protocol_start_sample
    with (output / 'signal.csv').open('w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['acquisition_sample', 'acquisition_s', 'protocol_s', 'current_ua'])
        for tick, current in enumerate(values):
            writer.writerow([tick, tick / rate, (tick - origin) / rate if tick >= origin else '', float(current)])
    selected = [m for m in markers if m.kind in {'sleep_start', 'wake_start', 'sleep_validated', 'wake_validated'}]
    payload = {'detection': rec.detection_info(), 'markers': [
        {'kind': m.kind, 'sample_index': m.sample_index, 't_s': m.sample_index / rate} for m in selected],
        'events': [{'trigger_sample': e.trigger_sample, 'duration_us': e.duration_us,
                    'raw_start': int(e.sample_index[0]), 'raw_end': int(e.sample_index[-1])} for e in events]}
    (output / 'result.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    end = (len(values) - origin) / rate
    x = lambda t: 70 + t / end * 850
    y = lambda current: 265 - math.log10(max(1, current)) / math.log10(20000) * 205
    points = []
    for start in range(origin, len(values), 10):
        block = values[start:start + 10]
        for offset in sorted({0, len(block) - 1, int(block.argmin()), int(block.argmax())}):
            points.append(f'{x((start + offset - origin) / rate):.2f},{y(float(block[offset])):.2f}')
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="980" height="370" viewBox="0 0 980 370">',
           '<rect width="980" height="370" fill="#111820"/>',
           '<g fill="#dbe6f0" font-family="sans-serif" font-size="12">',
           '<text x="70" y="25" font-size="17">Wake-Beginn bei 2 mA · Bestätigung erst bei 11 mA</text>']
    event = events[0]
    svg.append(f'<rect x="{x(event.start_sample/rate)}" y="45" width="{x((event.sample_index[-1]+1)/rate)-x(event.start_sample/rate)}" height="225" fill="#60a5fa" opacity=".1"/>')
    for current in (4, 35, 2000, 8000, 11000):
        svg.append(f'<line x1="70" x2="920" y1="{y(current)}" y2="{y(current)}" stroke="#334155"/><text x="8" y="{y(current)+4}">{current} µA</text>')
    svg.append(f'<polyline points="{" ".join(points)}" stroke="#65d98b" stroke-width="1.5" fill="none"/>')
    labels = {'wake_start': 'Wake-Beginn', 'wake_validated': 'Wake bestätigt'}
    for m in selected:
        if not m.kind.startswith('wake'):
            continue
        position, color = x(m.sample_index / rate), '#f5bd5b'
        svg.append(f'<line x1="{position}" x2="{position}" y1="40" y2="270" stroke="{color}" opacity=".4"/>')
        svg.append(f'<circle cx="{position}" cy="{y(m.current_ua)}" r="4" stroke="{color}" fill="{"none" if m.kind.endswith("validated") else color}"/>')
        svg.append(f'<text x="{position+7}" y="{45 if m.kind=="wake_start" else 62}">{labels[m.kind]}: {m.sample_index/rate:.3f}s</text>')
    for second in range(math.ceil(end)):
        svg.append(f'<text x="{x(second)}" y="292">{second}s</text>')
    svg.extend(['<text x="70" y="323">35-µA-Sleep-Peak wird verworfen · 2 → 8 → 5 → 11 mA bleibt zusammenhängend</text>',
                '<text x="70" y="345">Logarithmische Stromachse · Zeit ab Sleep Start · Blau: 1000ms Vorlauf + Event + 1000ms Nachlauf</text>', '</g></svg>'])
    (output / 'wake-onset.svg').write_text('\n'.join(svg), encoding='utf-8')
    print(json.dumps(payload['events']))


if __name__ == '__main__':
    main()
