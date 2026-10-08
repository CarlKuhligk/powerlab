"""Printable runtime report with a chart recomputed from the measurement data."""
import base64
import binascii
import json
import math
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .battery_life import combine, device_distribution, evaluate, measured_statistics, uncertainty, profile
from .report import number

TEMPLATE = Path(__file__).parent / 'templates' / 'battery-life.typ'


def report_data(measurement, summary, request, combined=None, generated_at=None):
    data = combined if combined is not None else profile(summary)
    point = evaluate(data, request.energy_wh, request.mode, request.value)
    estimate = uncertainty(data, request.energy_wh, request.mode, request.value)
    devices = device_distribution(data, request.energy_wh, request.mode, request.value)
    statistics = data['statistics'] if combined is not None else measured_statistics(summary)
    generated_at = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    def lifetime_unit(hours):
        return ((8760, 'Jahre') if hours >= 8760 else (730, 'Monate') if hours >= 2160
                        else (168, 'Wochen') if hours >= 336 else (24, 'Tage') if hours >= 48
                        else (1, 'h'))
    def lifetime(hours):
        if not math.isfinite(hours):
            return 'Kein Verbrauch im Modell'
        factor, unit = lifetime_unit(hours)
        return number(hours / factor, unit, 3)
    factor, unit = lifetime_unit(point['expected_h'])
    duty = point['duty_pct']
    return {
        'name': measurement['name'], 'id': measurement['id'],
        'generated': generated_at.strftime('%d.%m.%Y %H:%M:%S.%f UTC'),
        'chart_point': point, 'chart_estimate': estimate,
        'interval': (number(estimate['percentiles_h']['5'] / factor) + ' bis ' +
                     number(estimate['percentiles_h']['95'] / factor, unit)) if estimate['available'] else 'Nicht schätzbar',
        'uncertainty_note': estimate['reason'] or (
            'Keine beobachtete Streuung: das Modellintervall fällt auf die Schätzung zusammen. '
            'Dies belegt keine fehlerfreie Messung.' if estimate['log_variance'] == 0 else
            'Das 90-%-Intervall beschreibt die statistische Unsicherheit der geschätzten mittleren Laufzeit.'),
        'weighting': {'mean': 'Gleiche Anteile je Messung', 'cycle_count': 'Nach Anzahl gültiger Zyklen',
                      'custom': 'Eigene Gewichte je Messung'}.get(data.get('weighting'), 'Eine Messung'),
        'sources': [[s['name'], s['id'], str(s['count']), number(s['weight'], digits=6),
                     number(s['share'] * 100, '%', 3), number(s['voltage_v'], 'V'),
                     number(s['sleep_current_ua'], 'µA', 3)]
                    for s in data.get('sources', [])],
        'setup': [['Nutzbare Batterieenergie', number(request.energy_wh * 1e6, 'µWh')],
                  ['Messspannung', number(summary['voltage_v'], 'V')],
                  ['Gültige gemessene Zyklen', str(data['count'])],
                  ['Davon mit ergänzten Datenlücken', str(summary.get('interpolated_cycle_count', 0))],
                  ['Einstellung über', {'sleep': 'Sleep-Dauer', 'duty': 'Wake-Duty-Cycle',
                                        'period': 'Periodendauer · Wake-Beginn zu Wake-Beginn'}[request.mode]],
                  ['Periodendauer' if request.mode == 'period' else 'Mittlere Sleep-Dauer im Modell',
                   number(request.value if request.mode == 'period' else point['sleep_s'], 's')],
                  ['Gemessene mittlere Wake-Dauer', number(data['wake_s'], 's')],
                  ['Wake-Duty-Cycle', (f'{duty:.3g}'.replace('.', ',') + ' %')
                   if 0 < duty < .001 else number(duty, '%')]],
        'results': [['Laufzeitschätzung aus mittlerer Leistung', lifetime(point['expected_h'])],
                    ['Mittlere Leistung', number(point['power_uw'], 'µW')],
                    ['Standardunsicherheit der Laufzeitschätzung', lifetime(estimate['standard_error_h']) if estimate['available'] else 'Nicht schätzbar'],
                    ['Untere 90-%-Grenze · P5 (Näherung)', lifetime(estimate['percentiles_h']['5']) if estimate['available'] else 'Nicht schätzbar'],
                    ['Obere 90-%-Grenze · P95 (Näherung)', lifetime(estimate['percentiles_h']['95']) if estimate['available'] else 'Nicht schätzbar']],
        'statistics': [[label] + [number(statistics[key].get(field), unit + ('²' if field == 'variance' else ''), 3)
                                   for field in ['mean', 'min', 'max', 'stddev', 'variance']] +
                       [number(100 * statistics[key]['stddev'] / abs(statistics[key]['mean']), '%', 3)
                        if statistics[key]['stddev'] is not None and statistics[key]['mean'] else '—']
                       for label, key, unit in [('Wake-Dauer · Zyklen', 'wake_duration', 's'),
                                                 ('Wake-Energie · Zyklen', 'wake_energy', 'µWh'),
                                                 ('Sleep-Strom · Gerätemittel', 'sleep_current', 'µA'),
                                                 ('Sleep-Leistung · Gerätemittel', 'sleep_power', 'µW')]],
        'device_results': [
            ['Geräte mit positivem Einfluss', str(devices['count'])],
            ['Standardabweichung der Gerätelebensdauern', lifetime(devices['stddev_h']) if devices['available'] else 'Nicht schätzbar'],
            ['Beobachtete Geräte · P5 bis P95',
             lifetime(devices['percentiles_h']['5']) + ' bis ' + lifetime(devices['percentiles_h']['95'])
             if devices['available'] else 'Nicht schätzbar']],
        'device_note': devices['reason'] or 'Streuung der aus den Gerätemittelwerten berechneten Laufzeiten; beobachtete gewichtete Perzentile, kein Prognoseintervall.',
        'percentiles': [[f'P{p} · Näherung', f'{p} %', lifetime(estimate['percentiles_h'][str(p)]) if estimate['available'] else 'Nicht schätzbar']
                        for p in [5, 10, 50, 90, 95]],
    }


