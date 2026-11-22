"""Paired descriptive results for deliberately selected independent dates.

These dates are scenario probes, not a random population sample; the output
therefore retains raw paired differences without an inferential interval.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.summarize_corrected_comparison import summarize


def combine(paths):
    results = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if len({r["provenance"]["date"] for r in results}) != len(results):
        raise ValueError("dates must be distinct")
    reference = results[0]
    for result in results:
        for name in ("assets", "protocol", "ev_service_cap_kw", "code_sha256", "core_accounting_version"):
            if result[name] != reference[name]:
                raise ValueError(f"incomparable {name}")
        if result["provenance"]["site"] != reference["provenance"]["site"]:
            raise ValueError("site differs")
        for arm in ("fixed_price_bess", "dynamic_fixed_cost", "soh_aware"):
            output = result["results"][arm]
            if output["status"] not in ("diagnostic_completed", "diagnostic_failed_gate", "failed"):
                raise ValueError(f"arm incomplete: {arm}")
            if "summary" in output:
                summary = output["summary"]
                ref_summary = reference["results"][arm]["summary"]
                for name in ("soc_start", "soh_start", "terminal_soc_min", "initial_marginal_degradation_cost_yuan_per_kwh"):
                    if summary[name] != ref_summary[name]:
                        raise ValueError(f"incomparable {name}")
    dates = []
    for path, result in zip(paths, results):
        daily = summarize(result)
        daily["date"] = result["provenance"]["date"]
        daily["source_path"] = str(path)
        daily["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        daily["trace_sha256"] = result["trace_sha256"]
        daily["weather_date_matched"] = result["provenance"]["weather_date_matched"]
        fixed = result["results"]["fixed_price_bess"]
        paired = []
        for arm in ("dynamic_fixed_cost", "soh_aware"):
            arm_result = result["results"][arm]
            valid = fixed["status"] == "diagnostic_completed" and arm_result["status"] == "diagnostic_completed"
            differences = None
            if "summary" in fixed and "summary" in arm_result:
                a, b = arm_result["summary"], fixed["summary"]
                differences = {name: a[name] - b[name] for name in (
                    "served_kwh", "shed_kwh", "non_ev_unserved_kwh",
                    "curtailment_plus_loss_kwh", "aging_cost_yuan", "runtime_s",
                    "soc_end", "stored_energy_change_kwh", "revenue_yuan")}
                rows = {row["arm"]: row["metrics"] for row in daily["rows"]}
                differences["modeled_operating_value_yuan"] = rows[arm]["modeled_operating_value_yuan"] - rows["fixed_price_bess"]["modeled_operating_value_yuan"]
            paired.append({"arm": arm, "reference_arm": "fixed_price_bess",
                "accounting_gate_passed_both": valid, "differences": differences,
                "policy_ranking_authorized": False})
        daily["paired_descriptive_deltas"] = paired
        dates.append(daily)
    return {"status": "paired_purposive_date_diagnostics", "n_distinct_dates": len(results),
        "dates": dates, "shared_code_sha256": reference["code_sha256"],
        "shared_assets": reference["assets"], "shared_ev_cap_kw": reference["ev_service_cap_kw"],
        "selection": {"2021-05-04": "previously selected Site-A maximum observed daily EV energy; scarce renewable proxy",
            "2020-05-30": "Site-A daily EV P10 exactly 73.28kWh among available2020-2021 dates",
            "2021-05-01": "above EV P90 (502.72kWh versus459.92); positive daily renewable-proxy balance under1440kWh assumed base"},
        "uncertainty_interval": None,
        "uncertainty_reason": "Three deliberately selected dates cover contrasting scenarios, not independent random draws from a defined population; a confidence interval would imply unsupported population inference. Raw paired differences are reported.",
        "limits": ["Each date starts independently atSOC0.5/SOH0.9; this is not a chronological multiday health transition",
            "SOH fixed within each window; no health-feedback lifetime benefit tested",
            "Agent arm unimplemented and excluded from numeric comparisons",
            "same terminal lower bound; differing terminal stored energy is explicitly reported",
            "weather-to-generation conversion remains engineering proxy; no learned forecast claim"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = combine(args.inputs)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
