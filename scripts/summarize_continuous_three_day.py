"""Summarize original continuous diagnostics; never replace failed ledgers."""
import argparse
import hashlib
import json
from pathlib import Path


def summarize(folder):
    arms=("fixed_price_bess","dynamic_fixed_cost","soh_aware")
    runs={arm:json.loads((folder/f"{arm}.json").read_text(encoding="utf8"))for arm in arms}
    reference=runs[arms[0]]
    for run in runs.values():
        for key in ("assets","dates","initial_state","initial_soc","initial_matched_marginal_cost_yuan_per_kwh","code_sha256"):
            if run[key]!=reference[key]:
                raise ValueError(f"inconsistent shared field:{key}")
        if len(run["days"])!=3 or any("summary"not in day for day in run["days"]):
            raise ValueError("all original daily ledgers must be complete")
        for i,day in enumerate(run["days"]):
            if day["trace_sha256"]!=reference["days"][i]["trace_sha256"]:
                raise ValueError("trace mismatch")
            if i and (day["input_state"]!=run["days"][i-1]["output_state"] or day["input_soc"]!=run["days"][i-1]["summary"]["soc_end"]):
                raise ValueError("state transfer broken")
    output={"status":"continuous_original_ledgers_descriptive","acceptance_gate_kwh":1e-6,
        "days":[],"arm_totals":{},"source_sha256":{},
        "limits":["original gate failures retained; precise replay is separate numerical sensitivity",
            "3 consecutive days from one realized path; no inferential interval or lifetime conclusion",
            "Agent not implemented; rigid common requests; no queue/backlog carry",
            "marginal degradation coefficient daily update differs only in SOH arm; initial coefficient matched"],
        "agent_status":"not_implemented"}
    for arm,run in runs.items():
        output["source_sha256"][arm]=hashlib.sha256((folder/f"{arm}.json").read_bytes()).hexdigest()
        sums={name:sum(d["summary"][name]for d in run["days"])for name in (
            "served_kwh","shed_kwh","requested_kwh","non_ev_unserved_kwh","non_ev_served_kwh",
            "revenue_yuan","aging_cost_yuan","curtailment_plus_loss_kwh","runtime_s",
            "modeled_operating_value_yuan","capacity_revaluation_loss_kwh","replacement_cost_yuan")}
        sums.update({"initial_soh":run["initial_state"]["soh_system"],"final_soh":run["final_state"]["soh_system"],
            "initial_soc":run["initial_soc"],"final_soc":run["final_soc"],
            "original_gate_failed_days":sum(d["status"]!="diagnostic_completed"for d in run["days"]),
            "source_hashes_unchanged":run["source_hashes_unchanged"]})
        output["arm_totals"][arm]=sums
    for i,date in enumerate(reference["dates"]):
        rows=[]
        base=reference["days"][i]["summary"]
        for arm,run in runs.items():
            day=run["days"][i]
            rows.append({"arm":arm,"status":day["status"],"summary":day["summary"],
                "delta_versus_fixed":{name:day["summary"][name]-base[name]for name in (
                    "served_kwh","non_ev_unserved_kwh","aging_cost_yuan","revenue_yuan",
                    "curtailment_plus_loss_kwh","modeled_operating_value_yuan","soh_end_posthoc","soc_end")}})
        output["days"].append({"date":date,"rows":rows})
    return output


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory",type=Path,required=True)
    a=p.parse_args()
    result=summarize(a.directory)
    out=a.directory/"original_continuous_summary.json"
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf8")
    print(out)
