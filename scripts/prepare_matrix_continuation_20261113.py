"""Clone frozen matrix checkpoints into a separately identified 16:00 continuation."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "parper/experiment_runs/soh_date_matrix_v4_ssrd_aligned_20261112"
TARGET = ROOT / "parper/experiment_runs/soh_date_matrix_v4_continued_to_1600_20261113"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if TARGET.exists():
    raise SystemExit(f"Continuation already exists: {TARGET}")

source_bytes = (SOURCE / "manifest.json").read_bytes()
parent = json.loads(source_bytes)
manifest = dict(parent)
manifest["output"] = str(TARGET)
manifest["deadline_utc"] = "2026-11-13T08:00:00+00:00"
manifest["continuation"] = {
    "parent_output": str(SOURCE),
    "parent_manifest_file_sha256": digest(source_bytes),
    "parent_manifest_sha256": parent["manifest_sha256"],
    "created_utc": datetime.now(timezone.utc).isoformat(),
    "reason": "User extended experiment deadline from 08:00 to 16:00 CST on 2026-11-13",
    "unchanged": "cases, exogenous inputs, source snapshot, solver settings, physical model, and acceptance gates",
}
manifest.pop("manifest_sha256")
manifest["manifest_sha256"] = digest(json.dumps(manifest, sort_keys=True).encode())
write(TARGET / "manifest.json", manifest)
copied = []
for case in parent["cases"]:
    relative = Path("cases") / (case["case_id"] + ".json")
    path = SOURCE / relative
    if not path.exists():
        continue
    data_bytes = path.read_bytes()
    data = json.loads(data_bytes)
    data["continuation_parent_case_sha256"] = digest(data_bytes)
    data["continuation_parent_status"] = data["status"]
    data["manifest_sha256"] = manifest["manifest_sha256"]
    if data["status"] in ("running", "deadline_partial"):
        data["status"] = "continuation_pending"
    write(TARGET / relative, data)
    copied.append({"case_id": case["case_id"], "parent_status": data["continuation_parent_status"],
                   "parent_file_sha256": digest(data_bytes), "copied_steps": len(data.get("steps", []))})
write(TARGET / "CONTINUATION_PROVENANCE.json", {
    "parent_manifest_file_sha256": digest(source_bytes),
    "continuation_manifest_sha256": manifest["manifest_sha256"],
    "copied_cases": copied,
    "note": "Parent run remains untouched. Completed cases are retained; partial cases resume at recorded half-hour boundaries.",
})
print(json.dumps({"target": str(TARGET), "copied_cases": len(copied),
                  "copied_steps": sum(row["copied_steps"] for row in copied)}))
