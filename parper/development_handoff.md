# Development and experiment handoff — 2026-11-12

## Changes made

- `scripts/run_24h_dc_experiment.py`: multiply dynamic EV revenue and renewable curtailment power sums by the 0.5 h dispatch interval. These two reported quantities were previously doubled. Replace stale undefined `DailyCsvForecastProvider` annotations with the actual calibrated provider type. Add site/date, assumed renewable source, cyclic-window and explicit EV-cap provenance; mark existing hourly/endogenous baselines as descriptive and unmatched. Existing dirty edits were retained; no baseline/model capacity was silently changed.
- New `scripts/run_short_window_comparison.py`: four named arm slots with existing fixed-price/BRI model adapters, shared exogenous EV requests, same 0.5 h execution, starting SOC/SOH, physical usable capacity, reserve/terminal lower bound and DC stepper. Explicit `--ev-cap-kw 480|960` is mandatory. PV/Wind remain 400/100 kW, BESS remains nominal 2000 kWh, PCS remains 800 kW. It writes source SHA256, trace SHA256, renewable input, useful delivery, curtailment, battery/network losses, stored-energy change, revenue, aging cost, throughput and individual step records.
- New `parper/test/test_experiment_accounting.py`: a controlled 48-step unit regression catches both missing-dt bugs; a recorded-run check verifies common requests, revenue integration, energy identity and the unavailable Agent status.

## Executed diagnostic

Runtime: `D:/miniconda3/python.exe`; solver: `appsi_highs`. Window: Site A, 2021-05-04 10:00–12:00 Asia/Shanghai, four half-hour steps, initial SOC 0.5 and SOH 0.9, no EV demand scaling. All implemented methods use initial usable BESS capacity 1800 kWh, derived from the same nominal 2000 kWh asset and SOH.

Commands:

```powershell
& D:/miniconda3/python.exe scripts/run_short_window_comparison.py --ev-cap-kw 480 --output parper/experiment_runs/short_window_cap480_diagnostic_20261112.json
& D:/miniconda3/python.exe scripts/run_short_window_comparison.py --ev-cap-kw 960 --output parper/experiment_runs/short_window_cap960_diagnostic_20261112.json
& D:/miniconda3/python.exe -m unittest parper.test.test_experiment_accounting parper.test.test_fair_replay_runner_contract -v
```

The 480 kW scenario completed all three implemented arms with identical EV requests/service: 124.4753984406 kWh, zero EV shedding and zero curtailment. Renewable input is 81.8003167333 kWh; EV + non-EV useful delivery is 244.4753984406 kWh; BESS conversion loss is 8.6160275890 kWh; network loss is 1.0294424843 kWh; stored-energy change is −172.3205517807 kWh. Energy identity residual is 5.68e-14 kWh; maximum per-step residual is zero. Final SOC is 0.4042663601. The revenue difference (186.7130976610 yuan fixed vs 311.1884961016 yuan dynamic) comes entirely from the tariff applied to rigid requests. It is **not evidence of a causal pricing benefit**. The dynamic and SOH-aware arms are identical in this matched-cost, fixed-within-window-health diagnostic, as expected.

The separate 960 kW scenario also completed all three implemented arms, with the same physical energy, service, SOC and revenue values: neither cap is binding here. Both scenario files preserve separate capacity labels. All 10 unittest checks passed after the final code changes.

The first three arm slots are `fixed price + BESS`, `dynamic price + fixed degradation cost`, and `SOH-aware dynamic game`. The fourth slot, `Agent-assisted SOH-aware dynamic game`, is explicitly `not_implemented`: no executable Agent policy adapter was found in the inspected game/scripts modules. It has no fabricated metrics and no replacement rule policy.

## Data provenance and scope limits

EV requests use the existing processed Site A transaction calibration, without rescaling. Its absent hourly rows retain the existing provider's zero convention, now recorded in provenance; this is not a missing-data validation result. Dates with no matching EV rows are rejected.

ERA5 `Dataset/10.09/dataset_1.csv` covers the EV date. GRIB validity timestamps are explicitly UTC in `scripts/build_weather_dataset.py`; the runner converts to Asia/Shanghai and requires every selected hour. PV uses `400*clip(ssrd/3600/1000,0,1)` and wind uses a 100 kW cubic 3–12 m/s curve with 25 m/s cutout. **These are engineering proxies from date-matched weather, not measured output or trained forecast predictions.** The selected ERA5 grid point's spatial match to the site is unverified. SSRD hourly accumulation/interval alignment is assumed and recorded, not independently validated. This short test uses the realized trace as a shrinking perfect-information window; it establishes no forecast generalization.

