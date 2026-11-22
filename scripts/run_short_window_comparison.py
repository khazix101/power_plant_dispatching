"""Diagnostic common-request adapters for the four requested experiment arms.

This short-window replay is deliberately not a price-response or lifetime study.
The Agent arm remains unavailable until an actual policy adapter is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.battery.degradation import marginal_cycle_cost_per_kwh
from src.battery.state import BatteryState
from src.game.baseline import run_fixed_price_baseline
from src.game.bri import solve_equilibrium_bri
from src.game.case import BatteryParams, EVUser, GameCase
from src.game.dc_microgrid import DCNetworkConfig, DCMicrogridPowerFlow, DCSOCConfig, DCSOCState, balance_dc_step
from src.game.forecast_provider import CalibratedForecastProvider
from src.game.state_transition import transition_battery_state

ARMS = {
    "fixed_price_bess": "fixed price + BESS",
    "dynamic_fixed_cost": "dynamic price + fixed degradation cost",
    "soh_aware": "SOH-aware dynamic game",
    "agent_soh_aware": "Agent-assisted SOH-aware dynamic game",
}
DT = 0.5


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_trace(site, date, start_hour, hours, weather_path):
    """Use matching calendar instants; weather conversion remains assumed."""
    provider = CalibratedForecastProvider(site=site, date=date)
    calibration = ROOT / "Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv"
    ev_frame = pd.read_csv(calibration)
    observed = ev_frame[(ev_frame["location"] == site) & (ev_frame["date"].astype(str) == provider.date)]
    if observed.empty:
        raise ValueError("selected site/date has no calibrated EV observations")
    weather = pd.read_csv(weather_path)
    # build_weather_dataset.py explicitly writes GRIB validity timestamps in UTC.
    weather.index = pd.to_datetime(weather["time"], utc=True).dt.tz_convert("Asia/Shanghai")
    begin = pd.Timestamp(provider.start + timedelta(hours=start_hour), tz="Asia/Shanghai")
    steps = []
    for index in range(int(hours / DT)):
        timestamp = begin + pd.Timedelta(hours=index * DT)
        hourly = timestamp.floor("h")
        hour_end = hourly + pd.Timedelta(hours=1)
        if hourly not in weather.index or hour_end not in weather.index:
            raise ValueError(f"date-matched weather missing boundary: {hourly} or {hour_end}")
        row_start = weather.loc[hourly]
        row = weather.loc[hour_end]
        if isinstance(row, pd.DataFrame) or isinstance(row_start, pd.DataFrame):
            raise ValueError("duplicate weather instants; select one grid-point CSV")
        # GRIB stepRange is a one-hour accumulation ending at validity time.
        # Wind is instantaneous; use the two boundary samples' mean speed.
        irradiance = max(0.0, float(row.ssrd_j_m2) / 3600.0)
        speed = max(0.0, (float(row_start.wind_speed_100m_ms)
                          + float(row.wind_speed_100m_ms)) / 2.0)
        pv = 400.0 * min(1.0, irradiance / 1000.0)
        wind = 0.0 if speed < 3 or speed >= 25 else 100.0 * min(1.0, (speed ** 3 - 3 ** 3) / (12 ** 3 - 3 ** 3))
        values = (irradiance, speed, pv, wind)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("nonfinite weather inputs")
        steps.append({"step_index": index, "timestamp": timestamp.isoformat(),
                      "dt_hours": DT, "pv_kw": pv, "wind_kw": wind,
                      "base_load_kw": 60.0,
                      "ev_requested_kw": provider.ev_load[timestamp.hour]})
    return steps, {
        "site": provider.site, "date": provider.date,
        "ev_source": str(calibration), "ev_sha256": sha256(calibration),
        "weather_source": str(Path(weather_path).resolve()), "weather_sha256": sha256(weather_path),
        "weather_date_matched": True, "weather_timezone": "UTC converted to Asia/Shanghai",
        "generation_status": "engineering_proxy_from_date_matched_ERA5_not_measured_generation",
        "weather_site_match": "unverified_grid_point_1_spatial_proxy",
        "generation_mapping": "PV=400*clip(hour-ending ssrd/3600/1000,0,1); wind 100kW cubic 3-12m/s, cutout25m/s using mean boundary speed",
        "ssrd_semantics": "raw GRIB stepRange verified as one-hour accumulation; validity timestamp is interval end; both half-hours use the same containing-hour mean irradiance",
        "wind_sampling_assumption": "100m speed is instantaneous at hourly validity; mean of start/end speeds approximates interval mean",
        "forecast_mode": "perfect_trace_shrinking_window_no_cyclic_extension",
        "non_ev_source": "assumed_constant_60kw", "ev_demand_scale": 1.0,
        "ev_hour_gaps": "hours absent from transaction aggregation are zero under the existing provider convention",
        "ev_observed_hours": sorted(set(observed["hour"].astype(int))),
    }


def execute_arm(arm, steps, cap, initial_soh, initial_soc, max_sweeps, checkpoint=None):
    started = time.perf_counter()
    state = BatteryState.new(total_batteries=1, eb_kwh=2000.0, init_soh=[initial_soh])
    capacity = state.usable_capacity_kwh
    coefficient = marginal_cycle_cost_per_kwh(state, c_new=2000000.0)
    soc = DCSOCState(DCSOCConfig(capacity_kwh=capacity, power_kw=800.0,
                               soc_min=0.1, soc_reserve=0.2, soc_max=0.9), initial_soc)
    network = DCMicrogridPowerFlow(DCNetworkConfig(ev_posts=8, ev_post_kw=cap / 8))
    records = []
    for index, step in enumerate(steps):
        remaining = steps[index:]
        users = [EVUser(f"request_{i}", 3.0, r["ev_requested_kw"] * DT,
                        (i,), max(cap, r["ev_requested_kw"]),
                        energy_min_kwh=r["ev_requested_kw"] * DT)
                 for i, r in enumerate(remaining) if r["ev_requested_kw"] > 0]
        case = GameCase(T=len(remaining), pv=[r["pv_kw"] for r in remaining],
                        wind=[r["wind_kw"] for r in remaining],
                        base_load=[r["base_load_kw"] for r in remaining], users=users,
                        battery=BatteryParams(capacity_kwh=capacity, power_kw=800.0,
                            soc0=soc.soc, soc_min=0.2, soc_max=0.9, soc_end_min=0.2,
                            deg_cost_per_kwh=coefficient),
                        battery_state=state if arm == "soh_aware" else None,
                        interval_hours=[DT] * len(remaining), penalty_shed=100.0,
                        ev_service_limit_kw=cap)
        if arm == "fixed_price_bess":
            plan = run_fixed_price_baseline(case, fixed_price=1.5)
            converged = True
        else:
            plan = solve_equilibrium_bri(case, max_sweeps=max_sweeps)
            converged = len(plan["history"]) >= 2 and abs(plan["history"][-1] - plan["history"][-2]) <= 1e-6
        before = soc.soc
        flow = balance_dc_step(network, soc, step["pv_kw"], step["wind_kw"],
            step["ev_requested_kw"], step["base_load_kw"],
            plan["p_dis"][0] - plan["p_ch"][0], dt_hours=DT, ev_service_limit_kw=cap)
        power = flow["p_bess_kw"]
        non_ev_unserved = flow["non_ev_unserved_kw"]
        battery_loss = (power * (1 / 0.95 - 1) if power >= 0 else -power * (1 - 0.95)) * DT
        records.append({**step, "price": plan["prices"][0], "soc_before": before,
            "soc": soc.soc, "p_bess_kw": power, "served_kwh": flow["ev_served_kw"] * DT,
            "shed_kwh": flow["ev_shed_kw"] * DT,
            "curtailment_kwh": (flow["pv_curtailed_kw"] + flow["wind_curtailed_kw"]) * DT,
            "battery_loss_kwh": battery_loss, "network_loss_kwh": flow["losses_kw"] * DT,
            "non_ev_unserved_kwh": non_ev_unserved * DT,
            "non_ev_served_kwh": flow["non_ev_served_kw"] * DT,
            "energy_residual_kwh": flow["residual_kw"] * DT,
            "revenue_yuan": plan["prices"][0] * flow["ev_served_kw"] * DT,
            "bri_coordinate_no_improvement": converged,
            "thermal_limits_verified": flow["power_flow"]["thermal_limits_verified"]})
        if checkpoint is not None:
            checkpoint(records, time.perf_counter() - started)
    totals = {name: sum(r[name] for r in records) for name in (
        "served_kwh", "shed_kwh", "curtailment_kwh", "battery_loss_kwh",
        "network_loss_kwh", "revenue_yuan", "non_ev_unserved_kwh", "non_ev_served_kwh")}
    renewable = sum((r["pv_kw"] + r["wind_kw"]) * DT for r in records)
    useful = totals["served_kwh"] + totals["non_ev_served_kwh"]
    stored_delta = (soc.soc - initial_soc) * capacity
    identity = renewable - useful - totals["curtailment_kwh"] - totals["battery_loss_kwh"] - totals["network_loss_kwh"] - stored_delta
    next_state, aging = transition_battery_state(state, [r["p_bess_kw"] for r in records],
        [initial_soc * 100] + [r["soc"] * 100 for r in records], c_new=2000000.0, interval_hours=DT)
    max_residual = max(abs(r["energy_residual_kwh"]) for r in records)
    requested = sum(r["ev_requested_kw"] * DT for r in records)
    valid = max_residual <= 1e-6 and abs(identity) <= 1e-6 and soc.soc >= 0.2 - 1e-12 and all(r["bri_coordinate_no_improvement"] for r in records)
    return {"label": ARMS[arm], "status": "diagnostic_completed" if valid else "diagnostic_failed_gate",
        "eligible_for_policy_ranking": False, "steps": records, "summary": {
            **totals, "requested_kwh": requested,
            "service_rate": totals["served_kwh"] / requested if requested else None,
            "max_abs_step_energy_residual_kwh": max_residual,
            "initial_marginal_degradation_cost_yuan_per_kwh": coefficient,
            "renewable_input_kwh": renewable, "useful_delivery_kwh": useful,
            "stored_energy_change_kwh": stored_delta, "energy_identity_residual_kwh": identity,
            "throughput_kwh": sum(abs(r["p_bess_kw"]) * DT for r in records),
            "curtailment_plus_loss_kwh": totals["curtailment_kwh"] + totals["battery_loss_kwh"] + totals["network_loss_kwh"],
            "soc_start": initial_soc, "soc_end": soc.soc, "terminal_soc_min": 0.2,
            "terminal_soc_feasible": soc.soc >= 0.2 - 1e-12,
            "soh_start": initial_soh, "soh_end_posthoc": next_state.soh_system,
            "aging_cost_yuan": aging.cost_yuan,
            "revenue_less_aging_yuan": totals["revenue_yuan"] - aging.cost_yuan,
            "runtime_s": time.perf_counter() - started,
            "nonconverged_steps": sum(not r["bri_coordinate_no_improvement"] for r in records)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="Expressway Service District A")
    parser.add_argument("--date", default="2021-05-04")
    parser.add_argument("--start-hour", type=int, default=10)
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--ev-cap-kw", type=float, choices=(480.0, 960.0), required=True)
    parser.add_argument("--initial-soh", type=float, default=0.9)
    parser.add_argument("--initial-soc", type=float, default=0.5)
    parser.add_argument("--max-sweeps", type=int, default=2)
    parser.add_argument("--weather", type=Path, default=ROOT / "Dataset/10.09/dataset_1.csv")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (0 < args.hours <= 24 and args.hours / DT == int(args.hours / DT)
            and 0 <= args.start_hour < 24 and args.start_hour + args.hours <= 24):
        parser.error("window must contain whole half-hours inside one local calendar day")
    if not 0.7 <= args.initial_soh <= 1 or not 0.2 <= args.initial_soc <= 0.9 or args.max_sweeps < 1:
        parser.error("invalid initial state or sweep count")
    steps, provenance = build_trace(args.site, args.date, args.start_hour, args.hours, args.weather)
    result = {"status": "short_window_diagnostic_only", "provenance": provenance,
        "ev_cap_scenario_id": f"cap_{args.ev_cap_kw:g}kw", "ev_service_cap_kw": args.ev_cap_kw,
        "unresolved_capacity_discrepancy": "freeze=480kW; implementation_default=960kW; explicitly selected scenario only",
        "assets": {"pv_kw": 400, "wind_kw": 100, "battery_kwh": 2000, "pcs_kw": 800},
        "protocol": "shared exogenous requests; rolling half-hour shrinking perfect trace; reserve/terminal SOC>=0.2; initial marginal cost matched; SOH fixed during short window and evaluated posthoc",
        "limitations": ["request rigidity disables endogenous response, so prices and revenue are descriptive",
            "SOH cost is frozen within this short window; health-feedback lifetime benefit is not tested",
            "terminal minimum is common; exact terminal stored energy can differ and is reported",
            "network is an execution-layer check; real ampacities unavailable"],
        "trace_sha256": hashlib.sha256(json.dumps(steps, sort_keys=True).encode()).hexdigest(),
        "code_sha256": {name: sha256(ROOT / name) for name in (
            "scripts/run_short_window_comparison.py", "src/game/baseline.py",
            "src/game/bri.py", "src/game/dc_microgrid.py", "src/game/case.py")},
        "core_accounting_version": "delivered_ev_revenue_split_shed_and_resolved_base_shortage_20261112",
        "results": {}}
    for arm in ("fixed_price_bess", "dynamic_fixed_cost", "soh_aware"):
        def checkpoint(records, elapsed):
            result["results"][arm] = {"label": ARMS[arm], "status": "running",
                "eligible_for_policy_ranking": False, "steps": records,
                "completed_steps": len(records), "runtime_s": elapsed}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            if len(records) % 4 == 0 or len(records) == len(steps):
                print(f"{arm}: {len(records)}/{len(steps)} steps, {elapsed:.1f}s", flush=True)
        try:
            result["results"][arm] = execute_arm(arm, steps, args.ev_cap_kw, args.initial_soh, args.initial_soc, args.max_sweeps, checkpoint)
        except Exception as exc:
            partial = result["results"].get(arm, {})
            result["results"][arm] = {**partial, "label": ARMS[arm],
                "status": "failed", "eligible_for_policy_ranking": False,
                "error": f"{type(exc).__name__}: {exc}"}
            print(f"{arm}: failed: {exc}", flush=True)
    result["results"]["agent_soh_aware"] = {"label": ARMS["agent_soh_aware"],
        "status": "not_implemented", "reason": "No executable Agent policy adapter found; no substitute policy or results fabricated."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(args.output.resolve())
    for arm, output in result["results"].items():
        print(arm, output["status"], json.dumps(output.get("summary", {})))


if __name__ == "__main__":
    main()
