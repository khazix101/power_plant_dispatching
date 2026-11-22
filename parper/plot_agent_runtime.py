"""Plot paired runtime differences for the locked noon Agent evaluation."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent
RUN = ROOT / "agent_runs" / "knn_noon_full_v2"
rows = [json.loads(line) for line in (RUN / "test.jsonl").read_text(encoding="utf-8").splitlines()]
summary = json.loads((RUN / "summary.json").read_text(encoding="utf-8"))
failed = set(summary["strict_gate"]["nonconverged_dates"])
dates = [r["date"] for r in rows]
delta = np.array([r["agent"]["runtime_s"] - r["baseline"]["runtime_s"] for r in rows])
passed = np.array([date not in failed for date in dates])

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.3), gridspec_kw={"width_ratios": [1.4, 1]})
idx = np.arange(len(rows))
ax1.axhline(0, color="0.25", linewidth=0.8)
ax1.scatter(idx[passed], delta[passed], s=15, color="#1f77b4", label="Passed gate (n=59)")
ax1.scatter(idx[~passed], delta[~passed], s=39, marker="x", color="#d62728", label="Failed gate (n=1)")
ax1.set_xlabel("Held-out test date (chronological index)")
ax1.set_ylabel("Agent minus baseline runtime (s)")
ax1.legend(frameon=False, loc="upper left", fontsize=8)
ax1.set_title("All 60 completed noon replays")

passed_delta = delta[passed]
ax2.hist(passed_delta, bins=16, color="#4c78a8", edgecolor="white")
ax2.axvline(0, color="0.25", linewidth=0.8)
ax2.axvline(float(np.mean(passed_delta)), color="#e45756", linewidth=1.4, linestyle="--",
            label=f"Mean {np.mean(passed_delta):+.4f} s")
ax2.set_xlabel("Agent minus baseline runtime (s)")
ax2.set_ylabel("Dates")
ax2.set_title("59 strictly converged pairs")
ax2.legend(frameon=False, fontsize=8)
fig.suptitle("Locked supervised price warm start: no demonstrated acceleration", fontsize=11)
fig.tight_layout()
out = ROOT / "second_draft_figures" / "figure_12_agent_runtime_paired.png"
fig.savefig(out, dpi=220, bbox_inches="tight")
plt.close(fig)
print(out)
