"""Read-only progress and matched-protocol audit for the independent-date matrix."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def inspect(output):
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    counts = Counter()
    cells = defaultdict(lambda: Counter())
    records = []
    completed_steps = 0
    problems = []
    for case in manifest["cases"]:
        path = output / "cases" / (case["case_id"] + ".json")
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "pending"}
        status = data["status"]
        counts[status] += 1
        key = f"SOH{case['initial_soh']:.1f}/{case['demand_quantile']}"
        cells[key][status] += 1
        completed_steps += len(data.get("steps", []))
        if data.get("manifest_sha256") not in (None, manifest["manifest_sha256"]):
            problems.append(f"manifest mismatch: {case['case_id']}")
        if status == "diagnostic_completed" and not all(data["gates"].values()):
            problems.append(f"invalid completed status: {case['case_id']}")
        if data.get("steps"):
            expected = data["initial_marginal_degradation_cost_yuan_per_kwh"]
            if any(row["marginal_degradation_cost_yuan_per_kwh"] != expected for row in data["steps"]):
                problems.append(f"nonfrozen coefficient: {case['case_id']}")
        records.append((case, data))
    # Every date's common physical requests and renewable trace must match
    # across all SOHs and ablations that have actually started.
    hashes = defaultdict(set)
    for case, data in records:
        if data.get("trace_sha256"):
            hashes[case["date"]].add(data["trace_sha256"])
    for date, values in hashes.items():
        if len(values) > 1:
            problems.append(f"noncommon trace: {date}")
    runtimes = [d["summary"]["runtime_s"] for _, d in records if "summary" in d]
    return {"checked_utc": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": manifest["manifest_sha256"], "planned_cases": len(manifest["cases"]),
            "status_counts": dict(counts), "completed_steps": completed_steps,
            "planned_steps": 48*len(manifest["cases"]), "cells": {k: dict(v) for k,v in cells.items()},
            "completed_case_runtime_s": runtimes,
            "protocol_audit_problems": problems,
            "independent_date_reset": True, "continuous_multiday": False,
            "cost_feedback_effect_estimable": False, "cost_feedback_dormant_within_day": True,
            "eligible_for_policy_ranking": False,
            "all_source_snapshot_hashes_match": all(
                hashlib.sha256((Path(manifest["snapshot"])/p).read_bytes()).hexdigest() == expected
                for p, expected in manifest["source_sha256"].items())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.output.resolve()), ensure_ascii=False, indent=2))
