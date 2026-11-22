"""Plot the corrected single-day energy ledger in English."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

root = Path.cwd() / "parper"
result = json.loads((root / "experiment_runs/full_day_cap480_20210504_corrected_priority_20261112.json").read_text(encoding="utf-8"))
arm = result["results"]["fixed_price_bess"]
steps = arm["steps"]
x = [i / 2 for i in range(len(steps))]
ev_req = [r["ev_requested_kw"] for r in steps]
ev_served = [r["served_kwh"] / r["dt_hours"] for r in steps]
renewable = [r["pv_kw"] + r["wind_kw"] for r in steps]
non_ev = [r["non_ev_served_kwh"] / r["dt_hours"] for r in steps]
soc = [r["soc"] for r in steps]

fig, axes = plt.subplots(2, 1, figsize=(9, 6.2), sharex=True,
                         gridspec_kw={"height_ratios": [2, 1]})
axes[0].step(x, ev_req, where="post", color="#737b87", label="EV requested")
axes[0].step(x, ev_served, where="post", color="#1b77a5", label="EV delivered")
axes[0].step(x, renewable, where="post", color="#2b9d78", label="Modeled PV + wind")
axes[0].step(x, non_ev, where="post", color="#b67127", label="Non-EV delivered")
axes[0].set_ylabel("Power (kW)")
axes[0].legend(ncol=2, fontsize=9, frameon=False)
axes[0].grid(alpha=0.2)
axes[1].plot(x, soc, color="#6944a5", linewidth=2)
axes[1].axhline(0.2, color="#b44", linestyle="--", linewidth=1, label="Execution reserve")
axes[1].set_ylim(0.15, 0.55)
axes[1].set_ylabel("SOC (fraction)")
axes[1].set_xlabel("Local time on 4 May 2021 (h)")
axes[1].legend(fontsize=9, frameon=False)
axes[1].grid(alpha=0.2)
fig.suptitle("Corrected 24-hour common-request diagnostic — frozen 480-kW EV cap")
fig.tight_layout()
path = root / "second_draft_figures/figure_s1_corrected_day_20210504.png"
fig.savefig(path, dpi=220)
plt.close(fig)
print(path)
