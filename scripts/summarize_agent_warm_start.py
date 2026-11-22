"""Create machine-readable paired summaries; preserve failed/nonconverged counts."""
import argparse
import collections
import json
import hashlib
from pathlib import Path
import statistics
import numpy as np


def uncertainty(rows, fields):
    """Circular 7-date moving-block bootstrap, conditional on this locked model."""
    if not rows:
        return None
    n=len(rows)
    rng=np.random.default_rng(11)
    blocks=rng.integers(0,n,size=(10000,(n+6)//7))
    indices=((blocks[:,:,None]+np.arange(7))%n).reshape(10000,-1)[:,:n]
    result={}
    for field in fields:
        difference=np.asarray([r['agent'][field]-r['baseline'][field] for r in rows])
        draws=difference[indices].mean(axis=1)
        result[field]=dict(mean_agent_minus_baseline=float(difference.mean()),
            percentile_95_interval=list(map(float,np.quantile(draws,[.025,.975]))))
    base=np.asarray([r['baseline']['runtime_s'] for r in rows])
    agent=np.asarray([r['agent']['runtime_s'] for r in rows])
    speedups=base[indices].mean(axis=1)/agent[indices].mean(axis=1)
    result['runtime_speedup_baseline_over_agent']=dict(
        ratio_of_means=float(base.mean()/agent.mean()),
        percentile_95_interval=list(map(float,np.quantile(speedups,[.025,.975]))))
    return dict(method='circular_moving_block_bootstrap',block_dates=7,
        resamples=10000,seed=11,n_dates=n,metrics=result,
        interpretation='Historical-date variability conditional on one locked model; not training-seed or repeated-hardware timing uncertainty')


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    args=p.parse_args()
    manifest=json.loads((args.run/'manifest.json').read_text(encoding='utf-8'))
    train=read(args.run/'training.jsonl')
    validation=read(args.run/'validation.jsonl')
    test=read(args.run/'test.jsonl')
    for row in test:
        for arm in ['baseline','agent']:
            row[arm]['aging_cost_yuan']=row[arm]['aging']['aging_cost_yuan']
            row[arm]['delta_soh_total']=row[arm]['aging']['delta_soh_total']
    errors=read(args.run/'errors.jsonl')
    checkpoint=json.loads((args.run/'locked_checkpoint.json').read_text(encoding='utf-8'))
    root=Path(__file__).resolve().parents[1]
    source_integrity={path:hashlib.sha256((root/path).read_bytes()).hexdigest()==digest
        for path,digest in manifest['source_hashes'].items()}
    train_days=set(checkpoint['training_dates'])
    examples_train_only=all(row['date'] in train_days for row in checkpoint['examples'])
    paired=[r for r in test if r['baseline']['converged'] and r['agent']['converged']]
    fields=['runtime_s','requested_kwh','served_kwh','shed_kwh','revenue_yuan',
            'throughput_kwh','curtail_kwh','aging_cost_yuan','delta_soh_total','soc_end','soh_end']
    summary=dict(run_status=manifest['status'],training_dates=len(train),
        source_integrity_matches_manifest=source_integrity,
        checkpoint_examples_train_only=examples_train_only,
        checkpoint_training_examples=len(checkpoint['examples']),
        pipeline_runtime_s=manifest.get('runtime_s'),
        training_runtime_s=sum(r['runtime_s'] for r in train),
        training_label_vectors=dict(collections.Counter(str(r['prices']) for r in train)),
        validation_rows=len(validation),test_dates_attempted=len(test),
        paired_converged_dates=len(paired),
        baseline_nonconverged=sum(not r['baseline']['converged'] for r in test),
        agent_nonconverged=sum(not r['agent']['converged'] for r in test),
        errors_by_phase=dict(collections.Counter(r['phase'] for r in errors)),
        errors=errors,performance_means_paired_converged_only={
            arm:{key:statistics.mean(r[arm][key] for r in paired) if paired else None
                 for key in fields} for arm in ['baseline','agent']},
        paired_mean_agent_minus_baseline={key:statistics.mean(
            r['agent'][key]-r['baseline'][key] for r in paired) if paired else None
            for key in fields},
        selected_neighbors=checkpoint['neighbors'],
        locked_at=checkpoint['locked_at'],checkpoint_sha256=manifest.get('checkpoint_sha256'),
        validation_mean_teacher_profit_gap=checkpoint['validation_mean_teacher_profit_gap'],
        uncertainty_paired_converged=uncertainty(paired,fields),
        runtime_uncertainty_all_completed_dates=uncertainty(test,['runtime_s']),
        strict_gate=dict(price_demand_converged=True,forecast_price_residual_max=1e-3,
            nonconverged_dates=[r['date'] for r in test if r not in paired]),
        thermal_limits_verified=all(d['power_flow']['thermal_limits_verified']
            for r in test for arm in ['baseline','agent'] for d in r[arm]['decisions']),
        caveats=['Supervised imitation-trained retrieval agent, not reinforcement learning.',
            'Only noon short windows; no full-day/lifetime generalization claim.',
            'One deterministic training seed; runtime variation is not independently replicated.',
            'Weather generation is an engineering proxy; spatial site matching is unverified.',
            'Smoke QA ran 2021-11-01 under a separate locked tiny checkpoint before the full run; no result-driven hyperparameter changes.'])
    output=args.run/'summary.json'
    output.write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2,allow_nan=False))


if __name__=='__main__': main()
