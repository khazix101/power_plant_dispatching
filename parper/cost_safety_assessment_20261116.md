# Cost and safety assessment of the SOH-aware method

## Evidence snapshot

The assessment uses the common 2020-05-01 trajectory and the completed
checkpoint days available at 2026-11-16 22:19 CST. Fixed-price has completed
90/90 days. Both dynamic arms have completed 60 days, so the paired comparison
uses 2020-05-01 through 2020-06-29. The two dynamic trajectories
remain running, so these are interim results.

The frozen assets are PV 400 kW, wind 100 kW, BESS 2000 kWh/800 kW and eight
60-kW EV posts. The revenue term is delivered EV energy multiplied by tariff.
EV shortage and non-EV shortage are separate. The reported modeled operating
value is delivered-energy revenue minus aging, EV shortage penalty, non-EV
shortage penalty and curtailment penalty; replacement cost is reported
separately.

## Paired cost comparison

For the 60 matched dates, SOH-aware minus dynamic fixed-cost was:

- delivered EV energy: approximately 0 kWh;
- EV shed: approximately 0 kWh;
- delivered-energy revenue: approximately 0 CNY;
- non-EV unserved energy: **-11.83 kWh**;
- curtailment plus battery/network loss: **-11.85 kWh**;
- aging cost: **+94.04 CNY**;
- replacement cost: 0 CNY;
- modeled operating value: **+11,741.68 CNY**;
- cumulative dynamic runtime: about **106.62 seconds longer** than the fixed-cost
  arm over these 60 dates (about 0.1%; both require about 27.2 hours of
  recorded runtime).

The apparent operating-value gain is driven by the 1000 CNY/kWh non-EV shortage
penalty: 11.83 kWh of avoided non-EV shortage contributes about 11,833 CNY,
which is partly offset by 94 CNY additional aging and small loss changes. The
break-even non-EV shortage penalty for this interim difference is about
**7.72 CNY/kWh**. At a zero penalty, the same paired physical outcomes would
give an approximately 91 CNY disadvantage; at 10 CNY/kWh the difference is
only about 27 CNY. The advantage is therefore sensitive to the declared
shortage valuation and is not a tariff or delivered-EV revenue advantage.
These penalties are an assumed reliability valuation, not measured cash expense.
The full 60-day dynamic computation is about 283 times the fixed-price
baseline's 346 seconds of recorded runtime. Recorded runtimes may include host
pauses, so this is a workload observation rather than a controlled CPU benchmark.
The SOH-aware modeled value remains approximately -8.56 million CNY over the
60 days, dominated by unserved non-EV energy. The positive paired difference
does not imply project profitability. This single chronological trajectory
does not support a population confidence interval.

## Safety and feasibility evidence

The 60 matched days contain 2880 half-hour steps per arm. They show:

- zero nonconverged steps;
- maximum step energy residual below 4.72e-11 kWh;
- maximum daily energy-identity residual below 2.33e-10 kWh;
- SOC within the recorded 0.20--0.90 operating band;
- zero terminal-SOC feasibility failures.

The archived steps do not include voltage margins. A read-only reconstruction
using delivered load and the recorded PV-first curtailment reproduced network
loss within 7e-14 kWh per step. In this prototype network, all reconstructed
voltages fell within 0.99624--1.01219 pu, so there were no model voltage-band
violations (band 0.95--1.05 pu). All three arms had the same observed extrema;
the SOH-aware arm did not establish a comparative voltage advantage.

These support numerical feasibility and SOC constraint adherence. They do not
establish network thermal safety. The DC model records
thermal_verified=false for every completed step because the branch current
ratings are unset. Voltage feasibility is evaluated by the prototype model,
and the reconstruction uses the assumed 0.4-kV, three-branch 0.005-ohm topology,
not surveyed station infrastructure. The manuscript must retain the
limitation that this is an
accounting/diagnostic safety check, not certification of surveyed feeder
voltage or ampacity limits.

## Decision

The SOH-aware method has a **conditional interim cost advantage** under the
current 1000 CNY/kWh non-EV shortage penalty: it reduces non-EV shortage and
curtailment-plus-loss while preserving EV delivery. It has no observed
delivered-EV revenue advantage and incurs slightly higher aging cost. Both
dynamic arms have a high computation burden relative to fixed price.

It is not yet justified to claim a general innovation advantage or network
safety advantage. Final confirmation requires the remaining dynamic days,
the matched SOH 0.70 and autumn blocks, explicit replacement accounting, and
real branch current ratings or a clearly labeled voltage-only prototype audit.

The exact read-only calculation is reproducible with:

    D:\miniconda3\python.exe scripts/audit_continuous_cost_safety.py parper/experiment_runs/phase2_90day_20261115/2020_may_soh090
