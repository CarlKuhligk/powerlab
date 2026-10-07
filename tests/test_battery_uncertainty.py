import json
import math
import shutil
import subprocess
from pathlib import Path
from statistics import variance

import pytest
import numpy as np

from app.battery_life import combine, profile, uncertainty


def summary(powers=(1, 2, 3, 4), wake=(1, 1, 1, 1), durations=None):
    durations = durations or [10] * len(powers)
    return {'voltage_v': 3.3,
            'valid_wake_phases': [{'sequence': i, 'duration_s': 1, 'energy_uwh': w}
                                  for i, w in enumerate(wake)],
            'valid_sleep_phases': [{'following_wake_sequence': i, 'duration_s': t,
                                   'energy_uwh': p * t / 3600}
                                  for i, (p, t) in enumerate(zip(powers, durations))]}


def test_sleep_variance_propagates_to_mean_at_fixed_timing():
    d = profile(summary())
    u = uncertainty(d, 1, 'sleep', 20)
    expected = (20 / (3600 + 2.5 * 20))**2 * variance([1, 2, 3, 4]) / 4
    assert u['log_variance'] == pytest.approx(expected)
    assert u['standard_error_h'] == pytest.approx(u['center_h'] * math.sqrt(expected))
    assert u['percentiles_h']['5'] == pytest.approx(u['center_h'] * math.exp(-1.6448536269514722 * math.sqrt(expected)))
    assert uncertainty(d, 1, 'sleep', 0)['log_variance'] == 0
    doubled = uncertainty(d, 2, 'sleep', 20)
    assert doubled['standard_error_h'] == pytest.approx(2 * u['standard_error_h'])
    assert doubled['log_variance'] == pytest.approx(expected)


def test_more_independent_observations_reduce_mean_uncertainty():
    a = uncertainty(profile(summary()), 1, 'sleep', 20)
    b = uncertainty(profile(summary((1, 2, 3, 4) * 10, (1,) * 40)), 1, 'sleep', 20)
    assert b['center_h'] == pytest.approx(a['center_h'])
    assert b['log_variance'] == pytest.approx(a['log_variance'] * 3 / 39)


def test_paired_covariance_can_cancel_consumption_scatter():
    # At t=3600, each pair has the same total energy despite varying phases.
    u = uncertainty(profile(summary(wake=(4, 3, 2, 1))), 1, 'sleep', 3600)
    assert u['standard_error_h'] == pytest.approx(0, abs=1e-10)


def test_fixed_measurement_shares_combine_variance_with_squared_shares():
    s = summary()
    a = uncertainty(profile(s), 1, 'sleep', 20)
    d = combine([{'id': 'a', 'summary': s, 'weight': 3}, {'id': 'b', 'summary': s, 'weight': 1}], 'custom')
    b = uncertainty(d, 1, 'sleep', 20)
    assert b['log_variance'] == pytest.approx(a['log_variance'] * (.75**2 + .25**2))
    single = summary((2,), (1,))
    d = combine([{'id': 'a', 'summary': s, 'weight': 1}, {'id': 'b', 'summary': single, 'weight': 0}], 'custom')
    assert uncertainty(d, 1, 'sleep', 20)['available']
    d = combine([{'id': 'a', 'summary': s, 'weight': 1}, {'id': 'b', 'summary': single, 'weight': 1}], 'custom')
    assert not uncertainty(d, 1, 'sleep', 20)['available']


def test_single_constant_and_zero_consumption():
    assert not uncertainty(profile(summary((2,), (1,))), 1, 'sleep', 20)['available']
    u = uncertainty(profile(summary((2,) * 4)), 1, 'sleep', 20)
    assert u['standard_error_h'] == 0
    assert u['percentiles_h']['5'] == u['percentiles_h']['95']
    assert not uncertainty(profile(summary((0,) * 4, (0,) * 4)), 1, 'sleep', 20)['available']


