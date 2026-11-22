"""Chronological supervised solver-assistant experiment with locked held-out test.

Scope: one 2-hour forecast at noon per training/validation date, and two
half-hour rolling steps at noon per test date. This is not full-day training.
All labels use the existing SOH-aware BRI, and test uses unchanged DC execution.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_short_window_comparison import build_trace, sha256
from src.game.agent_warm_start import PriceWarmStartAgent, case_features
from src.game.bri import solve_equilibrium_bri
import src.game.dynamic_dc as dynamic
from src.game.dc_microgrid import DCSOCConfig
from src.game.forecast import ForecastBundle


def dump(path, payload):
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def controller(soh=0.8, soc=0.5):
    c = dynamic.DynamicDCSFSEController(
        horizon_hours=2, max_sweeps=2, ev_service_limit_kw=480,
        soc_config=DCSOCConfig(capacity_kwh=2000, power_kw=800,
                               soc_min=0.1, soc_max=0.9, soc_reserve=0.2),
        use_soh_in_game=True, use_soh_capacity_in_game=True,
        price_demand_max_iter=12, price_forecast_relaxation=0.5,
        adaptive_price_forecast_relaxation=True)
    c.reset(soc=soc, soh=soh)
    return c


def hourly(steps, hour):
    return next(row for row in steps if datetime.fromisoformat(row['timestamp']).hour == hour)


def training_case(steps, index):
    # Fixed ex ante coverage of four SOHs and three SOCs; no test-derived choices.
    c = controller([1.0, 0.9, 0.8, 0.7][index % 4], [0.3, 0.5, 0.7][index % 3])
    rows = [hourly(steps, h) for h in (12, 13)]
    return c._build_case(*[[r[key] for r in rows] for key in
                          ('pv_kw', 'wind_kw', 'ev_requested_kw', 'base_load_kw')],
                         dt_hours=0.5, demand_forecast_price=1.4)


@contextmanager
def assisted_solver(agent):
    """Process-local hook; never write or alter the baseline source module."""
    original = dynamic.solve_equilibrium_bri
    def assisted(case, **kwargs):
        return agent.solve(case, original, **kwargs)
    dynamic.solve_equilibrium_bri = assisted
    try:
        yield
    finally:
        dynamic.solve_equilibrium_bri = original


def replay(steps, agent=None):
    c = controller()
    decisions = []
    started = time.perf_counter()
    def run():
        for half in (0, 1):
            rows = [hourly(steps, h) for h in (12, 13)]
            ts = datetime.fromisoformat(rows[0]['timestamp']).replace(minute=half * 30)
            b = ForecastBundle(timestamp=ts, horizon_hours=2,
                 pv_forecast_kw=[r['pv_kw'] for r in rows],
                 wind_forecast_kw=[r['wind_kw'] for r in rows],
                 base_ev_load_forecast_kw=[r['ev_requested_kw'] for r in rows],
                 non_ev_load_forecast_kw=[r['base_load_kw'] for r in rows],
                 current_ev_load_kw=rows[0]['ev_requested_kw'],
                 current_ev_elapsed_hours=half * 0.5,
                 soc_current=c.soc.soc, soh_current=c.soh)
            decisions.append(c.step(b))
    if agent:
        with assisted_solver(agent):
            run()
    else:
        run()
    runtime = time.perf_counter() - started
    aging = c.finalize_day([0.5] + [d.soc for d in decisions],
                           [d.storage_kw for d in decisions], interval_hours=0.5)
    return dict(runtime_s=runtime, requested_kwh=sum(d.ev_requested_kw * .5 for d in decisions),
        served_kwh=sum(d.ev_served_kw * .5 for d in decisions),
        shed_kwh=sum(d.ev_shed_kw * .5 for d in decisions),
        revenue_yuan=sum(d.price * d.ev_served_kw * .5 for d in decisions),
        throughput_kwh=sum(abs(d.storage_kw) * .5 for d in decisions),
        curtail_kwh=sum((d.pv_curtailed_kw + d.wind_curtailed_kw) * .5 for d in decisions),
        aging=aging, soc_end=c.soc.soc, soh_end=c.soh,
        converged=all(d.price_demand_converged and d.forecast_price_residual <= 1e-3 for d in decisions),
        decisions=[dict(timestamp=d.timestamp.isoformat(), price=d.price, soc=d.soc,
            storage_kw=d.storage_kw, price_residual=d.price_residual,
            demand_residual_kw=d.demand_residual, soh_residual=d.soh_residual,
            forecast_price_residual=d.forecast_price_residual,
            converged=d.price_demand_converged, iterations=d.price_demand_iterations,
            horizon_prices=d.horizon_result['prices'], sweeps=d.horizon_result['sweeps'],
            power_flow=d.power_flow) for d in decisions])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT/'parper/agent_runs/knn_noon_v1')
    p.add_argument('--limit-train', type=int, default=0)
    p.add_argument('--limit-validation', type=int, default=0)
    p.add_argument('--limit-test', type=int, default=0)
    args = p.parse_args()
    random.seed(11)
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Output directory must be empty; preserve earlier raw runs')
    split_path = ROOT/'parper/agent_date_split.json'
    split = json.loads(split_path.read_text(encoding='utf-8'))
    weather = ROOT/'Dataset/10.09/dataset_1.csv'
    ev = ROOT/'Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv'
    assert sha256(weather) == split['source_weather_sha256']
    assert sha256(ev) == split['source_ev_sha256']
    manifest = dict(status='running', seed=11, started=datetime.now().isoformat(),
        split_sha256=sha256(split_path), ev_sha256=sha256(ev), weather_sha256=sha256(weather),
        method='supervised_knn_BRI_price_warm_start', scope=__doc__,
        generation_status='engineering_proxy_from_date_matched_ERA5_not_measured_generation',
        weather_site_match='unverified_grid_point_1_spatial_proxy',
        weather_timezone='UTC validity time converted to Asia/Shanghai',
        complete_weather_requirement='all 24 local hourly records required for every date',
        python=sys.version, solver='appsi_highs', protocol=dict(horizon_hours=2,
        train_validation_hour=12, test_start_hour=12, test_steps=2, dt_hours=.5,
        initial_test_soh=.8, initial_test_soc=.5, cap_kw=480, max_sweeps=2,
        neighbor_candidates=[1,3,5], selection='minimum_mean_validation_teacher_profit_gap',
        nonconvergence='retain raw records; exclude from performance claims'),
        source_hashes={str(path.relative_to(ROOT)):sha256(path) for path in
          [Path(__file__), ROOT/'scripts/run_short_window_comparison.py',
           ROOT/'src/game/agent_warm_start.py', ROOT/'src/game/dynamic_dc.py',
           ROOT/'src/game/bri.py', ROOT/'src/game/baseline.py', ROOT/'src/game/dc_microgrid.py']})
    dump(args.out/'manifest.json', manifest)
    def dates(part, limit):
        group=split['partitions'][part]
        return group[:limit] if limit else group
    def trace(date):
        steps, provenance=build_trace(split['site'], date, 0, 24, weather)
        assert len(steps)==48
        return steps
    def log(name, payload):
        with (args.out/name).open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload, allow_nan=False)+'\n')
    examples=[]
    start=time.perf_counter()
    train_dates=dates('train',args.limit_train)
    for i,date in enumerate(train_dates):
        tick=time.perf_counter()
        try:
            case=training_case(trace(date), i)
            teacher=solve_equilibrium_bri(case,max_sweeps=2)
            for feature,price in zip(case_features(case),teacher['prices']):
                examples.append(dict(date=date,features=feature,price=price))
            log('training.jsonl',dict(date=date,profit=teacher['profit'], prices=teacher['prices'],
                history=teacher['history'],sweeps=teacher['sweeps'],runtime_s=time.perf_counter()-tick))
        except Exception:
            log('errors.jsonl',dict(phase='train',date=date,error=traceback.format_exc()))
        print(f'train {i+1}/{len(train_dates)} {date}',flush=True)
    scores={k:[] for k in (1,3,5)}
    for i,date in enumerate(dates('validation',args.limit_validation)):
        try:
            case=training_case(trace(date), i)
            teacher=solve_equilibrium_bri(case,max_sweeps=2)
            for k in scores:
                tick=time.perf_counter()
                agent=PriceWarmStartAgent(examples,k)
                result=agent.solve(case,solve_equilibrium_bri,max_sweeps=2)
                gap=teacher['profit']-result['profit']
                scores[k].append(gap)
                log('validation.jsonl',dict(date=date,neighbors=k,teacher_profit=teacher['profit'],
                    profit=result['profit'],gap=gap,prices=result['prices'],runtime_s=time.perf_counter()-tick))
        except Exception:
            log('errors.jsonl',dict(phase='validation',date=date,error=traceback.format_exc()))
        print(f'validation {i+1} {date}',flush=True)
    if not all(scores.values()):
        raise RuntimeError('No valid validation results; cannot lock/test')
    means={k:sum(v)/len(v) for k,v in scores.items()}
    k=min(means,key=lambda key:(means[key],key))
    checkpoint=dict(kind='locked_supervised_knn',neighbors=k,examples=examples,
        locked_at=datetime.now().isoformat(),validation_mean_teacher_profit_gap=means,
        training_dates=train_dates,validation_dates=dates('validation',args.limit_validation),
        test_evaluation_started=False)
    dump(args.out/'locked_checkpoint.json',checkpoint)
    locked_hash=sha256(args.out/'locked_checkpoint.json')
    agent=PriceWarmStartAgent(examples,k)
    for i,date in enumerate(dates('prospective_test',args.limit_test)):
        try:
            steps=trace(date)
            # Alternate evaluation order to reduce first-run/cache timing bias.
            arms=[('baseline',None),('agent',agent)]
            if i%2: arms.reverse()
            results={name:replay(steps,policy) for name,policy in arms}
            log('test.jsonl',dict(date=date,checkpoint_sha256=locked_hash,
                arm_order=[name for name,_ in arms],**results))
        except Exception:
            log('errors.jsonl',dict(phase='test',date=date,error=traceback.format_exc()))
        print(f'test {i+1} {date}',flush=True)
    manifest.update(status='completed',finished=datetime.now().isoformat(),
        runtime_s=time.perf_counter()-start,checkpoint_sha256=locked_hash,
        training_examples=len(examples),selected_neighbors=k,
        actual_dates={part:dates(part,limit) for part,limit in
            [('train',args.limit_train),('validation',args.limit_validation),
             ('prospective_test',args.limit_test)]},
        limitation='Short-window supervised solver assistance; no full-day/lifetime/RL or acceleration claim')
    dump(args.out/'manifest.json',manifest)


if __name__=='__main__':
    main()
