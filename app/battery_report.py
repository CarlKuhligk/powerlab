"""PDF export of the selected lifetime scenario and the browser's chart snapshot."""
import base64
import binascii
import json
import math
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .battery_life import combine, evaluate, measured_statistics, weighted_statistics, profile
from .report import date, number

TEMPLATE = Path(__file__).parent / 'templates' / 'battery-life.typ'


def report_data(measurement, summary, request, combined=None):
    data = combined if combined is not None else profile(summary)
    point = evaluate(data, request.energy_wh, request.mode, request.value)
    finite_scenarios = all(math.isfinite(v) for v in point['scenarios_h'])
    scatter = (weighted_statistics(point['scenarios_h'], data.get('weights', [1/data['count']]*data['count']))
               if finite_scenarios else {'stddev': None, 'variance': None})
    statistics = data['statistics'] if combined is not None else measured_statistics(summary)
    def lifetime(hours):
        if not math.isfinite(hours):
            return 'Kein Verbrauch im Modell'
        factor, unit = ((8760, 'Jahre') if hours >= 8760 else (730, 'Monate') if hours >= 2160
                        else (168, 'Wochen') if hours >= 336 else (24, 'Tage') if hours >= 48
                        else (1, 'h'))
        return number(hours / factor, unit, 6)
    return {
        'name': measurement['name'], 'id': measurement['id'],
        'generated': date(datetime.now(timezone.utc).isoformat()),
        'weighting': {'mean': 'Gleiche Anteile je Messung', 'cycle_count': 'Nach Anzahl gültiger Zyklen',
                      'custom': 'Eigene Gewichte je Messung'}.get(data.get('weighting'), 'Eine Messung'),
        'sources': [[s['name'], s['id'], str(s['count']), number(s['weight'], digits=6),
                     number(s['share'] * 100, '%', 3), number(s['voltage_v'], 'V')]
                    for s in data.get('sources', [])],
        'setup': [['Nutzbare Batterieenergie', number(request.energy_wh * 1e6, 'µWh', 6)],
                  ['Messspannung', number(summary['voltage_v'], 'V')],
                  ['Gültige gemessene Zyklen', str(data['count'])],
                  ['Davon mit ergänzten Datenlücken', str(summary.get('interpolated_cycle_count', 0))],
                  ['Einstellung über', 'Sleep-Dauer' if request.mode == 'sleep' else 'Wake-Duty-Cycle'],
                  ['Gewählte Sleep-Dauer', number(point['sleep_s'], 's', 6)],
                  ['Gemessene mittlere Wake-Dauer', number(data['wake_s'], 's', 6)],
                  ['Wake-Duty-Cycle', number(point['duty_pct'], '%', 6)]],
        'results': [['Erwartete Laufzeit', lifetime(point['expected_h'])],
                    ['Erwartete Leistung', number(point['power_uw'], 'µW', 6)],
                    ['Standardabweichung der Szenario-Laufzeiten', lifetime(scatter['stddev']) if scatter['stddev'] is not None else '—'],
                    ['Varianz der Szenario-Laufzeiten', number(scatter['variance']/576 if scatter['variance'] is not None else None, 'd²', 9)],
                    ['Empirische untere Grenze P5', lifetime(point['percentiles']['5']) if data['count'] > 1 else 'Ab zwei gültigen Zyklen'],
                    ['Empirische obere Grenze P95', lifetime(point['percentiles']['95']) if data['count'] > 1 else 'Ab zwei gültigen Zyklen'],
                    ['Kürzeste gemessene Szenario-Laufzeit', lifetime(point['percentiles']['0'])],
                    ['Längste gemessene Szenario-Laufzeit', lifetime(point['percentiles']['100'])]],
        'statistics': [[label] + [number(statistics[key].get(field), unit + ('²' if field == 'variance' else ''), 6)
                                   for field in ['mean', 'min', 'max', 'stddev', 'variance']]
                       for label, key, unit in [('Wake-Energie', 'wake_energy', 'µWh'),
                                                 ('Sleep-Energie · gemessen', 'sleep_energy', 'µWh'),
                                                 ('Sleep-Leistung · normiert', 'sleep_power', 'µW')]],
        'percentiles': [[('Minimum' if p == 0 else 'Maximum' if p == 100 else 'Median · P50' if p == 50 else f'P{p}'),
                         f'{p} %',
                         lifetime(value) if data['count'] > 1 or p == 50 else 'Ab zwei gültigen Zyklen']
                        for key, value in point['percentiles'].items() for p in [int(key)]],
    }


def report_data_many(measurements, request):
    requested = {s.measurement_id: s.weight for s in request.sources}
    sources = [{'id': m['id'], 'name': m['name'], 'weight': requested[m['id']],
                'summary': m['cycle_energy']} for m in measurements]
    data = combine(sources, request.weighting)
    shares = {s['id']: s['share'] for s in data['sources']}
    summary = {'voltage_v': None, 'interpolated_cycle_count': sum(
        m['cycle_energy'].get('interpolated_cycle_count', 0) for m in measurements
        if shares[m['id']] > 0)}
    result = report_data({'id': 'Kombinierte Messungen' if len(measurements) > 1 else measurements[0]['id'],
                          'name': f'{len(measurements)} ausgewählte Messungen' if len(measurements) > 1 else measurements[0]['name']},
                         summary, request, combined=data)
    result['setup'][1] = ['Messspannungen', ', '.join(number(v, 'V') for v in sorted({
        s['voltage_v'] for s in data['sources'] if s['share'] > 0}))]
    return result


def decode_chart(data_url):
    try:
        image = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    except (IndexError, binascii.Error) as error:
        raise ValueError('Ungültiges PNG-Chart.') from error
    if len(image) < 33 or image[:8] != b'\x89PNG\r\n\x1a\n' or image[12:16] != b'IHDR':
        raise ValueError('Ungültiges PNG-Chart.')
    width, height = struct.unpack('>II', image[16:24])
    if not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > 8_000_000:
        raise ValueError('PNG-Chart ist zu groß oder hat ungültige Abmessungen.')
    return image


def render_report(measurement, summary, request):
    return _compile(report_data(measurement, summary, request), request.chart_png)


def render_multi_report(measurements, request):
    return _compile(report_data_many(measurements, request), request.chart_png)


def _compile(data, chart_png):
    import typst

    chart = decode_chart(chart_png)
    with tempfile.TemporaryDirectory(prefix='powerlab-battery-') as directory:
        root = Path(directory)
        (root / 'chart.png').write_bytes(chart)
        (root / 'battery-life.typ').write_text(TEMPLATE.read_text(encoding='utf-8'), encoding='utf-8')
        try:
            return typst.compile(str(root / 'battery-life.typ'), root=str(root),
                                 sys_inputs={'report': json.dumps(data, ensure_ascii=False)})
        except typst.TypstError as error:
            raise ValueError('Das Chart konnte nicht in das PDF übernommen werden.') from error
