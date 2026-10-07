"""Verify a previously generated case file and write a reviewable result report.

python tests/verify_battery_statistics.py --cases cases.json --report results.md
Requires the application's dev environment (NumPy, pytest) and Node.js.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_generated_battery_statistics import build_sources, test_generated_measurements_against_independent_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    generated = json.loads(args.cases.read_text(encoding='utf-8'))
    cases = generated['cases']
    for case in cases:
        test_generated_measurements_against_independent_reference(case)
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node') or str(root / '.test-tools' / 'node.exe')
    browser = [{**case, 'sources': build_sources(case)} for case in cases]
    result = subprocess.run([node, str(root/'tests/check_generated_battery_statistics.cjs')],
                            input=json.dumps(browser), text=True, capture_output=True, cwd=root)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    lines = ['# Generator verification of battery statistics', '',
             f'Seed: {generated["seed"]}. Passed: {len(cases)} Python cases.',
             result.stdout.strip(), '',
             'Independent references: NumPy averages, sample covariance (ddof=1),',
             'interpolation at weighted cumulative midpoints, and NormalDist inverse CDF.',
             'Numeric tolerance: relative 2e-10, absolute 1e-10.', '',
             'Validated the full record-to-statistics path, including rejection of an',
             'unclosed high-consumption Wake event in every generated measurement.',
             'Covered normal, skewed, bimodal, constant and single-cycle inputs,',
             'zero-weight outliers, all three weighting methods, Sleep 0/60/86400 seconds,',
             'Duty 1/100 percent and doubled battery capacity.', '',
             'The JavaScript check verifies histogram bins against NumPy, total mass 100%,',
             'and no tails beyond observed scenario bounds. Empirical P5-P95 is not a',
             'calibrated 90% probability of actual battery lifetime.', '',
             'Corrected: floating-point accumulation produced artificial nonzero scatter',
             'for identical observations. Centering before accumulation preserves zero variance.', '',
             '## Example reference results: 1 Wh, 24 h Sleep, custom weights', '',
             '| Generated inputs | Expected days | Std. dev. days | P5 days | P95 days |',
             '| --- | ---: | ---: | ---: | ---: |']
    for case in cases:
        if case['weighting'] != 'custom' or case['energy_wh'] != 1 or case['mode'] != 'sleep' or case['value'] != 86400:
            continue
        e = case['expected']
        row = [case['name'].split('-custom')[0], e['expected_h'], e['stddev_h'],
               e['percentiles_h']['5'], e['percentiles_h']['95']]
        lines.append('| ' + ' | '.join([row[0]] + ['undefined' if v is None else f'{v/24:.6f}' for v in row[1:]]) + ' |')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f'{len(cases)} Python cases passed. {result.stdout.strip()}')
    print(f'Report: {args.report}')


if __name__ == '__main__':
    main()
