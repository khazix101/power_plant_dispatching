"""Checkpointed independent-date SOH matrix with frozen physical ratings.

This is a diagnostic adapter, not the deployed controller. Capacity feedback
changes the planner's capacity; execution always uses initial-SOH physical
capacity. Cost feedback is dormant because state changes only at the daily
boundary. Both cost flags execute with the matched initial coefficient; this
cannot establish a cost-feedback effect. Replacement is reported at the boundary.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DT = 0.5
SITE = "Expressway Service District A"
SOHS = (1.0, 0.9, 0.8, 0.7)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for attempt in range(12):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(.1 * (attempt+1))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def select_dates(root, weather):
    import pandas as pd
    frame = pd.read_csv(root / "Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv")
    frame = frame[frame.location == SITE]
    daily = frame.groupby("date").normal_energy_kwh.sum().sort_index()
    instants = set(pd.to_datetime(pd.read_csv(weather, usecols=["time"])["time"], utc=True).dt.tz_convert("Asia/Shanghai"))
    # SSRD for [h,h+1) is stamped at validity h+1; wind snapshots may use h.
    # Require the complete union of those validity instants for the local day.
    eligible = [date for date in daily.index if all(pd.Timestamp(date, tz="Asia/Shanghai") + pd.Timedelta(hours=h) in instants for h in range(25))]
    eligible_daily = daily.loc[eligible]
    selections = []
    used = set()
    for label, quantile in (("P10", .1), ("P50", .5), ("P90", .9)):
        target = float(eligible_daily.quantile(quantile))
        nearest = sorted(eligible, key=lambda date: (abs(float(daily[date]) - target), date))
        chosen = []
        # Separate dates by at least 7 days within each stratum. These are
        # purposive nearest-quantile probes, not iid draws or population CIs.
        for date in nearest:
            if date in used or any(abs((pd.Timestamp(date) - pd.Timestamp(other)).days) < 7 for other in chosen):
                continue
            chosen.append(date)
            used.add(date)
            selections.append({"demand_quantile": label, "date": date, "target_quantile_daily_ev_kwh": target,
                               "observed_daily_ev_kwh": float(daily[date]),
                               "replicate": {"kind": "date", "id": date}})
            if len(chosen) == 3:
                break
        if len(chosen) < 3:
            raise ValueError(f"insufficient date-matched dates for {label}")
    return {"population_site_dates": len(daily), "eligible_complete_weather_dates": len(eligible),
            "selection_rule": "three nearest daily-EV-energy dates per quantile; unique globally; minimum seven-day within-stratum separation",
            "quantile_population": "Site A dates with complete date-matched local weather dispatch and following-hour SSRD validity records",
            "uncertainty": "purposive scenario probes; no iid independence or population confidence interval claimed",
            "selections": selections}


def prepare(args):
    out = args.output.resolve()
    if (out / "manifest.json").exists():
        return json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    snapshot = out / "source_snapshot"
    shutil.copytree(ROOT / "src", snapshot / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (snapshot / "scripts").mkdir()
    for name in ("run_short_window_comparison.py", "run_soh_date_matrix_20261112.py"):
        shutil.copy2(ROOT / "scripts" / name, snapshot / "scripts" / name)
    hashes = {str(p.relative_to(snapshot)).replace("\\", "/"): digest(p) for p in sorted(snapshot.rglob("*.py"))}
    weather = ROOT / "Dataset/10.09/dataset_1.csv"
    design = select_dates(ROOT, weather)
    cases = []
    # Cycle through SOH and demand strata before moving to the next replicate;
    # a deadline interruption therefore preserves broad initial coverage.
    for replicate_index in range(3):
        for soh in SOHS:
            for label in ("P10", "P50", "P90"):
                selected = [s for s in design["selections"] if s["demand_quantile"] == label][replicate_index]
                for capacity, cost in ((True, True), (False, True), (True, False), (False, False)):
                    cases.append({**selected, "initial_soh": soh, "initial_soc": .5,
                                  "capacity_feedback_enabled": capacity, "degradation_cost_feedback_enabled": cost,
                                  "case_id": f"{label}_{selected['date']}_soh{soh:.1f}_cap{int(capacity)}_cost{int(cost)}"})
    manifest = {"schema_version": "date-soh-matrix-diagnostic-v1", "created_utc": utc_now(),
                "root": str(ROOT), "output": str(out), "snapshot": str(snapshot), "weather": str(weather),
                "deadline_utc": "2026-11-13T00:00:00+00:00",
                "assets": {"nominal_battery_kwh": 2000, "pcs_kw": 800, "pv_kw": 400, "wind_kw": 100, "ev_service_limit_kw": 480},
                "design": design, "planned_cases": len(cases), "cases": cases, "source_sha256": hashes,
                "source_data_sha256": {str(p): digest(p) for p in (weather, ROOT / "Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv")},
                "protocol": {"dt_hours": DT, "hours": 24, "forecast": "perfect date-matched shrinking trace; no cyclic extension",
                             "requests": "fixed exogenous calibrated hourly EV energy; identical across all factors for each date",
                             "capacity_feedback_on": "planner=2000*initialSOH; physical=2000*initialSOH",
                             "capacity_feedback_off": "planner=2000; physical=2000*initialSOH; execution safety clips plans",
                             "cost_feedback_on": "dormant within day: initial matched coefficient frozen until daily state transition",
                             "cost_feedback_off": "initial matched coefficient frozen for the day",
                             "physical_soh": "fixed within day; exact trajectory aging and replacement reported posthoc at day boundary",
                             "soc": "initial .5; reserve and terminal lower bound .2; upper .9",
                             "bri": "at most three complete coordinate sweeps; no-improvement certificate retained",
                             "ranking": "diagnostic only; rigid requests disable price response; no global Stackelberg or lifetime claim"},
                "limitations": ["Cost-on/off is deliberately dormant within a single independent date; no dynamic cost-feedback effect is estimable. A chronological multiday experiment is required.",
                                "No online physical capacity shrinkage or replacement during the day.",
                                "Seven-day separation does not establish statistical independence.",
                                "Weather grid-point spatial match remains an unverified engineering proxy; SSRD uses the audited preceding-hour validity alignment.",
                                "Network executes after optimization; no measured branch ampacities, no network-safety claim.",
                                "Exact terminal stored energies may differ despite common lower-bound rule.",
                                "Outer coupled price/SOH/forecast residuals do not exist in this adapter and are recorded null, never zero certificates."]}
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    write_json(out / "manifest.json", manifest)
    return manifest


def run_case(manifest, case):
    snapshot = Path(manifest["snapshot"])
    for input_path, expected_hash in manifest["source_data_sha256"].items():
        if digest(input_path) != expected_hash:
            raise ValueError(f"frozen matrix input changed: {input_path}")
    sys.path.insert(0, str(snapshot))
    import scripts.run_short_window_comparison as replay
    replay.ROOT = Path(manifest["root"])
    from functools import partial
    replay.CalibratedForecastProvider = partial(replay.CalibratedForecastProvider, root=replay.ROOT)
    from src.battery.degradation import evaluate_daily_degradation, marginal_cycle_cost_per_kwh
    from src.battery.state import BatteryState
    from src.game.case import BatteryParams, EVUser, GameCase
    from src.game.bri import solve_equilibrium_bri
    from src.game.dc_microgrid import DCNetworkConfig, DCMicrogridPowerFlow, DCSOCConfig, DCSOCState, balance_dc_step
    from src.game.state_transition import transition_battery_state
    from src.game.solvers import get_solver_name
    output_path = Path(manifest["output"]) / "cases" / (case["case_id"] + ".json")
    if output_path.exists():
        previous = json.loads(output_path.read_text(encoding="utf-8"))
        if previous["status"] in ("diagnostic_completed", "diagnostic_failed_gate", "failed"):
            return {"case_id": case["case_id"], "status": previous["status"], "resumed_completed_file": True}
    started = time.perf_counter()
    steps, provenance = replay.build_trace(SITE, case["date"], 0, 24, manifest["weather"])
    state = BatteryState.new(total_batteries=1, eb_kwh=2000, init_soh=[case["initial_soh"]])
    physical_capacity = state.usable_capacity_kwh
    plan_capacity = physical_capacity if case["capacity_feedback_enabled"] else 2000.0
    initial_coefficient = marginal_cycle_cost_per_kwh(state, c_new=2000000.0)
    soc = DCSOCState(DCSOCConfig(capacity_kwh=physical_capacity, power_kw=800,
                               soc_min=.1, soc_reserve=.2, soc_max=.9), case["initial_soc"])
    network = DCMicrogridPowerFlow(DCNetworkConfig(ev_posts=8, ev_post_kw=60))
    record = {"case": case, "status": "running", "eligible_for_policy_ranking": False,
              "manifest_sha256": manifest["manifest_sha256"], "source_sha256": manifest["source_sha256"],
              "provenance": provenance, "started_utc": utc_now(), "solver": get_solver_name(),
              "trace_sha256": hashlib.sha256(json.dumps(steps, sort_keys=True).encode()).hexdigest(),
              "physical_capacity_kwh": physical_capacity, "planning_capacity_kwh": plan_capacity,
              "initial_marginal_degradation_cost_yuan_per_kwh": initial_coefficient, "steps": []}
    # Resume only at deterministic half-hour boundaries. Checkpoints carry the
    # actual SOC, prefix trajectory, and all diagnostic records.
    if output_path.exists():
        previous = json.loads(output_path.read_text(encoding="utf-8"))
        if previous.get("manifest_sha256") != manifest["manifest_sha256"] or previous.get("trace_sha256") != record["trace_sha256"]:
            raise ValueError("resume provenance mismatch")
        record["steps"] = previous.get("steps", [])
        if record["steps"]:
            soc.soc = record["steps"][-1]["soc"]
    write_json(output_path, record)
    try:
        for index in range(len(record["steps"]), len(steps)):
            if datetime.now(timezone.utc) >= datetime.fromisoformat(manifest["deadline_utc"]):
                record["status"] = "deadline_partial"
                record["interruption_reason"] = "Fixed 2026-11-13 08:00 CST deadline reached before next half-hour solve"
                write_json(output_path, record)
                return {"case_id": case["case_id"], "status": record["status"], "completed_steps": len(record["steps"])}
            step = steps[index]
            rows = record["steps"]
            coefficient = initial_coefficient
            remaining = steps[index:]
            users = [EVUser(f"request_{j}", 3.0, r["ev_requested_kw"] * DT, (j,),
                            max(480, r["ev_requested_kw"]), energy_min_kwh=r["ev_requested_kw"] * DT)
                     for j, r in enumerate(remaining) if r["ev_requested_kw"] > 0]
            game = GameCase(T=len(remaining), pv=[r["pv_kw"] for r in remaining], wind=[r["wind_kw"] for r in remaining],
                            base_load=[r["base_load_kw"] for r in remaining], users=users,
                            battery=BatteryParams(capacity_kwh=plan_capacity, power_kw=800, soc0=soc.soc,
                                                  soc_min=.2, soc_max=.9, soc_end_min=.2, deg_cost_per_kwh=coefficient),
                            interval_hours=[DT]*len(remaining), penalty_shed=100.0, ev_service_limit_kw=480)
            plan_started = time.perf_counter()
            plan = solve_equilibrium_bri(game, max_sweeps=3)
            history = plan["history"]
            objective_residual = abs(history[-1] - history[-2]) if len(history) > 1 else None
            converged = objective_residual is not None and objective_residual <= 1e-6
            before = soc.soc
            scheduled = plan["p_dis"][0] - plan["p_ch"][0]
            flow = balance_dc_step(network, soc, step["pv_kw"], step["wind_kw"], step["ev_requested_kw"],
                                   step["base_load_kw"], scheduled, dt_hours=DT, ev_service_limit_kw=480)
            power = flow["p_bess_kw"]
            expected_soc = before + ((-power*.95) if power < 0 else (-power/.95))*DT/physical_capacity
            loss = (power*(1/.95-1) if power >= 0 else -power*(1-.95))*DT
            rows.append({**step, "price": plan["prices"][0], "soc_before": before, "soc": soc.soc,
                         "p_bess_kw": power, "scheduled_bess_kw": scheduled,
                         "execution_schedule_adjustment_kw": power-scheduled,
                         "marginal_degradation_cost_yuan_per_kwh": coefficient,
                         "cost_feedback_dormant_within_day": True,
                         "served_kwh": flow["ev_served_kw"]*DT, "shed_kwh": flow["ev_shed_kw"]*DT,
                         "curtailment_kwh": (flow["pv_curtailed_kw"]+flow["wind_curtailed_kw"])*DT,
                         "battery_loss_kwh": loss, "network_loss_kwh": flow["losses_kw"]*DT,
                         "non_ev_unserved_kwh": flow["non_ev_unserved_kw"]*DT,
                         "non_ev_served_kwh": flow["non_ev_served_kw"]*DT,
                         "revenue_yuan": plan["prices"][0]*flow["ev_served_kw"]*DT,
                         "power_balance_residual_kw": flow["residual_kw"],
                         "energy_balance_residual_kwh": flow["residual_kw"]*DT,
                         "soc_transition_residual_fraction": soc.soc-expected_soc,
                         "bri_coordinate_no_improvement": converged, "bri_objective_residual_yuan": objective_residual,
                         "bri_sweeps": plan["sweeps"], "bri_history_yuan": history,
                         "price_residual": None, "demand_residual_kw": 0.0,
                         "soh_residual": None, "forecast_price_residual": None,
                         "residual_explanation": "Only BRI objective no-improvement is available; no outer coupled fixed-point loop is run. Demand is fixed exogenous.",
                         "thermal_limits_verified": flow["power_flow"]["thermal_limits_verified"],
                         "voltage_feasible": flow["power_flow"]["voltage_feasible"],
                         "reserve_boundary": soc.soc <= .2+1e-9, "capacity_boundary": soc.soc >= .9-1e-9,
                         "step_runtime_s": time.perf_counter()-plan_started})
            record.update(completed_steps=len(rows), runtime_s=time.perf_counter()-started, updated_utc=utc_now())
            write_json(output_path, record)
        rows = record["steps"]
        totals = {key: sum(r[key] for r in rows) for key in ("served_kwh", "shed_kwh", "curtailment_kwh", "battery_loss_kwh", "network_loss_kwh", "non_ev_served_kwh", "non_ev_unserved_kwh", "revenue_yuan")}
        renewable = sum((r["pv_kw"]+r["wind_kw"])*DT for r in rows)
        requested = sum(r["ev_requested_kw"]*DT for r in rows)
        stored = (soc.soc-case["initial_soc"])*physical_capacity
        identity = renewable-totals["served_kwh"]-totals["non_ev_served_kwh"]-totals["curtailment_kwh"]-totals["battery_loss_kwh"]-totals["network_loss_kwh"]-stored
        aging = evaluate_daily_degradation(state, [r["p_bess_kw"] for r in rows],
                    [case["initial_soc"]*100]+[r["soc"]*100 for r in rows], c_new=2000000, interval_hours=DT)
        next_state, transitioned = transition_battery_state(state, [r["p_bess_kw"] for r in rows],
                    [case["initial_soc"]*100]+[r["soc"]*100 for r in rows], c_new=2000000, interval_hours=DT)
        gates = {"energy_identity": abs(identity) <= 1e-6,
                 "step_power_balance": max(abs(r["power_balance_residual_kw"]) for r in rows) <= 1e-6,
                 "soc_transition": max(abs(r["soc_transition_residual_fraction"]) for r in rows) <= 1e-12,
                 "terminal_reserve": soc.soc >= .2-1e-12,
                 "complete_coordinate_no_improvement": all(r["bri_coordinate_no_improvement"] for r in rows)}
        record["status"] = "diagnostic_completed" if all(gates.values()) else "diagnostic_failed_gate"
        record["gates"] = gates
        record["summary"] = {**totals, "requested_kwh": requested, "service_rate": totals["served_kwh"]/requested if requested else None,
                             "renewable_input_kwh": renewable, "stored_energy_change_kwh": stored,
                             "energy_identity_residual_kwh": identity, "throughput_kwh": sum(abs(r["p_bess_kw"])*DT for r in rows),
                             "curtailment_plus_loss_kwh": totals["curtailment_kwh"]+totals["battery_loss_kwh"]+totals["network_loss_kwh"],
                             "aging_cost_yuan": aging.cost_yuan, "soc_end": soc.soc,
                             "soh_end_before_replacement": case["initial_soh"]-aging.delta_soh,
                             "soh_end_after_boundary_transition": next_state.soh_system,
                             "replacement_cost_yuan": transitioned.replacement_cost_yuan,
                             "reserve_boundary_steps": sum(r["reserve_boundary"] for r in rows),
                             "capacity_boundary_steps": sum(r["capacity_boundary"] for r in rows),
                             "nonconverged_steps": sum(not r["bri_coordinate_no_improvement"] for r in rows),
                             "runtime_s": sum(r["step_runtime_s"] for r in rows)}
        record["completed_utc"] = utc_now()
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()
    write_json(output_path, record)
    return {"case_id": case["case_id"], "status": record["status"], "completed_steps": len(record["steps"]), "runtime_s": record.get("runtime_s")}


def summarize(manifest):
    groups = {}
    statuses = {}
    for case in manifest["cases"]:
        path = Path(manifest["output"]) / "cases" / (case["case_id"]+".json")
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "pending"}
        status = data["status"]
        statuses[status] = statuses.get(status, 0)+1
        key = f"{case['initial_soh']}_{case['demand_quantile']}_cap{int(case['capacity_feedback_enabled'])}_cost{int(case['degradation_cost_feedback_enabled'])}"
        group = groups.setdefault(key, {"n_planned": 0, "n_completed_gate_pass": 0, "n_nonconverged": 0, "n_failed": 0, "n_pending_or_running": 0, "performance_rows_gate_pass_only": []})
        group["n_planned"] += 1
        if status == "diagnostic_completed":
            group["n_completed_gate_pass"] += 1
            group["performance_rows_gate_pass_only"].append({"date": case["date"], **data["summary"]})
        elif status == "diagnostic_failed_gate":
            group["n_nonconverged"] += int(not data["gates"]["complete_coordinate_no_improvement"])
            group["n_failed"] += int(data["gates"]["complete_coordinate_no_improvement"])
        elif status == "failed":
            group["n_failed"] += 1
        else:
            group["n_pending_or_running"] += 1
    result = {"updated_utc": utc_now(), "manifest_sha256": manifest["manifest_sha256"],
              "status_counts": statuses, "eligible_for_policy_ranking": False, "groups": groups}
    write_json(Path(manifest["output"])/f"progress.{os.getpid()}.json", result)
    return statuses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    manifest = prepare(args)
    print(json.dumps({"planned_cases": len(manifest["cases"]), "selection": manifest["design"]}, ensure_ascii=False), flush=True)
    if args.prepare_only:
        return
    cases = manifest["cases"][args.shard_index::args.shard_count]
    cases = cases[:args.limit] if args.limit else cases
    if args.workers == 1:
        for case in cases:
            try:
                print(json.dumps(run_case(manifest, case)), flush=True)
            except Exception as exc:
                failure = {"case": case, "status": "failed", "eligible_for_policy_ranking": False,
                           "manifest_sha256": manifest["manifest_sha256"], "error": str(exc), "traceback": traceback.format_exc()}
                write_json(Path(manifest["output"])/"cases"/(case["case_id"]+".json"), failure)
                print(json.dumps(failure), flush=True)
            print(json.dumps(summarize(manifest)), flush=True)
        return
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, manifest, case): case for case in cases}
        for future in concurrent.futures.as_completed(futures):
            case = futures[future]
            try:
                print(json.dumps(future.result()), flush=True)
            except Exception as exc:
                print(json.dumps({"case_id": case["case_id"], "worker_error": str(exc), "traceback": traceback.format_exc()}), flush=True)
            print(json.dumps(summarize(manifest)), flush=True)


if __name__ == "__main__":
    main()
