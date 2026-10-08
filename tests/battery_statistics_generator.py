"""Seeded measurement generator and independent NumPy reference, without app imports.

Run before validation: python tests/battery_statistics_generator.py --output cases.json
Columns of each truth matrix: wake seconds, sleep seconds, wake microamps,
sleep microamps. References use NumPy covariance/interpolation and NormalDist.
"""
import argparse
import json
from pathlib import Path
from statistics import NormalDist

import numpy as np


def statistics(values, weights=None):
    values = np.asarray(values, dtype=float)
    if weights is None:
        weights = np.ones(len(values)) / len(values)
    spread = float(np.cov(values, aweights=weights, ddof=1)) if len(values) > 1 else None
    if len(values) > 1 and np.all(values == values[0]):
        spread = 0.0  # Exact analytic result, independent of covariance roundoff.
    return dict(mean=float(np.average(values, weights=weights)), min=float(values.min()),
                max=float(values.max()), variance=spread,
                stddev=float(np.sqrt(spread)) if spread is not None else None)


def measurement(rng, kind, count, index):
    if kind in ('constant', 'single'):
        truth = np.tile([.25, 2, 1200, 4], (count, 1))
        voltage = 3.3
    else:
        latent = rng.normal(size=count)
        if kind == 'skewed':
            latent = rng.lognormal(0, .65, size=count) - 1
        elif kind == 'bimodal':
            latent = rng.choice([-2., 2.], size=count) + rng.normal(0, .1, size=count)
        truth = np.column_stack((rng.integers(100, 600, count)/1000,
                                 rng.integers(1000, 5000, count)/1000,
                                 1200 + 180*latent + index*220,
                                 4 + .7*latent + index))
        voltage = [3., 3.3, 3.6][index]
    if kind == 'zero_weight_outlier' and index == 2:
        truth[:, 2:] *= 1000
    # Record complete Sleep -> Wake -> Sleep cycles plus an unclosed outlier.
    events, segments, markers, cursor = [], [], [], 0
    for sequence, (wake_s, sleep_s, wake_ua, sleep_ua) in enumerate(truth, 1):
        wake = cursor + round(sleep_s*1000)
        end = wake + round(wake_s*1000)
        segments.append(dict(start_sample=cursor, end_sample=wake-1,
                             sample_count=wake-cursor, mean_ua=float(sleep_ua),
                             charge_uc=float(sleep_ua*sleep_s)))
        events.append(dict(sequence=sequence, trigger_sample=wake, end_sample=end-1,
                           mean_ua=float(wake_ua), peak_ua=float(wake_ua),
                           charge_uc=float(wake_ua*wake_s), event_kind='wake'))
        markers.extend(dict(kind=kind, sample_index=tick) for kind, tick in
                       [('sleep_start', cursor), ('sleep_validated', cursor+1),
                        ('wake_start', wake), ('wake_validated', wake+1)])
        cursor = end
    markers.extend([dict(kind='sleep_start', sample_index=cursor),
                    dict(kind='sleep_validated', sample_index=cursor+1),
                    dict(kind='wake_start', sample_index=cursor+1000),
                    dict(kind='wake_validated', sample_index=cursor+1001)])
    events.append(dict(sequence=count+1, trigger_sample=cursor+1000,
                       end_sample=cursor+1999, mean_ua=1e9, peak_ua=1e9,
                       charge_uc=1e9, event_kind='wake'))
    return dict(id=f'source-{index}', name=f'Generated {kind} {index}',
                weight=[7, 3, 0 if kind == 'zero_weight_outlier' else 2][index],
                voltage_mv=round(voltage*1000), truth=truth.tolist(),
                events=events, segments=segments, markers=markers)