def report_data_many(measurements, request, generated_at=None):
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
                         summary, request, combined=data, generated_at=generated_at)
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


def report_filename(generated_at, measurement_id='combined'):
    safe_id = ''.join(c for c in measurement_id if c.isascii() and (c.isalnum() or c in '-_')) or 'measurement'
    timestamp = generated_at.astimezone(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S_%fZ')
    return f'battery_life_{safe_id}_{timestamp}.pdf'


def render_report(measurement, summary, request, generated_at=None):
    return _compile(report_data(measurement, summary, request, generated_at=generated_at), request.chart_png)


def render_multi_report(measurements, request, generated_at=None):
    return _compile(report_data_many(measurements, request, generated_at=generated_at), request.chart_png)


def _compile(data, chart_png=None):
    import typst
    from .battery_chart import render_chart

    if chart_png is not None:
        decode_chart(chart_png)  # Keep validation for legacy callers; never embed their snapshot.
    chart = render_chart(data.pop('chart_point'), data.pop('chart_estimate'))
    with tempfile.TemporaryDirectory(prefix='powerlab-battery-') as directory:
        root = Path(directory)
        (root / 'chart.svg').write_bytes(chart)
        (root / 'battery-life.typ').write_text(TEMPLATE.read_text(encoding='utf-8'), encoding='utf-8')
        try:
            return typst.compile(str(root / 'battery-life.typ'), root=str(root),
                                 sys_inputs={'report': json.dumps(data, ensure_ascii=False)})
        except typst.TypstError as error:
            raise ValueError('Der Batteriebericht konnte nicht als PDF erstellt werden.') from error
