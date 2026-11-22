"""Refresh the review-copy inventory from files actually present on disk."""

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FILES = [
    "WEVJ_second_draft.md",
    "WEVJ_second_draft.pdf",
    "second_draft_references.bib",
    "second_draft_pdf_audit.json",
    "current_aligned_experiment_summary.json",
    "agent_date_split.json",
    "agent_runs/knn_noon_full_v2/manifest.json",
    "agent_runs/knn_noon_full_v2/locked_checkpoint.json",
    "agent_runs/knn_noon_full_v2/summary.json",
    "agent_runs/knn_noon_full_v2/test.jsonl",
    "experiment_runs/short_window_cap480_hour_end_20261112.json",
    "experiment_runs/soh_date_matrix_v4_ssrd_aligned_20261112/manifest.json",
    "experiment_runs/soh_date_matrix_v4_ssrd_aligned_20261112/final_key_numbers.json",
    "experiment_runs/soh_date_matrix_v4_ssrd_aligned_20261112/final_paired_cell_summary.json",
    "experiment_runs/continuous_three_day_20261112/design.md",
    "experiment_runs/continuous_three_day_20261112/original_continuous_summary.json",
    "experiment_runs/continuous_three_day_20261112/handoff.md",
    "PENDING_WORK_AUDIT_20261112.md",
    "SECOND_DRAFT_DELIVERY.md",
]


def file_entry(relative):
    path = ROOT / relative
    if not path.exists():
        return {"path": relative, "status": "missing"}
    data = path.read_bytes()
    return {"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def status(relative):
    path = ROOT / relative
    if not path.exists():
        return "missing"
    return json.loads(path.read_text(encoding="utf-8")).get("status", "unknown")


def matrix_status():
    path = ROOT / "experiment_runs/soh_date_matrix_v4_ssrd_aligned_20261112/monitor_status.json"
    if not path.exists():
        return "missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    return "terminal" if data.get("all_terminal") else "running"


manifest = {
    "status": "review_copy_ready_experiments_in_progress",
    "updated_cst": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    "review_copy_deadline_cst": "2026-11-13T08:00:00+08:00",
    "experiment_continuation_deadline_cst": "2026-11-13T16:00:00+08:00",
    "files": [file_entry(name) for name in FILES],
    "experiment_status": {
        "agent_short_window": status("agent_runs/knn_noon_full_v2/manifest.json"),
        "soh_date_matrix": matrix_status(),
        "continuous_fixed_price": status("experiment_runs/continuous_three_day_20261112/fixed_price_bess.json"),
        "continuous_dynamic_fixed_cost": status("experiment_runs/continuous_three_day_20261112/dynamic_fixed_cost.json"),
        "continuous_soh_aware": status("experiment_runs/continuous_three_day_20261112/soh_aware.json"),
    },
    "checks": {
        "unittest": "43 passed on 2026-11-12",
        "pdf": "21 pages rendered and visually inspected",
    },
    "evidence_limits": [
        "Old 24-hour and three-date runs with one-hour SSRD misalignment are superseded.",
        "Agent result is a one-hour noon replay, not a full-day four-arm comparison.",
        "Independent-day matrix does not identify within-day cost feedback or continuous lifetime effects.",
        "Site A latitude/longitude and grid-point spatial match are unverified.",
        "Continuous three-day raw energy gate failures remain failed even if a secondary numerical ledger passes.",
    ],
}
(ROOT / "second_draft_delivery_manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(ROOT / "second_draft_delivery_manifest.json")
