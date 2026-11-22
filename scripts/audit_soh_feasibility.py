"""Audit SOH coupling and state continuity from immutable experiment ledgers.

Reads existing records only; does not resume a job or change acceptance gates.
"""
import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path


def audit(directory):
    root = Path(__file__).resolve().parents[1]
    cost = json.loads(subprocess.check_output([
        sys.executable, str(root / 'scripts/audit_continuous_cost_safety.py'),
        str(directory)], text=True))
    report = {'snapshot_date_cst': '2026-11-18',
              'cost_and_constraint_audit': cost, 'arms': {}}
    for arm in ('fixed_price_bess', 'dynamic_fixed_cost', 'soh_aware'):
        path = directory / f'{arm}.json'
        run = json.loads(path.read_text(encoding='utf8'))
        days = run['days']
        accepted = [d for d in days if d['status'] == 'diagnostic_completed']
        chain_errors = []
        for previous, current in zip(days, days[1:]):
            if previous['output_state'] != current['input_state']:
                chain_errors.append(current['date'] + ': BatteryState')
            if abs(previous['summary']['soc_end'] - current['input_soc']) > 1e-12:
                chain_errors.append(current['date'] + ': SOC')
        max_income_error = max(abs(
            step['revenue_yuan'] - step['price'] * step['served_kwh'])
            for d in days for step in d['steps'])
        capacity_error = max(abs(d['summary']['usable_capacity_start_kwh'] -
            2000 * d['summary']['soh_start']) for d in days)
        max_coefficient_error = 0.0
        for d in days:
            expected = run['initial_matched_marginal_cost_yuan_per_kwh']
            if arm == 'soh_aware':
                h = d['summary']['soh_start']
                expected = 2000000 * 1.5e-8 * math.sqrt(0.5) / 0.30 * (
                    1 + 0.5 * (1 - (h - 0.70) / 0.30))
            max_coefficient_error = max(max_coefficient_error, abs(expected -
                d['summary']['initial_marginal_degradation_cost_yuan_per_kwh']))
        failed = []
        for d in days:
            if d['status'] == 'diagnostic_completed':
                continue
            failed.append({'date': d['date'], 'status': d['status'],
                'energy_identity_residual_kwh': d['summary']['energy_identity_residual_kwh'],
                'nonconverged_steps': d['summary']['nonconverged_steps'],
                'nonconverged_timestamps': [s['timestamp'] for s in d['steps']
                    if not s['bri_coordinate_no_improvement']]})
        report['arms'][arm] = {
            'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'status': run['status'], 'accepted_days': len(accepted),
            'recorded_days': len(days), 'failed_days': failed,
            'state_chain_errors': chain_errors,
            'max_income_identity_error_yuan': max_income_error,
            'max_capacity_identity_error_kwh': capacity_error,
            'max_marginal_coefficient_formula_error': max_coefficient_error,
            'accepted_start_soh': accepted[0]['summary']['soh_start'],
            'accepted_end_soh': accepted[-1]['summary']['soh_end_posthoc'],
            'accepted_first_coefficient': accepted[0]['summary'][
                'initial_marginal_degradation_cost_yuan_per_kwh'],
            'accepted_last_coefficient': accepted[-1]['summary'][
                'initial_marginal_degradation_cost_yuan_per_kwh'],
            'accepted_end_capacity_kwh': accepted[-1]['summary'][
                'usable_capacity_end_kwh'],
        }
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run_directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run_directory)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps(result, indent=2))