The frozen document says 480 kW EV cap while current implementation defaults to 960 kW. The new runner exposes both as separately named scenarios, and leaves existing defaults/documents unchanged. Neither cap binds in this particular low-load window; this run cannot resolve the capacity discrepancy.

SOH is held constant during the window, following the existing daily-transition convention, and evaluated posthoc with the existing state-transition model. The SOH-aware slot attaches the initial battery state to the optimization; all arms start with the same matched marginal cost. **This does not test evolving health feedback or demonstrate lifetime value.** No learning/Agent policy, multi-day experiment, endogenous price response, independent Stackelberg optimality certificate or exact-terminal-energy matching was implemented. BRI diagnostics only test no objective improvement in the last complete coordinate sweep. Network checks remain execution-layer diagnostics; no real ampacities exist. Revenue less aging is labeled as such and is not full operator net profit.

The old full 24 h experiment was not rerun: its baseline protocol is still unsuitable for policy ranking. Its arithmetic fix is verified by the regression test. Output under `local/results` was denied by the filesystem; completed new artifacts use `parper/experiment_runs` instead. No escalation or installation was needed. Pytest is not installed in the available runtime, so the existing unittest tests were run directly.

No manuscript, PDF, frozen model document, existing result artifact, or unrelated model source was edited by this development subtask.

## Subsequent 24-hour diagnostic and interruption

At the parent request, a 24 h Site A 2021-05-04 cap-480 scenario was started with `--start-hour 0 --hours 24`. The fixed arm completed 48 steps in 3.55 s and failed the physical/energy gate. Its full checkpoint is `parper/experiment_runs/full_day_cap480_20210504_diagnostic_20261112.json`. The dynamic BRI arm took approximately 206 s for its first four steps and was stopped at the parent's explicit instruction after the fixed-arm failure was established. No further full-day dates or caps were launched. No paired mean, uncertainty estimate or policy ranking is reported for this incomplete, failed experiment.

The checkpoint preserves the pre-correction planner behavior: requested-energy revenue entered optimization, whereas reported revenue was calculated ex post from delivered energy. Exact source snapshots are saved under `parper/experiment_runs/pre_correction_source_20261112`; file SHA256 values are in `full_day_cap480_20210504_code_provenance_20261112.json`. An interruption sidecar records the reason. The original checkpoint is preserved, including its invalid assumption that all non-EV base load was served.

The runner was then corrected only to separate non-EV requested, served and unserved energy in ex-post accounting. No core baseline, BRI or DC stepper source was edited. New `scripts/replay_checkpoint_energy.py` performs a fast 48-step ledger replay of the completed fixed-arm actions through the existing stepper; it does not rerun optimization. Output: `parper/experiment_runs/full_day_cap480_20210504_fast_ledger_replay_20261112.json`.

Replay results: EV requested 917.58 kWh, EV served 309.3989266 kWh, EV shed 608.1810734 kWh. Non-EV requested 1440 kWh, served 1170.6144044 kWh, unserved 269.3855956 kWh. Useful delivery is therefore 1480.0133310 kWh. After explicitly accounting for the base-load shortfall, the energy identity residual is 4.13e-8 kWh. The raw stepper power-balance residual still sums to 269.3855955 kWh; 17 time steps fail, and the maximum step deficit is 28.0655656 kWh. Final SOC is 0.2. Ex-post delivered EV revenue is 464.0983898 yuan. Replay runtime was 0.0035 s.

**The replay remains `failed_physical_gate`.** The existing stepper's network solution still assumes requested base load; voltage is not re-solved after assigning the deficit to unserved base demand. Closing the energy ledger by recording shortage does not validate that network solution. This result establishes a service/accounting failure, not a completed comparison or evidence of feasible off-grid supply. All 11 regression/contract checks passed after the final accounting change.

## Corrected-core rerun — completed

The parent subsequently corrected core leader revenue to delivered EV energy, separated EV/non-EV planned shedding, added non-EV priority (1000 versus EV 100 yuan/kWh), and corrected the DC stepper to re-solve after rigid load shortage. These core edits belong to the parent. This subtask updated the runner to consume explicit `non_ev_served_kw` and `non_ev_unserved_kw`, then embedded source SHA256 values in new experiment outputs.

