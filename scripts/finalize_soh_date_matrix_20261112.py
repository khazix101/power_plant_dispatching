"""Write dated final reporting-cutoff statistics without changing case records."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize_soh_date_matrix_20261112 import summarize


def finalize(output):
    result = summarize(output)
    audit = result["audit"]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    completed = [r for r in result["case_records"] if r["status"] == "diagnostic_completed"]
    pairs = [p for p in result["date_paired_records"] if p["both_pass_gates"]]
    capacity_pairs = [p for p in pairs if p["factor"] == "capacity"]
    cost_pairs = [p for p in pairs if p["factor"] == "cost"]
    starts, finishes = [], []
    step_runtime = 0.0
    physical_residuals = {}
    for row in result["case_records"]:
        path = Path(row["source_path"])
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("started_utc"):
            starts.append(datetime.fromisoformat(data["started_utc"]))
        if data.get("completed_utc"):
            finishes.append(datetime.fromisoformat(data["completed_utc"]))
        step_runtime += sum(s.get("step_runtime_s", 0) for s in data.get("steps", []))
        for metric, value in row["physical_residual_max_all_attempted_steps"].items():
            if value is not None:
                physical_residuals[metric] = max(physical_residuals.get(metric, 0), value)
    checked = datetime.fromisoformat(audit["checked_utc"])
    all_terminal = not any(audit["status_counts"].get(key, 0) for key in ("running", "pending", "continuation_pending", "deadline_partial"))
    elapsed_end = max(finishes) if all_terminal and finishes else checked
    n_nonconverged = sum(r["nonconverged_steps"] > 0 for r in result["case_records"])
    summary = {"generated_utc": audit["checked_utc"], "planned_cases": audit["planned_cases"],
               "status_counts": audit["status_counts"], "all_planned_terminal": all_terminal,
               "reporting_cutoff_local": datetime.fromisoformat(manifest["deadline_utc"]).astimezone(timezone(timedelta(hours=8))).isoformat(),
               "compute_deadline_local": datetime.fromisoformat(manifest["deadline_utc"]).astimezone(timezone(timedelta(hours=8))).isoformat(),
               "completed_half_hour_steps": audit["completed_steps"], "planned_half_hour_steps": audit["planned_steps"],
               "n_cases_with_nonconverged_steps": n_nonconverged,
               "n_nonconverged_steps": sum(r["nonconverged_steps"] for r in result["case_records"]),
               "complete_capacity_date_pairs": len(capacity_pairs), "complete_cost_date_pairs": len(cost_pairs),
               "capacity_pairs_with_planning_difference_masked_by_execution": sum(p["planning_difference_masked_by_execution"] for p in capacity_pairs),
               "max_capacity_pair_planned_bess_difference_kw": max((p["max_abs_planned_bess_difference_kw"] for p in capacity_pairs), default=None),
               "max_capacity_pair_executed_bess_difference_kw": max((p["max_abs_executed_bess_difference_kw"] for p in capacity_pairs), default=None),
               "max_capacity_pair_abs_served_delta_kwh": max((abs(p["on_minus_off_descriptive_delta"]["served_kwh"]) for p in capacity_pairs), default=None),
               "max_cost_pair_abs_executed_bess_difference_kw": max((p["max_abs_executed_bess_difference_kw"] for p in cost_pairs), default=None),
               "max_cost_pair_abs_aging_cost_difference_yuan": max((abs(p["on_minus_off_descriptive_delta"]["aging_cost_yuan"]) for p in cost_pairs), default=None),
               "runtime_wall_s": (elapsed_end-min(starts)).total_seconds() if starts else None,
               "summed_step_elapsed_runtime_s_all_attempts_including_host_suspension": step_runtime,
               "runtime_measurement_note": "perf_counter elapsed time, not solver CPU; a host-wide heartbeat gap can inflate step runtime.",
               "completed_case_runtime_range_s": [min(r["metrics"]["runtime_s"] for r in completed), max(r["metrics"]["runtime_s"] for r in completed)] if completed else None,
               "physical_residual_max_all_attempts": physical_residuals,
               "cell_counts": [{k: cell[k] for k in ("initial_soh", "demand_quantile", "capacity_feedback_enabled", "degradation_cost_feedback_enabled", "n_planned", "n_gate_pass", "n_nonconverged", "n_failed_or_failed_gate", "status_counts")} for cell in result["cells"]],
               "failures": [r for r in result["case_records"] if r["status"] in ("failed", "diagnostic_failed_gate")],
               "source_hash_audit": audit["all_source_snapshot_hashes_match"], "protocol_audit_problems": audit["protocol_audit_problems"],
               "cost_feedback_effect_estimable": False, "continuous_lifetime_advantage_established": False,
               "independent_date_resets": True, "physical_soh_fixed_within_day": True}
    n = audit["status_counts"].get("diagnostic_completed", 0)
    failed = audit["status_counts"].get("failed", 0)+audit["status_counts"].get("diagnostic_failed_gate", 0)
    text = (f"The frozen independent-day diagnostic matrix yielded {n} gate-passing cases out of 144 planned cases at the reporting cutoff; "
            f"{failed} cases failed an execution or diagnostic gate, and {n_nonconverged} cases contained a nonconverged coordinate-search step. "
            "All attempted and unfinished cases are retained in the machine-readable results, and only gate-passing cases enter performance means. "
            "Each date starts from the same prescribed initial SOC and SOH for its paired comparisons, and physical SOH remains fixed during the day. "
            "The degradation-cost switch is therefore dormant under matched initial marginal costs and cannot identify a dynamic cost-feedback effect. "
            "The capacity switch changes the planner's assumed capacity while execution uses the same SOH-reduced physical battery; "
            "the paired planned and executed trajectories are reported separately to expose any planning differences suppressed by the execution layer. "
            "These purposively selected, date-matched scenarios support descriptive implementation checks and do not establish a continuous-life benefit, "
            "a general policy ranking, a global Stackelberg optimum, or network safety.\n")
    for name, value in (("final_key_numbers.json", summary), ("final_paired_cell_summary.json", result)):
        (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output/"final_cautious_conclusion_en.txt").write_text(text, encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = finalize(args.output.resolve())
    print(json.dumps({k: result[k] for k in ("status_counts", "runtime_wall_s", "n_cases_with_nonconverged_steps")}))
