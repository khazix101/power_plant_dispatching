"""Diagnostic common-request adapters for the four requested experiment arms.

This short-window replay is deliberately not a price-response or lifetime study.
The Agent arm remains unavailable until an actual policy adapter is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import date as calendar_date, timedelta
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


def replay_completed_days(result, state, soc, dates, hours):
    """Restore the exact day-boundary state from recorded executed actions."""
    completed = 0
    for item in result["days"]:
        if item["status"] != "diagnostic_completed":
            if item["status"] != "running" or item is not result["days"][-1]:
                raise ValueError("only an interrupted final day can be resumed")
            break
        if completed >= len(dates) or item["date"] != dates[completed]:
            raise ValueError("checkpoint dates do not match the requested trajectory")
        steps, provenance = build_trace("Expressway Service District A", item["date"], 0, hours,
                                        ROOT / "Dataset/10.09/dataset_1.csv")
        trace_hash = hashlib.sha256(json.dumps(steps, sort_keys=True).encode()).hexdigest()
        if item["trace_sha256"] != trace_hash or item["provenance"] != provenance:
            raise ValueError("checkpoint input trace or provenance changed")
        records = item["steps"]
        if len(records) != len(steps) or item["input_state"] != state.as_dict() or item["input_soc"] != soc:
            raise ValueError("checkpoint state or completed step count is inconsistent")
        summary = item["summary"]
        if abs(summary["energy_identity_residual_kwh"]) > 1e-6 or summary["nonconverged_steps"]:
            raise ValueError("checkpoint contains a failed energy or convergence gate")
        next_state, aging = transition_battery_state(
            state, [r["p_bess_kw"] for r in records],
            [soc * 100] + [r["soc"] * 100 for r in records],
            c_new=2000000.0, interval_hours=DT)
        if next_state.as_dict() != item["output_state"] or not math.isclose(
                aging.cost_yuan, summary["aging_cost_yuan"], rel_tol=0, abs_tol=1e-8):
            raise ValueError("checkpoint battery transition does not replay")
        state, soc = next_state, summary["soc_end"]
        completed += 1
    return state, soc, completed


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


def execute_arm(arm, steps, cap, initial_soh, initial_soc, max_sweeps, checkpoint=None, carry_state=None, frozen_coefficient=None):
    started = time.perf_counter()
    state = carry_state.copy() if carry_state is not None else BatteryState.new(total_batteries=1, eb_kwh=2000.0, init_soh=[initial_soh])
    capacity = state.usable_capacity_kwh
    coefficient = marginal_cycle_cost_per_kwh(state, c_new=2000000.0) if frozen_coefficient is None else frozen_coefficient
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
    return next_state, {"label": ARMS[arm], "status": "diagnostic_completed" if valid else "diagnostic_failed_gate",
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
    parser = argparse.ArgumentParser(description="Continuous shared-request diagnostic; isolated runner")
    parser.add_argument("--arm", choices=("fixed_price_bess", "dynamic_fixed_cost", "soh_aware"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=24.0, help="24 for actual experiment; smaller value only for labeled smoke test")
    parser.add_argument("--dates-file", type=Path, help="newline-delimited local dates for a continuous trajectory")
    parser.add_argument("--start-date", help="first local date for a consecutive trajectory")
    parser.add_argument("--num-days", type=int, help="number of consecutive dates from --start-date")
    parser.add_argument("--initial-soh", type=float, default=0.9)
    parser.add_argument("--resume", action="store_true", help="resume after the last fully verified day")
    args = parser.parse_args()
    if args.dates_file and (args.start_date or args.num_days):
        parser.error("--dates-file cannot be combined with --start-date or --num-days")
    if bool(args.start_date) != bool(args.num_days) or (args.num_days is not None and args.num_days <= 0):
        parser.error("--start-date requires a positive --num-days")
    if args.dates_file:
        dates = tuple(line.strip() for line in args.dates_file.read_text(encoding="utf-8").splitlines() if line.strip())
    elif args.start_date:
        first = calendar_date.fromisoformat(args.start_date)
        dates = tuple((first + timedelta(days=index)).isoformat() for index in range(args.num_days))
    else:
        dates = ("2021-05-01", "2021-05-02", "2021-05-03")
    if not dates:
        raise ValueError("dates-file must contain at least one date")
    state = BatteryState.new(total_batteries=1, eb_kwh=2000.0, init_soh=[args.initial_soh])
    frozen = marginal_cycle_cost_per_kwh(state, c_new=2000000.0)
    soc = 0.5
    result = {"status": "running", "arm": args.arm, "label": ARMS[args.arm],
        "assets": {"battery_kwh": 2000, "pcs_kw": 800, "pv_kw": 400, "wind_kw": 100, "ev_cap_kw": 480},
        "dates": list(dates), "hours_per_day": args.hours, "initial_soh": args.initial_soh, "initial_state": state.as_dict(),
        "initial_soc": soc, "initial_matched_marginal_cost_yuan_per_kwh": frozen,
        "protocol": "same exogenous trace across arms; half-hour shrinking perfect trace within each day; full BatteryState and SOC carried across calendar-day boundaries; no EV backlog (unserved demand expires and is penalized); same reserve/terminal minimum0.2",
        "boundary_energy_policy": "carry SOC fraction across SOH usable-capacity update; quantify lost stored energy separately as capacity_revaluation_loss_kwh; no replacement expected above0.7SOH",
        "cost_factor": "fixed tariff and dynamic fixed-cost freeze initial marginal coefficient; SOH-aware recomputes daily from carried full BatteryState; physical capacity follows carried SOH in every arm",
        "code_sha256": {name: sha256(ROOT/name) for name in ("scripts/run_continuous_three_day.py", "scripts/run_continuous_three_day_precision.py", "scripts/continuous_precision_step_audit.py", "src/game/baseline.py", "src/game/bri.py", "src/game/dc_microgrid.py", "src/game/case.py", "src/game/state_transition.py", "src/battery/degradation.py", "src/battery/state.py")},
        "days": [], "limitations": ["3-day realized scenario diagnostic; no lifetime or statistical population claim", "rigid common request disables endogenous demand response", "Agent not included; no policy adapter supplied", "weather-generation proxy and sparse EV zero-fill conventions are recorded each day"]}
    start_day = 0
    if args.output.exists():
        if not args.resume:
            raise FileExistsError(f"output already exists; use --resume: {args.output}")
        saved = json.loads(args.output.read_text(encoding="utf8"))
        for key in ("arm", "assets", "dates", "hours_per_day", "initial_soh", "initial_state",
                    "initial_soc", "initial_matched_marginal_cost_yuan_per_kwh", "code_sha256"):
            if saved.get(key) != result[key]:
                raise ValueError(f"checkpoint {key} differs from this run")
        if saved["status"] not in ("running", "continuous_diagnostic_completed"):
            raise ValueError("failed trajectory requires a new output file and explicit review")
        state, soc, start_day = replay_completed_days(saved, state, soc, dates, args.hours)
        if saved["status"] == "continuous_diagnostic_completed":
            if start_day != len(dates):
                raise ValueError("completed checkpoint has missing days")
            print(args.output, "already_completed", flush=True)
            return
        result = saved
        result["days"] = result["days"][:start_day]
    elif args.resume:
        raise FileNotFoundError(args.output)

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_name(args.output.name + ".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
        os.replace(temp, args.output)
    for day, date in enumerate(dates[start_day:], start=start_day):
        steps, provenance = build_trace("Expressway Service District A", date, 0, args.hours, ROOT/"Dataset/10.09/dataset_1.csv")
        item = {"date": date, "status": "running", "input_state": state.as_dict(), "input_soc": soc,
            "provenance": provenance, "trace_sha256": hashlib.sha256(json.dumps(steps,sort_keys=True).encode()).hexdigest()}
        result["days"].append(item)
        def checkpoint(records, elapsed):
            item.update({"status":"running", "steps":records, "completed_steps":len(records), "runtime_s":elapsed})
            save()
            if len(records)%8==0:
                print(f"{args.arm} {date} {len(records)}/{len(steps)} {elapsed:.1f}s",flush=True)
        try:
            next_state, daily = execute_arm(args.arm, steps, 480.0, state.soh_system, soc, 2,
                checkpoint, carry_state=state, frozen_coefficient=None if args.arm=="soh_aware" else frozen)
            summary=daily["summary"]
            capacity_loss=summary["soc_end"]*(state.usable_capacity_kwh-next_state.usable_capacity_kwh)
            summary.update({"capacity_revaluation_loss_kwh":capacity_loss,
                "usable_capacity_start_kwh":state.usable_capacity_kwh,
                "usable_capacity_end_kwh":next_state.usable_capacity_kwh,
                "modeled_operating_value_yuan":summary["revenue_yuan"]-summary["aging_cost_yuan"]-100*summary["shed_kwh"]-1000*summary["non_ev_unserved_kwh"]-0.2*summary["curtailment_kwh"],
                "replacement_cost_yuan":next_state.group_manager.total_replace_cost-state.group_manager.total_replace_cost})
            item.update(daily)
            item["output_state"]=next_state.as_dict()
            state=next_state
            soc=summary["soc_end"]
            save()
            if item["status"] != "diagnostic_completed":
                result["status"] = "continuous_diagnostic_failed_gate"
                save()
                print(args.output, result["status"], flush=True)
                return
        except Exception as exc:
            item.update({"status":"failed", "error":f"{type(exc).__name__}: {exc}"})
            result["status"]="failed_stopped_without_state_fabrication"
            save()
            raise
    result["status"]="continuous_diagnostic_completed" if all(d["status"]=="diagnostic_completed" for d in result["days"]) else "continuous_diagnostic_failed_gate"
    result["source_hashes_unchanged"]=all(sha256(ROOT/name)==digest for name,digest in result["code_sha256"].items())
    result["final_state"]=state.as_dict()
    result["final_soc"]=soc
    result["agent_status"]="not_implemented"
    save()
    print(args.output, result["status"], flush=True)


if __name__ == "__main__":
    main()

