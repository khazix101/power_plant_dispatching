# Current review edition: SOH integration feasibility, 18 November 2026

The current manuscript is WEVJ_second_draft.md; the synchronized Chinese source is WEVJ_second_draft_zh.md. Their current PDF outputs are WEVJ_second_draft_feasibility_20261118.pdf and WEVJ_second_draft_zh_feasibility_20261118.pdf. Earlier PDF filenames are historical copies and may remain open in a viewer. The main contribution is the capacity/cost/state-transfer interface for incorporating SOH into rolling leader-follower decisions. Agent and reinforcement-learning tests are auxiliary diagnostics.

The current continuous evidence is 72 accepted dynamic days plus a retained day-73 search-stopping failure in both dynamic arms, not a completed 90-day validation. Fixed price completed all 90 days. Fixed-request tests establish health/dispatch accounting and state transfer; they do not establish strategic price response or global equilibrium. The energy and revenue identities, carried state chain, and actual health-dependent capacity and marginal coefficient were independently audited by scripts/audit_soh_feasibility.py. Source hashes and failure timestamps are in soh_feasibility_audit_20261118.json. Figure 8 directly plots the recorded health feedback. The nominal replacement-value reference in Equation (10) is corrected to C_ref=2000000 CNY, consistent with the continuous runner.

Remaining requirements for stronger claims are a traceable diagnosis and re-test of the day-73 bounded search, continuous responsive-user validation, calibrated aging and behavioral inputs, and real branch ampacity data before a thermal-safety claim. None is presented as completed. This method-feasibility edition does not require an Agent benefit or a lifetime-profit advantage.

Rebuild equations with D:/miniconda3/python.exe parper/render_second_draft_equations.py; rebuild each PDF with the bundled Python and parper/build_second_draft_pdf.py or parper/build_second_draft_zh_pdf.py. The read-only feasibility audit command is D:/miniconda3/python.exe scripts/audit_soh_feasibility.py parper/experiment_runs/phase2_90day_20261115/2020_may_soh090 --output parper/soh_feasibility_audit_20261118.json.

The handoff text below is archived earlier context. Its pending/running statements and old numerical summaries do not supersede this edition.

---

# English second draft delivery — 12 November 2026

Review-copy deadline: **13 November 2026, 08:00 China Standard Time**. The user extended the experiment deadline to **16:00 China Standard Time**, and the remaining matrix cases subsequently finished in a provenance-linked continuation batch.

## Deliverables

- `WEVJ_second_draft.md`: editable English manuscript with model, role separation, rolling algorithm, experiment design, historical results, new short-window diagnostic, discussion, conclusion and 12 references.
- `WEVJ_second_draft.pdf`: 20-page reading copy. All pages were rebuilt and visually reviewed after the complete 144-case matrix result.
- `second_draft_figures/`: six English historical figures and a new paired Agent runtime figure. A separate Figure S1 for the earlier 24-hour diagnostic is superseded by the ERA5 hour-ending correction and must not be used as current evidence. The historical figures must not be presented as four-arm outcomes.
- `second_draft_references.bib`: BibTeX corresponding to the 12 manuscript references.
- `experiment_runs/short_window_cap480_diagnostic_20261112.json` and `short_window_cap960_diagnostic_20261112.json`: complete step-level, hash-identified new diagnostics.
- `experiment_runs/full_day_cap480_20210504_corrected_stepper_replay_20261112.json`: 24-hour corrected physical ledger for the saved fixed-price actions, with separate EV and non-EV shortfalls. This is not a corrected optimization outcome.
- `experiment_runs/full_day_cap480_20210504_corrected_priority_20261112.json` and `full_day_cap480_20210504_corrected_priority_summary_20261112.json`: completed fresh 24-hour, three-arm common-request run under the corrected leader objective and execution stepper. The fourth Agent slot is explicitly unavailable.
- `experiment_runs/three_date_cap480_paired_diagnostics_20261112.json`: raw per-date paired differences and gate status for one supply-tight date, a P10 date, and a high-renewable date. Two dates pass and one fails the strict cumulative energy residual gate. No population confidence interval is claimed.
- `three_date_paired_summary.csv`: English flat summary of all 12 date-arm slots, including paired deltas and gate flags.
- `agent_date_split.json` and `agent_runs/knn_noon_full_v2/`: chronological date IDs, locked supervised Agent checkpoint, all 357 training dates, 241 validation dates, 60 test records, and paired/uncertainty summaries. This is a one-hour noon solver-assistance test; 59 pairs pass the strict convergence gate and no acceleration is demonstrated.
- `experiment_summary.csv`: archived 12-row diagnostic summary generated before the ERA5 hour-ending correction. Its weather-driven rows are superseded and must not be used as current comparative evidence. The current aligned short-window JSON, Agent summary, and checkpointed matrix files are authoritative.
- `current_aligned_experiment_summary.json`: current aligned-weather short-window results, locked Agent comparison, completed matrix result and continuous three-day raw gate status.
- `experiment_runs/soh_date_matrix_v4_continued_to_0800_20261114/final_key_numbers.json` and `final_paired_cell_summary.json`: complete 144-case matrix result, with 136 gate passes and 8 failures.
- `experiment_runs/continuous_three_day_20261112/`: completed raw three-arm, three-day state-transfer trajectories, paired summary, independent precision sensitivity and run instructions in `handoff.md`. All nine original arm-days retain failed energy gates; the secondary numerical ledger explains them without replacing the raw results.
- `scripts/run_short_window_comparison.py` and `parper/test/test_experiment_accounting.py`: reproduction runner and accounting tests.

