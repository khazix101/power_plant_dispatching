"""Machine-readable conservative cell and date-paired independent-day diagnostics."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_soh_date_matrix_20261112 import inspect

METRICS = ("requested_kwh", "served_kwh", "shed_kwh", "service_rate", "curtailment_kwh",
           "battery_loss_kwh", "network_loss_kwh", "curtailment_plus_loss_kwh", "non_ev_served_kwh",
           "non_ev_unserved_kwh", "renewable_input_kwh", "stored_energy_change_kwh", "throughput_kwh",
           "aging_cost_yuan", "revenue_yuan", "soc_end", "soh_end_before_replacement",
           "soh_end_after_boundary_transition", "replacement_cost_yuan", "runtime_s")


def summarize(output):
    manifest = json.loads((output/"manifest.json").read_text(encoding="utf-8"))
    rows = []
    index = {}
    step_index = {}
    for case in manifest["cases"]:
        path = output/"cases"/(case["case_id"]+".json")
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "pending"}
        row = {**case, "status": data["status"], "complete_steps": len(data.get("steps", [])),
               "source_path": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
               "gates": data.get("gates"), "error": data.get("error"), "metrics": data.get("summary"),
               "trace_sha256": data.get("trace_sha256"),
               "initial_marginal_degradation_cost_yuan_per_kwh": data.get("initial_marginal_degradation_cost_yuan_per_kwh"),
               "nonconverged_steps": sum(not r["bri_coordinate_no_improvement"] for r in data.get("steps", [])),
               "physical_residual_max_all_attempted_steps": {
                   key: max((abs(r[key]) for r in data.get("steps", [])), default=None)
                   for key in ("power_balance_residual_kw", "energy_balance_residual_kwh", "soc_transition_residual_fraction")}}
        rows.append(row)
        index[(case["initial_soh"], case["date"], case["capacity_feedback_enabled"], case["degradation_cost_feedback_enabled"])] = row
        step_index[case["case_id"]] = data.get("steps", [])
    groups = defaultdict(list)
    for row in rows:
        groups[(row["initial_soh"], row["demand_quantile"], row["capacity_feedback_enabled"], row["degradation_cost_feedback_enabled"])].append(row)
    cells = []
    for key, group in sorted(groups.items()):
        valid = [r for r in group if r["status"] == "diagnostic_completed"]
        statuses = Counter(r["status"] for r in group)
        cells.append({"initial_soh": key[0], "demand_quantile": key[1], "capacity_feedback_enabled": key[2],
                      "degradation_cost_feedback_enabled": key[3], "n_planned": len(group),
                      "n_gate_pass": len(valid), "n_nonconverged": sum(r["nonconverged_steps"] > 0 for r in group),
                      "n_failed_or_failed_gate": sum(r["status"] in ("failed", "diagnostic_failed_gate") for r in group),
                      "status_counts": dict(statuses), "performance_n": len(valid),
                      "performance_mean_gate_pass_only": {metric: statistics.mean(r["metrics"][metric] for r in valid if r["metrics"][metric] is not None) if valid and any(r["metrics"][metric] is not None for r in valid) else None for metric in METRICS},
                      "failure_records": [{"case_id": r["case_id"], "status": r["status"], "gates": r["gates"], "error": r["error"], "nonconverged_steps": r["nonconverged_steps"]} for r in group if r["status"] in ("failed", "diagnostic_failed_gate")],
                      "case_ids": [r["case_id"] for r in group]})
    pairs = []
    for soh in (1., .9, .8, .7):
        for date_spec in manifest["design"]["selections"]:
            date = date_spec["date"]
            for factor in ("capacity", "cost"):
                for matched in (False, True):
                    # on-minus-off, holding the other switch fixed.
                    on = index[(soh, date, True, matched)] if factor == "capacity" else index[(soh, date, matched, True)]
                    off = index[(soh, date, False, matched)] if factor == "capacity" else index[(soh, date, matched, False)]
                    available = on["metrics"] is not None and off["metrics"] is not None
                    valid = on["status"] == off["status"] == "diagnostic_completed"
                    on_steps, off_steps = step_index[on["case_id"]], step_index[off["case_id"]]
                    n_matched = min(len(on_steps), len(off_steps))
                    schedule_delta = max((abs(a["scheduled_bess_kw"]-b["scheduled_bess_kw"]) for a,b in zip(on_steps, off_steps)), default=None)
                    actual_delta = max((abs(a["p_bess_kw"]-b["p_bess_kw"]) for a,b in zip(on_steps, off_steps)), default=None)
                    pairs.append({"initial_soh": soh, **date_spec, "factor": factor, "matched_other_factor_enabled": matched,
                                  "on_case_id": on["case_id"], "off_case_id": off["case_id"], "on_status": on["status"], "off_status": off["status"],
                                  "both_pass_gates": valid, "eligible_for_paired_summary": valid,
                                  "cost_feedback_dormant": factor == "cost", "factor_effect_estimable": factor == "capacity",
                                  "initial_coefficients_matched": on["initial_marginal_degradation_cost_yuan_per_kwh"] == off["initial_marginal_degradation_cost_yuan_per_kwh"] if available else None,
                                  "trace_matched": on["trace_sha256"] == off["trace_sha256"] if available else None,
                                  "matched_execution_steps": n_matched,
                                  "max_abs_planned_bess_difference_kw": schedule_delta,
                                  "max_abs_executed_bess_difference_kw": actual_delta,
                                  "planning_difference_masked_by_execution": schedule_delta > 1e-6 and actual_delta <= 1e-6 if schedule_delta is not None else None,
                                  "on_minus_off_descriptive_delta": {m: on["metrics"][m]-off["metrics"][m] if on["metrics"][m] is not None and off["metrics"][m] is not None else None for m in METRICS} if available else None})
    return {"generated_utc": datetime.now(timezone.utc).isoformat(), "audit": inspect(output),
            "manifest_sha256": manifest["manifest_sha256"], "assets": manifest["assets"], "design": manifest["design"],
            "protocol": manifest["protocol"], "limitations": manifest["limitations"],
            "interpretation": "Independent dates reset SOC/SOH; cost feedback dormant and matched within each day. Capacity contrasts test planner feedback with identical physical asset per SOH. No continuous lifetime superiority or policy ranking is established. Invalid cases remain visible and do not enter performance means.",
            "outer_solver_residuals": {"price_residual": None, "soh_residual": None, "forecast_price_residual": None,
                                       "reason": "Adapter has no outer coupled SOH/price/forecast fixed-point loop; null is not a convergence certificate."},
            "cells": cells, "case_records": rows, "date_paired_records": pairs,
            "statistical_uncertainty": {"confidence_interval": None, "reason": "Purposive nearest-quantile dates; no population sampling or iid independence established."}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.output.resolve())
    args.write.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"path": str(args.write.resolve()), "status_counts": result["audit"]["status_counts"],
                      "cell_count": len(result["cells"]), "date_pair_count": len(result["date_paired_records"])}))
