"""Validate the production path against generator references and the browser model."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from battery_statistics_generator import generate, statistics
from app.battery_life import combine, evaluate, normal_approximation
from app.cycle_energy import confirmed_cycle_energy


CASES = generate()


def assert_values(actual, expected):
    if isinstance(expected, dict):
        for key, value in expected.items():
            assert_values(actual[key], value)
    elif expected is None:
        assert actual is None
    else:
        np.testing.assert_allclose(actual, expected, rtol=2e-10, atol=1e-10)


def build_sources(case):
    result = []
    for source in case['sources']:
        summary = confirmed_cycle_energy(1000, source['voltage_mv'],
            *[[SimpleNamespace(**row) for row in source[key]]
              for key in ['events', 'segments', 'markers']])
        truth = np.asarray(source['truth'])
        assert summary['cycle_count'] == len(truth)
        assert summary['excluded_wake_count'] == 1  # Unclosed outlier never affects statistics.
        for phase, duration, current in [('wake', truth[:, 0], truth[:, 2]),
                                          ('sleep', truth[:, 1], truth[:, 3])]:
            for field, values in [('duration_s', duration), ('current_ua', current),
                                  ('energy_uwh', duration*current*source['voltage_mv']/1000/3600)]:
                assert_values(summary[f'{phase}_variability'][field], statistics(values))
        result.append(dict(id=source['id'], name=source['name'], weight=source['weight'], summary=summary))
    return result


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
def test_generated_measurements_against_independent_reference(case):
    data = combine(build_sources(case), case['weighting'])
    expected = case['expected']
    assert_values({key: data[key] for key in ['count', 'wake_s', 'wake_energy_uwh', 'sleep_power_uw', 'statistics']}, expected_subset(expected, ['count', 'wake_s', 'wake_energy_uwh', 'sleep_power_uw', 'statistics']))
    assert_values([s['share'] for s in data['sources']], expected['shares'])
    args = (data, case['energy_wh'], case['mode'], case['value'])
    assert_values(evaluate(*args), expected_subset(expected, ['expected_h', 'power_uw', 'sleep_s', 'duty_pct', 'scenarios_h', 'percentiles']))
    assert_values(normal_approximation(*args), expected_subset(expected, ['mean_h', 'stddev_h', 'variance_h2', 'percentiles_h']))


def expected_subset(data, keys):
    return {key: data[key] for key in keys}


def test_generated_browser_model_against_same_independent_reference():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node') or str(root / '.test-tools' / 'node.exe')
    if not Path(node).is_file():
        pytest.skip('Node required for browser model verification')
    cases = [{**case, 'sources': build_sources(case)} for case in CASES]
    result = subprocess.run([node, str(root/'tests/check_generated_battery_statistics.cjs')],
                            input=json.dumps(cases), text=True, capture_output=True, cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