@pytest.mark.parametrize('mode,value', [('sleep', 17), ('duty', 3), ('duty', 100)])
def test_uncertainty_matches_independent_numerical_gradient_and_covariance(mode, value):
    # Independent reference: differentiate the entire aggregate ratio, then
    # propagate raw four-variable covariance matrices via NumPy.
    raw = [np.array([[1, 1, .1, 2], [2, 3, .2, 5], [4, 2, .15, 10], [3, 4, .4, 8]]),
           np.array([[2, 2, .3, 4], [3, 1, .2, 3], [1, 4, .4, 7]])]
    shares = np.array([.7, .3])
    means = np.array([rows.mean(axis=0) for rows in raw])
    def log_runtime(m):
        w = shares @ m[:, 0]
        duration = shares @ m[:, 1]
        sleep_power = shares @ (m[:, 2] * 3600 / m[:, 3])
        t = value if mode == 'sleep' else duration * (100 / value - 1)
        return np.log(1e6 * (duration + t) / (3600 * w + sleep_power * t))
    reference = 0
    for j, rows in enumerate(raw):
        gradient = []
        for k in range(4):
            h = means[j, k] * 1e-5
            plus, minus = means.copy(), means.copy()
            plus[j, k] += h
            minus[j, k] -= h
            gradient.append((log_runtime(plus) - log_runtime(minus)) / (2 * h))
        gradient = np.array(gradient)
        reference += gradient @ np.cov(rows.T, ddof=1) @ gradient / len(rows)
    sources = []
    for j, rows in enumerate(raw):
        s = {'voltage_v': 3.3,
             'valid_wake_phases': [{'sequence': i, 'energy_uwh': r[0], 'duration_s': r[1]} for i, r in enumerate(rows)],
             'valid_sleep_phases': [{'following_wake_sequence': i, 'energy_uwh': r[2], 'duration_s': r[3]} for i, r in enumerate(rows)]}
        sources.append({'id': str(j), 'weight': float(shares[j]), 'summary': s})
    estimate = uncertainty(combine(sources, 'custom'), 1, mode, value)
    assert estimate['log_variance'] == pytest.approx(reference, rel=1e-8)
    assert estimate['center_h'] == pytest.approx(math.exp(log_runtime(means)))


@pytest.mark.parametrize('mode,value', [('sleep', 0), ('sleep', 20), ('sleep', 86400), ('duty', 1), ('duty', 100)])
def test_browser_and_pdf_agree_for_duration_weighted_sleep_power(mode, value):
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node') or str(root / '.test-tools/node.exe')
    if not Path(node).exists():
        pytest.skip('Node.js unavailable')
    sources = [{'id': 'a', 'summary': summary(durations=[1, 10, 5, 30]), 'weight': 3},
               {'id': 'b', 'summary': summary((2, 4, 5, 7), (2, 4, 1, 3)), 'weight': 1}]
    code = """const fs=require('fs'),vm=require('vm');const c=vm.createContext({});
vm.runInContext(fs.readFileSync('app/static/battery-life.js','utf8')+';globalThis.m=BatteryLifeModel',c);
const p=JSON.parse(fs.readFileSync(0,'utf8')); const u=c.m.uncertainty(c.m.combine(p.sources,'custom'),1,p.mode,p.value);
console.log(JSON.stringify({center:u.point.expectedH,se:u.standardErrorH,v:u.logVariance,lo:u.point.p05H,hi:u.point.p95H}));"""
    output = subprocess.run([node, '-e', code], cwd=root, input=json.dumps({'sources': sources, 'mode': mode, 'value': value}), text=True, capture_output=True, check=True)
    js = json.loads(output.stdout)
    py = uncertainty(combine(sources, 'custom'), 1, mode, value)
    assert js == pytest.approx({'center': py['center_h'], 'se': py['standard_error_h'], 'v': py['log_variance'],
                              'lo': py['percentiles_h']['5'], 'hi': py['percentiles_h']['95']})
