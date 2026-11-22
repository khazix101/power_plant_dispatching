"""Persist read-only case monitoring and conservative summaries until terminal state."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize_soh_date_matrix_20261112 import summarize
from finalize_soh_date_matrix_20261112 import finalize


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for attempt in range(20):
        try:
            temp.replace(path)
            return
        except PermissionError:
            time.sleep(.1)
    raise RuntimeError(f"cannot replace {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--deadline-utc", default="2026-11-12T23:40:00+00:00")
    args = parser.parse_args()
    output = args.output.resolve()
    deadline = datetime.fromisoformat(args.deadline_utc)
    while True:
        result = summarize(output)
        audit = result["audit"]
        counts = audit["status_counts"]
        all_terminal = not any(counts.get(key, 0) for key in ("running", "pending", "continuation_pending", "deadline_partial"))
        deadline_reached = datetime.now(timezone.utc) >= deadline
        result["monitor_final_state"] = all_terminal or deadline_reached
        result["monitor_stop_reason"] = "all_planned_cases_terminal" if all_terminal else "deadline_final_partial" if deadline_reached else None
        save(output/"paired_cell_summary.json", result)
        heartbeat = {"checked_utc": audit["checked_utc"], "status_counts": counts,
                     "completed_steps": audit["completed_steps"], "planned_steps": audit["planned_steps"],
                     "audit_problems": audit["protocol_audit_problems"], "all_terminal": all_terminal,
                     "deadline_reached": deadline_reached}
        save(output/"monitor_status.json", heartbeat)
        print(json.dumps(heartbeat), flush=True)
        if all_terminal or deadline_reached:
            save(output/"FINAL_STATE.json", heartbeat)
            finalize(output)
            break
        time.sleep(60)
