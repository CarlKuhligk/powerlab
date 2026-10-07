"""Battery lifetime scenarios from paired observed cycles, without distribution fitting."""
import math
from statistics import mean, variance


def percentile(values, fraction):
    ordered = sorted(values)
    at = (len(ordered) - 1) * fraction
    lower, upper = ordered[math.floor(at)], ordered[math.ceil(at)]
    if lower == upper:
        return lower
    return lower + (upper - lower) * (at - math.floor(at))


def weighted_percentile(values, weights, fraction):
    """Interpolate at cumulative probability midpoints; zero weights are excluded."""
    ordered = sorted((v, w) for v, w in zip(values, weights) if w > 0)
    total = sum(w for _, w in ordered)
    positions, accumulated = [], 0
    for value, weight in ordered:
        positions.append(((accumulated + weight / 2) / total, value))
        accumulated += weight
    if fraction <= positions[0][0]:
        return positions[0][1]
    for (left, lower), (right, upper) in zip(positions, positions[1:]):
        if fraction <= right:
            return lower if lower == upper else lower + (upper - lower) * (fraction - left) / (right - left)
    return positions[-1][1]


def weighted_statistics(values, weights):
    # Center before summing so identical observations retain exactly zero spread.
    origin = values[0]
    average = origin + math.fsum((v - origin) * w for v, w in zip(values, weights))
    correction = 1 - sum(w * w for w in weights)
    spread = sum(w * (v - average)**2 for v, w in zip(values, weights)) / correction if correction > 1e-15 else None
    return {'mean': average, 'min': min(values), 'max': max(values), 'variance': spread,
            'stddev': math.sqrt(spread) if spread is not None else None}


def combine(sources, weighting='mean'):
    """Measurement shares, distributed equally among each measurement's valid cycles."""
    if weighting not in {'mean', 'cycle_count', 'custom'} or not sources:
        raise ValueError('Messungen und gültige Gewichtungsmethode erforderlich.')
    if len({s['id'] for s in sources}) != len(sources):
        raise ValueError('Eine Messung darf nur einmal ausgewählt werden.')
    prepared = []
    for source in sources:
        data = profile(source['summary'])
        weight = 1 if weighting == 'mean' else data['count'] if weighting == 'cycle_count' else source.get('weight', 1)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError('Gewichte müssen endlich und nichtnegativ sein.')
        prepared.append((source, data, weight))
    total = sum(weight for _, _, weight in prepared)
    if total <= 0:
        raise ValueError('Mindestens eine Messung muss ein positives Gewicht haben.')
    samples, weights, details = [], [], []
    for source, data, weight in prepared:
        share = weight / total
        details.append({'id': source['id'], 'name': source.get('name', source['id']),
                        'count': data['count'], 'weight': weight, 'share': share,
                        'voltage_v': source['summary']['voltage_v']})
        if share:
            samples.extend(data['samples'])
            weights.extend([share / data['count']] * data['count'])
    return {'samples': samples, 'weights': weights, 'count': len(samples), 'sources': details,
            'groups': [{'data': d, 'share': w / total} for _, d, w in prepared if w > 0],
            'weighting': weighting, 'active_measurement_count': sum(d['share'] > 0 for d in details),
            'wake_s': sum(d['wake_s'] * w / total for _, d, w in prepared),
            'wake_energy_uwh': sum(d['wake_energy_uwh'] * w / total for _, d, w in prepared),
            'sleep_power_uw': sum(d['sleep_power_uw'] * w / total for _, d, w in prepared),
            'statistics': {key: weighted_statistics([s[field] for s in samples], weights)
                           for key, field in [('wake_energy', 'wake_energy_uwh'),
                                              ('sleep_energy', 'sleep_energy_uwh'),
                                              ('sleep_power', 'sleep_power_uw')]}}


def profile(summary):
    sleeps = {p['following_wake_sequence']: p for p in summary['valid_sleep_phases']}
    samples = []
    for wake in summary['valid_wake_phases']:
        sleep = sleeps.get(wake['sequence'])
        if (sleep is None or not all(math.isfinite(v) for v in
                [wake['duration_s'], sleep['duration_s'], wake['energy_uwh'], sleep['energy_uwh']])
                or min(wake['duration_s'], sleep['duration_s']) <= 0
                or min(wake['energy_uwh'], sleep['energy_uwh']) < 0):
            raise ValueError('Gültige Sleep-/Wake-Zyklen mit nichtnegativem Energieverbrauch erforderlich.')
        samples.append({'wake_s': wake['duration_s'], 'sleep_s': sleep['duration_s'],
                        'wake_energy_uwh': wake['energy_uwh'], 'sleep_energy_uwh': sleep['energy_uwh'],
                        'sleep_power_uw': sleep['energy_uwh'] * 3600 / sleep['duration_s']})
    if not samples:
        raise ValueError('Diese Messung enthält keinen vollständig gültigen Sleep → Wake → Sleep-Zyklus.')
    return {'samples': samples, 'count': len(samples),
            'wake_s': mean(s['wake_s'] for s in samples),
            'wake_energy_uwh': mean(s['wake_energy_uwh'] for s in samples),
            'sleep_power_uw': sum(s['sleep_energy_uwh'] for s in samples) * 3600 / sum(s['sleep_s'] for s in samples)}


