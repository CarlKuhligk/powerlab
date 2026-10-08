"""Device sleep means, paired Wake observations and hierarchical runtime uncertainty."""
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
                        'sleep_current_ua': data['sleep_current_ua'],
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
                                              ('wake_duration', 'wake_s'),
                                              ('sleep_energy', 'sleep_energy_uwh')]}
                          | {'sleep_power': weighted_statistics(
                              [d['sleep_power_uw'] for _, d, w in prepared if w > 0],
                              [w / total for _, _, w in prepared if w > 0]),
                             'sleep_current': weighted_statistics(
                              [d['sleep_current_ua'] for _, d, w in prepared if w > 0],
                              [w / total for _, _, w in prepared if w > 0])}}


def profile(summary):
    if not math.isfinite(summary['voltage_v']) or summary['voltage_v'] <= 0:
        raise ValueError('Positive Messspannung für die Stromberechnung erforderlich.')
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
    origin = samples[0]['sleep_power_uw']
    sleep_power = origin + math.fsum((s['sleep_power_uw'] - origin) * s['sleep_s'] for s in samples) / math.fsum(s['sleep_s'] for s in samples)
    for sample in samples:
        sample['sleep_power_uw'] = sleep_power
    return {'samples': samples, 'count': len(samples),
            'wake_s': mean(s['wake_s'] for s in samples),
            'wake_energy_uwh': mean(s['wake_energy_uwh'] for s in samples),
            'sleep_power_uw': sleep_power, 'sleep_current_ua': sleep_power / summary['voltage_v']}


def evaluate(data, energy_wh, mode, value):
    if not math.isfinite(energy_wh) or energy_wh <= 0:
        raise ValueError('Positive nutzbare Batterieenergie erforderlich.')
    if (mode not in {'sleep', 'duty', 'period'} or not math.isfinite(value) or value < 0
            or (mode == 'duty' and not 0 < value <= 100)):
        raise ValueError('Sleep-Dauer mindestens 0; Duty-Cycle über 0 bis 100 %; positive Periodendauer.')
    if mode == 'period' and (value <= 0 or any(s['wake_s'] > value for s in data['samples'])):
        raise ValueError('Periodendauer muss mindestens so lang wie jede gültige Wake-Phase sein.')
    sleep_s = (value if mode == 'sleep' else value - data['wake_s'] if mode == 'period'
               else data['wake_s'] * (100 / value - 1))
    groups = data.get('groups', [{'data': data, 'share': 1}])
    def planned_sleep(d):
        return (value if mode == 'sleep' else value - d['wake_s'] if mode == 'period'
                else d['wake_s'] * (100 / value - 1))
    cycle_energy = sum(g['share'] * (g['data']['wake_energy_uwh'] * 3600 +
                       g['data']['sleep_power_uw'] * planned_sleep(g['data'])) for g in groups)
    power = cycle_energy / (data['wake_s'] + sleep_s)
    def hours(power_uw):
        return energy_wh * 1e6 / power_uw if power_uw else math.inf
    scenarios = []
    for s in data['samples']:
        planned = (value if mode == 'sleep' else value - s['wake_s'] if mode == 'period'
                   else s['wake_s'] * (100 / value - 1))
        scenarios.append(hours((s['wake_energy_uwh'] * 3600 + s['sleep_power_uw'] * planned) / (s['wake_s'] + planned)))
    return {'expected_h': hours(power), 'power_uw': power, 'sleep_s': sleep_s, 'scenarios_h': scenarios,
            'cycle_energy_uws': cycle_energy,
            'duty_pct': 100 * data['wake_s'] / (data['wake_s'] + sleep_s),
            'percentiles': {str(p): (weighted_percentile(scenarios, data['weights'], p / 100)
                                     if 'weights' in data else percentile(scenarios, p / 100))
                            for p in [0, 5, 10, 50, 90, 95, 100]}}