def reference(sources, weighting, energy, mode, value):
    matrices = [np.asarray(s['truth']) for s in sources]
    raw = np.array([1 if weighting == 'mean' else len(m) if weighting == 'cycle_count'
                    else s['weight'] for s, m in zip(sources, matrices)], dtype=float)
    shares = raw/raw.sum()
    active = [(s, m, share) for s, m, share in zip(sources, matrices, shares) if share > 0]
    weights = np.concatenate([np.full(len(m), share/len(m)) for _, m, share in active])
    wake_s = np.concatenate([m[:, 0] for _, m, _ in active])
    sleep_s = np.concatenate([m[:, 1] for _, m, _ in active])
    wake_energy = np.concatenate([m[:, 0]*m[:, 2]*(s['voltage_mv']/1000)/3600 for s, m, _ in active])
    recorded_sleep_power = np.concatenate([m[:, 3]*(s['voltage_mv']/1000) for s, m, _ in active])
    device_currents = np.array([np.average(m[:, 3], weights=m[:, 1]) for _, m, _ in active])
    device_powers = np.array([p * s['voltage_mv']/1000 for p, (s, _, _) in zip(device_currents, active)])
    device_shares = np.array([share for _, _, share in active])
    sleep_power = np.concatenate([np.full(len(m), p) for p, (_, m, _) in zip(device_powers, active)])
    mean_wake = float(np.average(wake_s, weights=weights))
    mean_energy = float(np.average(wake_energy, weights=weights))
    mean_sleep_power = np.average(device_powers, weights=device_shares)
    planned = value if mode == 'sleep' else value-mean_wake if mode == 'period' else mean_wake*(100/value-1)
    device_durations = np.array([m[:, 0].mean() for _, m, _ in active])
    device_sleep = np.full(len(active), value) if mode == 'sleep' else value-device_durations if mode == 'period' else device_durations*(100/value-1)
    power = (mean_energy*3600+np.average(device_powers*device_sleep, weights=device_shares))/(mean_wake+planned)
    expected = energy*1e6/power
    scenario_sleep = value if mode == 'sleep' else value-wake_s if mode == 'period' else wake_s*(100/value-1)
    scenarios = energy*1e6*(wake_s+scenario_sleep)/(wake_energy*3600+sleep_power*scenario_sleep)
    order = np.argsort(scenarios, kind='stable')
    positions = np.cumsum(weights[order])-weights[order]/2
    positions /= weights.sum()
    empirical = {str(p): float(np.interp(p/100, positions, scenarios[order]))
                 for p in [0, 5, 10, 50, 90, 95, 100]}
    scatter = statistics(scenarios, weights)
    sigma = scatter['stddev']
    normal = {str(p): float(expected+NormalDist().inv_cdf(p/100)*sigma)
              if sigma is not None else None for p in [5, 10, 50, 90, 95]}
    bins = []
    if scenarios.min() != scenarios.max():
        count = min(30, max(2, int(np.ceil(np.sqrt(len(scenarios))))))
        mass, edges = np.histogram(scenarios, bins=count, weights=weights/weights.sum())
        bins = [dict(leftH=float(edges[i]), rightH=float(edges[i+1]), share=float(mass[i]))
                for i in range(count)]
    return dict(shares=shares.tolist(), count=len(wake_s), wake_s=mean_wake,
                wake_energy_uwh=mean_energy, sleep_power_uw=float(mean_sleep_power),
                statistics=dict(wake_energy=statistics(wake_energy, weights),
                                wake_duration=statistics(wake_s, weights),
                                sleep_energy=statistics(recorded_sleep_power*sleep_s/3600, weights),
                                sleep_power=statistics(device_powers, device_shares),
                                sleep_current=statistics(device_currents, device_shares)),
                expected_h=float(expected), power_uw=float(power), sleep_s=float(planned),
                duty_pct=float(100*mean_wake/(mean_wake+planned)),
                scenarios_h=scenarios.tolist(), percentiles=empirical,
                mean_h=float(expected), stddev_h=sigma, variance_h2=scatter['variance'],
                percentiles_h=normal, bins=bins)


def generate(seed=20261007):
    rng = np.random.default_rng(seed)
    cases = []
    for kind in ['normal', 'skewed', 'bimodal', 'constant', 'single', 'zero_weight_outlier']:
        counts = [1] if kind == 'single' else [41, 73, 19]
        sources = [measurement(rng, kind, n, i) for i, n in enumerate(counts)]
        for weighting in ['mean', 'cycle_count', 'custom']:
            for energy, mode, value in [(1, 'sleep', 0), (1, 'sleep', 60),
                                        (1, 'sleep', 86400), (2, 'sleep', 86400),
                                        (1, 'period', 60), (1, 'duty', 1), (1, 'duty', 100)]:
                cases.append(dict(name=f'{kind}-{weighting}-{energy}-{mode}-{value}',
                                  sources=sources, weighting=weighting, energy_wh=energy,
                                  mode=mode, value=value,
                                  expected=reference(sources, weighting, energy, mode, value)))
    return cases


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261007)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = generate(args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(seed=args.seed, cases=cases), allow_nan=False), encoding='utf-8')
    print(f'Generated {len(cases)} independent reference cases: {args.output}')