## Research structure and cited reference PDFs

The second draft follows the leader/follower information-exchange organization in Cheng et al. (2025, Applied Energy, DOI 10.1016/j.apenergy.2024.125145), whose sequential design states initialization, adjustment and stopping steps, and uses an explicit literature-comparison table. Singh et al. (2024, Journal of Energy Storage, DOI 10.1016/j.est.2024.113913) motivates a bi-level islanded microgrid setup and contrasts uncertainty, demand response and network modeling against prior work. The present manuscript adopts that reporting structure, **not** either paper's unimplemented convergence or network-security claims. Here SOH changes capacity and cost, the game maps tariff to follower demand and operator dispatch, and the prospective Agent only assists computation. The existing Q-learning storage benchmark is a separate historical negative result.

## New verified diagnostic

Site A, 4 May 2021, 10:00–12:00 local time; four 0.5-hour steps; initial SOC 0.50, SOH 0.90. ERA5 weather is date matched after UTC-to-China conversion; PV and wind are assumed power conversions, not measured station generation. For each capacity scenario, fixed price+BESS, dynamic price+fixed cost, and SOH-aware dynamic game each requested and served 124.4754 kWh EV energy. Curtailment and EV shortfall were zero. Energy identity residual was below 5.7e-14 kWh. Dynamic and SOH-aware physical trajectories coincide; the price revenue difference is purely tariff arithmetic on fixed requests. The Agent-assisted arm was not run in this particular four-slot check; its separate locked noon test is reported below. These observations are an accounting check, not evidence of net-profit, service, aging or health advantages. See `development_handoff.md` for detailed source assumptions and commands.

A fresh corrected 24-hour run at the selected 2000-kWh/480-kW main configuration completed all three executable arms. Every arm served 309.406 of 917.580 kWh EV demand and 1171.211 of 1440 kWh non-EV demand; the paired service and modeled operating-value differences were zero on this one date. Curtailment plus battery and network losses were 38.252 kWh. Assumed reliability penalties make modeled operating value -329,358.888 CNY in all arms; this is not measured cash profit. Fixed, dynamic fixed-cost and SOH-aware runtimes were 4.01, 670.66 and 624.99 s. Energy residual was below 4.13e-8 kWh. No confidence interval is justified with one date; health does not change within this day. The Agent arm was not run in this particular 24-hour case, while its separate locked noon test is complete.

**Weather-alignment correction (12 November).** The preceding full-day and three-date results used ERA5 solar-radiation validity timestamps one hour early relative to the dispatch interval. They remain archived but are superseded as current weather-driven evidence. Raw GRIB metadata confirms each `ssrd` value is a one-hour accumulation ending at its validity timestamp. The aligned trace uses that hour-ending value and an average of wind-speed samples at the two hourly boundaries. A new aligned two-hour cap-480 diagnostic is in `experiment_runs/short_window_cap480_hour_end_20261112.json`; it passes the energy gate. The aligned SOH matrix completed all 144 cases in the continuation batch: 136 passed and 8 failed the cumulative energy gate, with no BRI nonconvergence. The Agent's separately locked, aligned-weather one-hour noon test has completed; 59/60 dates pass the strict convergence gate, physical/economic differences are zero, and a baseline/Agent runtime ratio of 0.9928 [0.9781, 1.0007] shows no acceleration evidence.