def device_distribution(data, energy_wh, mode, value):
    """Observed device mean runtimes; not a predictive or confidence interval."""
    groups = data.get('groups', [{'data': data, 'share': 1}])
    values = [evaluate(g['data'], energy_wh, mode, value)['expected_h'] for g in groups]
    result = {'available': False, 'count': len(groups), 'runtimes_h': values,
              'stddev_h': None, 'variance_h2': None, 'percentiles_h': {}, 'reason': ''}
    if len(groups) < 2:
        result['reason'] = 'Mindestens zwei Geräte mit positivem Einfluss für die Gerätestreuung erforderlich.'
    elif not all(math.isfinite(v) for v in values):
        result['reason'] = 'Mindestens ein Gerät hat keine endliche Laufzeit im Modell.'
    else:
        weights = [g['share'] for g in groups]
        stats = weighted_statistics(values, weights)
        if stats['stddev'] is None:
            result['reason'] = 'Gerätegewichte erlauben keine numerisch stabile Streuungsschätzung.'
            return result
        result.update(available=True, stddev_h=stats['stddev'], variance_h2=stats['variance'],
                      percentiles_h={str(p): weighted_percentile(values, weights, p / 100)
                                     for p in [5, 50, 95]})
    return result


def uncertainty(data, energy_wh, mode, value):
    """Hierarchical delta-method uncertainty of log(runtime).

    Each measurement is a device. Sleep power is fixed at its duration-weighted
    mean. Paired Wake energy/duration covariance gives within-device uncertainty.
    Between-device variance is corrected for that finite-cycle estimation noise,
    then propagated using squared device shares. This is a random-device model;
    battery energy, timing and measurement shares are treated as fixed.
    """
    point = evaluate(data, energy_wh, mode, value)
    center = point['expected_h']
    groups = data.get('groups', [{'data': data, 'share': 1}])
    result = {'center_h': center, 'standard_error_h': None, 'log_variance': None,
              'available': False, 'percentiles_h': {}, 'reason': '',
              'within_log_variance': None, 'between_log_variance': None,
              'device_log_variance': None}
    if not math.isfinite(center):
        result['reason'] = 'Kein mittlerer Verbrauch: keine endliche Laufzeitschätzung.'
        return result
    if any(g['data']['count'] < 2 for g in groups):
        result['reason'] = 'Mindestens zwei gültige Zyklen je Messung mit positivem Einfluss erforderlich.'
        return result
    t = point['sleep_s']
    numerator = point['cycle_energy_uws']
    gw = -3600 / numerator
    within_variances, device_influences, shares = [], [], []
    for group in groups:
        d = group['data']
        gd = (d['sleep_power_uw'] / numerator if mode == 'period' else
              1 / (data['wake_s'] + t) if mode == 'sleep' else
              1 / data['wake_s'] - d['sleep_power_uw'] * (100 / value - 1) / numerator)
        influences = [gw * (s['wake_energy_uwh'] - d['wake_energy_uwh']) +
                      gd * (s['wake_s'] - d['wake_s'])
                      for s in d['samples']]
        within_variances.append(variance(influences) / d['count'])
        shares.append(group['share'])
        device_point = evaluate(d, energy_wh, mode, value)
        device_influences.append(-(device_point['cycle_energy_uws'] - numerator) / numerator +
                                 (d['wake_s'] + device_point['sleep_s'] - data['wake_s'] - t) /
                                 (data['wake_s'] + t))
    squared_shares = sum(w * w for w in shares)
    if len(groups) > 1 and 1 - squared_shares <= 1e-15:
        result['reason'] = 'Gerätegewichte erlauben keine numerisch stabile Streuungsschätzung.'
        return result
    within = sum(w * w * v for w, v in zip(shares, within_variances))
    device_variance = None
    if len(groups) > 1:
        observed = weighted_statistics(device_influences, shares)['variance']
        noise = sum(w * (1 - w) * v for w, v in zip(shares, within_variances)) / (1 - squared_shares)
        device_variance = max(0, observed - noise)
    between = (device_variance or 0) * squared_shares
    log_variance = within + between
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
                  log_variance=log_variance, percentiles_h=percentiles,
                  within_log_variance=within, between_log_variance=between,
                  device_log_variance=device_variance)
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
    return {'sleep_energy': summary['sleep_variability']['energy_uwh'],
            'wake_energy': summary['wake_variability']['energy_uwh'],
            'wake_duration': weighted_statistics([s['wake_s'] for s in data['samples']],
                                                [1 / data['count']] * data['count']),
            'sleep_current': weighted_statistics([data['sleep_current_ua']], [1]),
            'sleep_power': weighted_statistics([data['sleep_power_uw']], [1])}