The corrected 2 h test completed all three implemented arms with zero step residual and 5.68e-14 kWh energy identity residual. Its distinct output is `parper/experiment_runs/short_window_cap480_corrected_20261112.json`. This test preceded the parent's final priority-weight edit; no shortage bound in the 2 h window. It remains a basic corrected-accounting smoke test.

An initial corrected 24 h launch was interrupted when the parent notified a final priority-weight edit. Its partial checkpoint `full_day_cap480_20210504_corrected_20261112.json` and interruption sidecar are preserved and superseded. A fresh process restarted from step 0 with distinct output `parper/experiment_runs/full_day_cap480_20210504_corrected_priority_20261112.json`. All three implemented arms completed 48 half-hour steps. Source hashes were checked after completion and remained unchanged; exact source copies are under `parper/experiment_runs/corrected_priority_source_20261112`. No corrected 960 kW or extra dates were launched.

The completed priority run used Site A 2021-05-04, 00:00–24:00 Asia/Shanghai, mandatory cap480, nominal BESS2000 kWh/PCS800 kW, PV400/Wind100 kW, initial SOC0.5/SOH0.9, the same weather-proxy trace and requests, and the same daily SOH transition convention described above. All three arms produced identical physical service, prices and revenue:

- EV requested 917.58 kWh; served 309.4060043 kWh; shed 608.1739957 kWh; service rate 0.3371978512.
- Non-EV requested 1440 kWh; served 1171.2108377 kWh; unserved 268.7891623 kWh. Useful delivery is 1480.6168421 kWh.
- Renewable input 978.9275050 kWh; curtailment 1.0824906 kWh; BESS conversion loss 32.1202767 kWh; network loss 5.0489591 kWh; curtailment + both losses 38.2517264 kWh; throughput 612.9125510 kWh.
- Stored-energy change −539.9410635 kWh; final SOC0.2000327425; posthoc SOH0.8999675671. Aggregate energy identity residual4.1257e-8 kWh and maximum absolute half-hour balance residual1.1018e-8 kWh pass the1e-6 gates.
- Executed tariff1.5 yuan/kWh in every step of every arm. Delivered revenue464.1090065 yuan; modeled aging216.219 yuan; revenue less aging247.8900065 yuan.
- Runtime fixed4.0142267 s, dynamic fixed-cost670.6553592 s, SOH-aware624.9914823 s. Both dynamic arms report48/48 steps with no objective improvement greater than1e-6 yuan in the last complete coordinate sweep. This is the precise implemented BRI stopping diagnostic, not a global optimum or independent Stackelberg certificate. The fixed arm's MILP solved to solver-optimal termination at each call.

`diagnostic_completed` denotes the accounting and stopping checks. It does not mean adequate service: the non-EV and EV shortfalls remain explicit. Dynamic/SOH policy superiority is unsupported. Holding health fixed within the window and matching initial marginal cost makes the fixed-cost and SOH-aware adapters equivalent for this test; their equal outcomes provide no lifetime-feedback benefit evidence. Single-date descriptive paired differences are all zero; no uncertainty interval is computed. The Agent slot remains `not_implemented` with no metrics.

New `scripts/summarize_corrected_comparison.py` writes `parper/experiment_runs/full_day_cap480_20210504_corrected_priority_summary_20261112.json`, with source-file SHA256 and a declared common operating-value boundary:

```text
modeled operating value = delivered EV revenue - modeled aging
                         - 100 × EV unserved kWh
                         - 1000 × non-EV unserved kWh
                         - 0.2 × curtailed kWh
```

This value is **−329358.8883113 yuan for each implemented arm**. The weights are explicit modeling choices, not observed market costs. Revenue less aging remains separate. Neither quantity is full net profit: capex, O&M, identified demand elasticity and user welfare are excluded. The negative modeled operating value reflects the severe shortage penalties and should not be hidden behind positive delivered revenue.

For separate execution-layer before/after evidence, `scripts/replay_checkpoint_energy.py` was updated for the corrected stepper and run on the archived fixed-arm actions. Its new output is `full_day_cap480_20210504_corrected_execution_ledger_20261112.json`; the original `fast_ledger_replay` failure remains untouched. The corrected execution replay closes balance to4.13e-8 kWh and reports `service_shortfall_completed`, retaining the shortages. It is not a rerun of corrected optimization.

Final verification:13 accounting/contract unittest checks passed, including archived pre-correction failure preservation, separate corrected execution replay, the completed48-step corrected scenario, separate non-EV shortage identities, delivered-energy revenue integration and the declared operating-value formula. No further experiment processes from this subtask remain running.

