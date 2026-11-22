"""Build a conservative, machine-readable summary of current aligned evidence."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(relative):
    path = ROOT / relative
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


short = read("experiment_runs/short_window_cap480_hour_end_20261112.json")
agent = read("agent_runs/knn_noon_full_v2/summary.json")
matrix = read("experiment_runs/soh_date_matrix_v4_continued_to_0800_20261114/final_key_numbers.json") or read("experiment_runs/soh_date_matrix_v4_ssrd_aligned_20261112/monitor_status.json")
arms = {
    name: read("experiment_runs/continuous_three_day_20261112/" + name + ".json")
    for name in ("fixed_price_bess", "dynamic_fixed_cost", "soh_aware")
}
summary = {
    "updated_cst": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    "configuration": {"pv_kw": 400, "wind_kw": 100, "bess_kwh": 2000, "pcs_kw": 800, "ev_kw": 480},
    "superseded_results": [
        "experiment_summary.csv",
        "three_date_paired_summary.csv",
        "earlier 24-hour and three-date weather-driven outputs using same-hour SSRD validity",
    ],
    "aligned_short_window": {
        "status": short["status"],
        "arms": {name: {"status": value["status"], "summary": value.get("summary")}
                 for name, value in short["results"].items()},
        "scope": "four half-hour steps on 2021-05-04; accounting diagnostic only",
    },
    "agent_noon": {
        "status": agent["run_status"],
        "training_dates": agent["training_dates"],
        "validation_rows": agent["validation_rows"],
        "test_dates_attempted": agent["test_dates_attempted"],
        "paired_converged_dates": agent["paired_converged_dates"],
        "nonconverged_dates": agent["strict_gate"]["nonconverged_dates"],
        "paired_mean_agent_minus_baseline": agent["paired_mean_agent_minus_baseline"],
        "runtime_uncertainty_all_completed_dates": agent["runtime_uncertainty_all_completed_dates"],
        "scope": "two half-hour noon executed steps; no full-day claim",
    },
    "soh_demand_matrix": matrix or {"status": "pending"},
    "continuous_three_day": {
        name: {
            "status": data["status"] if data else "pending",
            "completed_dates": len(data.get("days", [])) if data else 0,
            "days": [{"date": day["date"], "status": day["status"],
                      "summary": day.get("summary")} for day in data.get("days", [])] if data else [],
        }
        for name, data in arms.items()
    },
    "claim_limit": "No SOH-aware policy advantage or full four-arm multi-day effect is established by these diagnostics.",
}
(ROOT / "current_aligned_experiment_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(ROOT / "current_aligned_experiment_summary.json")