The aligned continuous 1–3 May test completed three 24-hour trajectories with full battery-state transfer. SOH fell from 0.9000000 to 0.8998263; dynamic fixed-cost and SOH-aware arms had identical service, aging and modeled operating value. All nine original arm-days failed the strict cumulative energy gate by 0.214–1.063 micro-kWh above threshold. The separate higher-precision execution replay passes the unchanged gate without changing actions or economic metrics; original failures remain marked. This diagnostic cannot support a policy ranking or lifetime claim.

Two additional purposive dates have completed. The low-demand P10 date passed all three arms and again showed zero paired physical and modeled-value differences. The high-renewable date completed but all three arms failed the strict cumulative energy identity gate by 0.408e-6 kWh; its dynamic revenue difference comes from applying different tariffs to the same rigid request. Preserve these raw results and exclude that date from pass-only summaries. Three purposive dates do not support a population confidence interval or a claim of SOH advantage.

## Run instructions

From the project root in PowerShell:

```powershell
& D:/miniconda3/python.exe scripts/run_short_window_comparison.py --ev-cap-kw 480 --output parper/experiment_runs/short_window_cap480_diagnostic_20261112.json
& D:/miniconda3/python.exe scripts/run_short_window_comparison.py --ev-cap-kw 960 --output parper/experiment_runs/short_window_cap960_diagnostic_20261112.json
& D:/miniconda3/python.exe scripts/run_short_window_comparison.py --date 2021-05-04 --start-hour 0 --hours 24 --ev-cap-kw 480 --output parper/experiment_runs/full_day_cap480_reproduction.json
& D:/miniconda3/python.exe scripts/summarize_corrected_comparison.py --input parper/experiment_runs/full_day_cap480_reproduction.json --output parper/experiment_runs/full_day_cap480_reproduction_summary.json
& D:/miniconda3/python.exe -m unittest discover -s parper/test -p 'test_*.py' -v
& D:/miniconda3/python.exe parper/render_second_draft_equations.py
& C:/Users/yuyu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe parper/build_second_draft_pdf.py
```

The PDF builder reads the editable `WEVJ_second_draft.md` and equation images from the second-draft equation renderer. The figure files are bundled in `second_draft_figures/`. `prepare_second_draft.py` was used for initial migration from the first draft; do not rerun it over later manuscript edits.

## Pending before a comparative claim

1. The author selected the frozen 2000-kWh BESS and 8 × 60-kW (480-kW) EV configuration for the second-round main case. Historical 1200-kWh/960-kW outputs and the explicit 960-kW short-window sensitivity remain separate. Do not pool these outputs or tune ratings to favor a method.
2. Extend beyond the three completed, purposively selected, independently reset date diagnostics to a continuous multi-day test with the same dated EV/weather trace, initial states, terminal energy rule and half-hour step in all arms. Predeclare P10/P50/P90 and SOH 1.00/0.90/0.80/0.70 cells, at least three independent dates or seeds per cell, and separately report failed/nonconverged cases. Resolve the high-renewable accumulated numerical residual without relaxing the recorded gate post hoc.
3. Completed for the one-hour noon scope: a supervised price warm-start Agent was locked after chronological training/validation and tested on 60 held-out dates. Still required for a full four-arm multi-day comparison: integrate this locked Agent with the full-day runner and report all matched cases, including failures. Existing storage-only Q-learning is not this arm.
4. Separate capacity and degradation-cost feedback after matching the initial marginal coefficient. Evaluate low-SOH, supply-tight and renewable-surplus input strata. A 95% paired interval across independent dates is appropriate only after enough paired dates exist; preserve individual differences otherwise.
5. Report delivered-energy net value with its complete cost boundary, absolute EV delivery, EV and non-EV shortfall, curtailment plus battery/network loss, aging and runtime. The new two-hour price revenue contrast alone cannot rank policies.
6. Verify that the selected ERA5 grid point represents the station spatially, confirm the `ssrd` hour-ending interval alignment, and treat weather-to-power curves as assumptions until site generation data are available.
7. Author metadata, affiliations, funding, conflict statements and final journal formatting need confirmation before submission.

Luna high-effort literature organization was attempted twice but its channel returned HTTP 502 both times; the primary author completed the PDF-based structure check and draft integration directly. Terra medium was unavailable in this session, so development ran through the available medium-effort agent.