## Additional independent dates — selection and coverage

The parent requested two extra independent24 h dates under the same nominal BESS2000 kWh/EVcap480 setup. Selected2020-05-30 has73.28 kWh observed EV energy, exactly the empirical P10 of available Site-A2020–2021 daily aggregation. Its weather-derived renewable input is812.7557 kWh. Selected2021-05-01 has502.72 kWh EV demand, above the available-period P90 of459.92 kWh, and3058.8746 kWh renewable proxy input:1116.1546 kWh more than EV demand plus the assumed1440 kWh base load on a daily-energy basis. These contrast with the previously completed max-demand2021-05-04. This is purposive scenario coverage, not a random sample.

Both additional dates have24 matched hourly ERA5 instants after UTC→Asia/Shanghai conversion. EV inputs retain the existing transaction aggregation's sparse-hour convention:2020-05-30 has observed EV rows at hours14,15,16,23;2021-05-01 has17 observed hours. Absent aggregate hours become zero under the existing provider. Independent transaction completeness/missingness has not been established; these must not be described as verified complete transaction records. Every run records the observed hours and this zero-fill convention in its provenance.

Both additional runs are now complete, each retaining all48 steps for the three implemented arms and the explicit unimplemented Agent slot. Their files are `full_day_cap480_20200530_corrected_priority_20261112.json` and `full_day_cap480_20210501_corrected_priority_20261112.json` under `parper/experiment_runs`. The common code hashes equal the previously completed2021-05-04 main case and were verified unchanged against the current files after completion. These are independent daily resets, not a chronological multiday SOH transition.

For the P10 date2020-05-30, all three arms pass the strict energy/stopping gates and give the same physical service and value. EV requested73.28 kWh, served33.52 kWh, shed39.76 kWh; non-EV shortfall155.1611923 kWh; curtailment+conversion+network loss34.3782645 kWh; aging215.8316 yuan. Delivered revenue50.28 yuan and declared modeled operating value−159302.7439174 yuan. Runtime fixed3.1831774 s, dynamic399.2111679 s, SOH400.3817434 s. The aggregate energy identity residual is−1.14e-12 kWh; maximum step residual is zero.

For the renewable-surplus/high-demand date2021-05-01, **all three implemented arms retain `diagnostic_failed_gate`**: maximum individual half-hour residual2.6942e-7 kWh passes the step gate, but their accumulated daily identity residual1.40795e-6 kWh exceeds the unchanged1e-6 aggregate threshold. No tolerance was relaxed. All BRI steps meet the implemented stopping diagnostic, but this numerical gate failure excludes the date from passed performance claims. Daily renewable surplus also does not guarantee hourly service: EV requested502.72 kWh, served480.6821710 kWh, shed22.0378290 kWh; non-EV shortfall73.5139784 kWh. All arms share curtailment691.9852089 kWh, combined curtailment+loss826.9920241 kWh, aging454.2608 yuan, final SOC0.7137302319 and stored-energy change+384.7144173 kWh. Fixed delivered revenue721.0232565 yuan; dynamic/SOH delivered revenue1146.7837401 yuan. Declared operating value fixed−75589.3959203 yuan, dynamic/SOH−75163.6354367 yuan. The raw425.7604836 yuan tariff difference changes neither service nor physical resource use and is not a causal pricing benefit under rigid common requests. Runtime fixed3.8915504 s, dynamic1016.9112577 s, SOH1008.7359709 s.

New `scripts/summarize_three_date_comparison.py` verifies common assets, code hashes, protocol, EVcap, initial SOC/SOH, terminal lower bound and matched initial marginal cost. Output `parper/experiment_runs/three_date_cap480_paired_diagnostics_20261112.json` includes per-date rows with modeled operating value, absolute served energy, curtailment+loss, aging, runtime, exact convergence, shortages, terminal energy, source hashes and raw paired differences against fixed price. It keeps the surplus-date failed gate and provides no pass-only pooled estimate. All physical paired differences across arms are zero for these probes; the surplus date has the tariff-only revenue difference described above. No confidence interval is supplied because three deliberately chosen contrasting dates do not support population inference. A statistical interval would overstate this design.

Final additional-date verification:14 unittest checks pass, including retention of the failed surplus gate, all three dates/48-step arm coverage, raw paired differences and no inferential interval. Both additional processes finished; none remain running. No core or manuscript source was changed during these runs; no health-feedback or Agent benefit is claimed.