def evaluate(data, energy_wh, mode, value):
    if not math.isfinite(energy_wh) or energy_wh <= 0:
        raise ValueError('Positive nutzbare Batterieenergie erforderlich.')
    if (mode not in {'sleep', 'duty'} or not math.isfinite(value) or value < 0
            or (mode == 'duty' and not 0 < value <= 100)):
        raise ValueError('Sleep-Dauer mindestens 0; Duty-Cycle über 0 bis 100 %.')
    sleep_s = value if mode == 'sleep' else data['wake_s'] * (100 / value - 1)
    power = (data['wake_energy_uwh'] * 3600 + data['sleep_power_uw'] * sleep_s) / (data['wake_s'] + sleep_s)
    def hours(power_uw):
        return energy_wh * 1e6 / power_uw if power_uw else math.inf
    scenarios = []
    for s in data['samples']:
        planned = value if mode == 'sleep' else s['wake_s'] * (100 / value - 1)
        scenarios.append(hours((s['wake_energy_uwh'] * 3600 + s['sleep_power_uw'] * planned) / (s['wake_s'] + planned)))
    return {'expected_h': hours(power), 'power_uw': power, 'sleep_s': sleep_s, 'scenarios_h': scenarios,
            'duty_pct': 100 * data['wake_s'] / (data['wake_s'] + sleep_s),
            'percentiles': {str(p): (weighted_percentile(scenarios, data['weights'], p / 100)
                                     if 'weights' in data else percentile(scenarios, p / 100))
                            for p in [0, 5, 10, 50, 90, 95, 100]}}


def uncertainty(data, energy_wh, mode, value):
    """Delta-method uncertainty of log(runtime), stratified by measurement.

    Cycles and measurements are assumed independent; paired sleep/wake
    covariance and the duration-weighted sleep-power ratio are retained.
    Battery energy, timing and measurement shares are treated as fixed.
    """
    point = evaluate(data, energy_wh, mode, value)
    center = point['expected_h']
    groups = data.get('groups', [{'data': data, 'share': 1}])
    result = {'center_h': center, 'standard_error_h': None, 'log_variance': None,
              'available': False, 'percentiles_h': {}, 'reason': ''}
    if not math.isfinite(center):
        result['reason'] = 'Kein mittlerer Verbrauch: keine endliche Laufzeitschätzung.'
        return result
    if any(g['data']['count'] < 2 for g in groups):
        result['reason'] = 'Mindestens zwei gültige Zyklen je Messung mit positivem Einfluss erforderlich.'
        return result
    t = point['sleep_s']
    numerator = 3600 * data['wake_energy_uwh'] + data['sleep_power_uw'] * t
    gw, gs = -3600 / numerator, -t / numerator
    gd = (1 / (data['wake_s'] + t) if mode == 'sleep' else
          1 / data['wake_s'] - data['sleep_power_uw'] * (100 / value - 1) / numerator)
    log_variance = 0
    for group in groups:
        d = group['data']
        mean_sleep = mean(s['sleep_s'] for s in d['samples'])
        influences = [gw * (s['wake_energy_uwh'] - d['wake_energy_uwh']) +
                      gd * (s['wake_s'] - d['wake_s']) +
                      gs * (s['sleep_energy_uwh'] * 3600 - d['sleep_power_uw'] * s['sleep_s']) / mean_sleep
                      for s in d['samples']]
        log_variance += group['share']**2 * variance(influences) / d['count']
    sigma = math.sqrt(max(0, log_variance))
    z = {5: -1.6448536269514722, 10: -1.2815515655446004, 50: 0,
         90: 1.2815515655446004, 95: 1.6448536269514722}
    try:
        percentiles = {str(p): center * math.exp(v * sigma) for p, v in z.items()}
        chart_max = center * math.exp(4 * sigma)
    except OverflowError:
        chart_max = math.inf
    if not math.isfinite(chart_max):
        result['reason'] = 'Unsicherheit zu groß für eine endliche Näherung.'
        return result
    result.update(available=True, standard_error_h=center * sigma,
                  log_variance=log_variance, percentiles_h=percentiles)
    return result


def normal_approximation(data, energy_wh, mode, value):
    point = evaluate(data, energy_wh, mode, value)
    if not all(math.isfinite(v) for v in point['scenarios_h']):
        return {'mean_h': None, 'stddev_h': None, 'variance_h2': None, 'percentiles_h': {}}
    stats = weighted_statistics(point['scenarios_h'], data.get('weights', [1 / data['count']] * data['count']))
    z = {5: -1.6448536269514722, 10: -1.2815515655446004, 50: 0,
         90: 1.2815515655446004, 95: 1.6448536269514722}
    return {'mean_h': point['expected_h'], 'stddev_h': stats['stddev'], 'variance_h2': stats['variance'],
            'percentiles_h': {str(p): point['expected_h'] + value * stats['stddev']
                              if stats['stddev'] is not None else None for p, value in z.items()}}


def measured_statistics(summary):
    data = profile(summary)
    values = [s['sleep_power_uw'] for s in data['samples']]
    return {'sleep_energy': summary['sleep_variability']['energy_uwh'],
            'wake_energy': summary['wake_variability']['energy_uwh'],
            'sleep_power': {'mean': mean(values), 'variance': variance(values) if len(values) > 1 else None,
                            'stddev': math.sqrt(variance(values)) if len(values) > 1 else None,
                            'min': min(values), 'max': max(values)}}
