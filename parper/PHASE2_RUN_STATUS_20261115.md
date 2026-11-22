# Phase 2 continuous experiment status

## Latest audit: 18 November 2026

Fixed price completed 90 accepted days. Both dynamic jobs stopped after recording day 73 (12 July 2020) as diagnostic_failed_gate; their accepted consecutive prefix is 72 days (1 May-11 July). At 02:00 on day 73, each dynamic arm failed its two-sweep objective-stagnation gate. Day-73 energy residuals remain below 3e-11 kWh. No process is currently running these jobs. Days 74-90 remain unexecuted in the dynamic runs. Preserve the failures; a repair requires a separately identified replay or versioned continuous run. Current source and results are in soh_feasibility_audit_20261118.json. The 17 November 69-day snapshot is historical.


Started 15 November 2026, approximately 19:09 China Standard Time. The first
pre-registered complete-input block is 2020-05-01 through 2020-07-29, with
initial SOH 0.90 and SOC 0.50. The frozen configuration remains PV 400 kW,
wind 100 kW, BESS 2000 kWh/800 kW, and eight 60-kW EV posts. The three
non-Agent arms have separate output and stdout/stderr files under
`parper/experiment_runs/phase2_90day_20261115/2020_may_soh090/`.

| Arm | Launch PID | Output |
| --- | ---: | --- |
| fixed price + BESS | 87152 | `fixed_price_bess.json` |
| dynamic price + fixed degradation cost | 85988 | `dynamic_fixed_cost.json` |
| SOH-aware dynamic game | 75896 | `soh_aware.json` |

The runner writes an atomic JSON checkpoint at each half-hour step. `--resume`
verifies the exact source hashes, dates, initial state, completed-day input
traces and provenance, and replays the executed power/SOC records to reconstruct
the full battery state. An interrupted final day is recalculated from its
verified start state. Failed gates and exceptions are not silently resumed.
Three recovery tests passed, including a real two-date short-window interruption
and continuation using the precision runner.

All 90 local dates beginning 2020-05-01 and 2021-09-01 passed input preflight.
The planned 2020-01-01 winter block cannot run under the frozen no-imputation
rule: its first local weather boundary is outside the ERA5 CSV, and Site A has
no calibrated EV observations on 2020-02-08, 10-14, 17, and 26. Starting on
2020-01-02 still leaves the eight missing EV dates. This block needs an
explicitly amended date selection before execution; those gaps must not be
interpreted as zero demand. The 2021 autumn block and SOH 0.70 repeats remain
pending. A full-day held-out Agent comparison remains separate and pending.

The Phase 1 SOH 0.70 pilot crossed the retirement threshold after its first
day and incurred a modeled CNY 1,900,000 replacement in every corresponding
arm. Its remaining 13 days therefore describe a new battery, not sustained
low-SOH operation. Replacement cost must be reported separately from the
runner's `modeled_operating_value_yuan`, which excludes replacement by
definition. The SOH 0.70 Phase 2 repeats are threshold/replacement cases.

For an interrupted job, use the same command and append `--resume`; for example:

```powershell
D:\miniconda3\python.exe scripts/run_continuous_three_day_precision.py --arm soh_aware --output parper/experiment_runs/phase2_90day_20261115/2020_may_soh090/soh_aware.json --start-date 2020-05-01 --num-days 90 --initial-soh 0.9 --resume
```

No 90-day outcome or lifetime advantage is claimed until matched trajectories
finish and pass every gate. The 14-day pilot remains short-horizon evidence.
