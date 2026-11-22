"""Declare an identical ex-post operating-value boundary for completed arms."""
import argparse
import hashlib
import json
from pathlib import Path


def summarize(result):
    rows = []
    for arm, output in result["results"].items():
        if "summary" not in output:
            rows.append({"arm": arm, "status": output["status"], "metrics": None})
            continue
        s = output["summary"]
        step_count = len(output["steps"])
        value = s["revenue_yuan"] - s["aging_cost_yuan"] - 100 * s["shed_kwh"] - 1000 * s["non_ev_unserved_kwh"] - 0.2 * s["curtailment_kwh"]
        rows.append({"arm": arm, "status": output["status"], "metrics": {
            **s, "modeled_operating_value_yuan": value,
            "ev_shortfall_penalty_yuan": 100 * s["shed_kwh"],
            "non_ev_shortfall_penalty_yuan": 1000 * s["non_ev_unserved_kwh"],
            "curtailment_cost_yuan": 0.2 * s["curtailment_kwh"],
            "convergence": {"executed_steps": step_count,
                "complete_coordinate_sweep_without_objective_improvement_steps": step_count - s["nonconverged_steps"] if arm != "fixed_price_bess" else None,
                "nonconverged_steps": s["nonconverged_steps"],
                "meaning": "fixed arm solver optimal per call; dynamic flag means last full BRI coordinate sweep objective improvement<=1e-6 yuan; no global or Stackelberg certificate"}}})
    completed = [row for row in rows if row["metrics"] is not None]
    paired = []
    if completed and completed[0]["arm"] == "fixed_price_bess":
        baseline = completed[0]["metrics"]
        for row in completed[1:]:
            paired.append({"arm": row["arm"], "reference_arm": "fixed_price_bess",
                "delta_served_kwh": row["metrics"]["served_kwh"] - baseline["served_kwh"],
                "delta_modeled_operating_value_yuan": row["metrics"]["modeled_operating_value_yuan"] - baseline["modeled_operating_value_yuan"],
                "uncertainty": None, "n_dates": 1})
    return {"status": "descriptive_single_date_diagnostic", "rows": rows,
        "paired_descriptive_deltas": paired,
        "boundary": {"ev_shortfall_yuan_per_kwh": 100.0,
            "non_ev_shortfall_yuan_per_kwh": 1000.0, "curtailment_yuan_per_kwh": 0.2,
            "definition": "delivered_EV_revenue - modeled_aging - EV_shortfall_penalty - nonEV_shortfall_penalty - curtailment_cost",
            "not_full_net_profit": "excludes capex, O&M, tariff elasticity identification and user welfare; penalty weights are modeling choices"},
        "limitations": ["one date, no inferential uncertainty estimate",
            "common rigid request replay removes endogenous demand response",
            "health is fixed inside window with matched initial marginal cost; lifetime feedback not tested",
            "terminal minimum shared, exact terminal stored energy is reported rather than constrained equal",
            "Agent arm not implemented; no four-way performance ranking"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.input.read_bytes()
    result = summarize(json.loads(source))
    result["source_file"] = str(args.input)
    result["source_sha256"] = hashlib.sha256(source).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
