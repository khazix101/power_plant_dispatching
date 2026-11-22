# Continuous lifetime experiment plan

## Evidence boundary

The completed 1--3 May 2021 run is a state-transfer diagnostic, not a lifetime estimate. It carried the full `BatteryState` and SOC across days, but all original arm-day energy gates failed by small accumulated execution residuals. The separate high-precision replay used recorded actions and did not re-optimize, so it explains the numerical issue without replacing the failed run.

## Phase 0: execution gate repair

1. Keep the frozen physical and economic configuration: PV 400 kW, wind 100 kW, BESS 2000 kWh/800 kW, eight 60-kW posts, 0.5-h steps, date-matched hour-ending SSRD, and the same EV requests.
2. Tighten the local DC execution stopping tolerance to `1e-10 kW` and retain the unchanged acceptance gate of `1e-6 kWh` for the daily and cumulative energy identity.
3. Run the same 1--3 May trajectory with the three available full-day arms. Compare actions, delivered EV/non-EV energy, revenue, aging and SOC against the original ledger. Do not overwrite the original failed files.
4. Release the lifetime experiment only if every replayed day passes the energy gate, SOC/terminal constraints, source hash checks and solver status checks. Otherwise stop and report the failure.

## Phase 1: short continuous horizon

Run 14 consecutive complete-weather local dates, using one initial SOC/SOH per trajectory and carrying the full battery state at every day boundary. Use common requests and identical traces across the three non-Agent arms:

- fixed price + BESS;
- dynamic price + fixed degradation cost;
- SOH-aware dynamic game.

The Agent can join a separate matched pilot after its full-day adapter passes the same solver and energy gates. Do not use Agent training dates as independent test evidence.

Use two initial SOH levels (0.90 and 0.70) and two input strata selected before looking at outcomes: supply-tight/high EV demand and renewable-surplus/low EV demand. Keep initial SOC at 0.50 for the first pass. Record daily and cumulative requested/served/shed EV energy, non-EV shortage, delivered-energy revenue, aging cost, replacement cost, curtailment, battery/network loss, capacity-revaluation loss, throughput, SOC, SOH, boundary coefficient, runtime and all residuals.

The purpose is implementation validation and detection of divergence or replacement events. Do not report a lifetime benefit from 14 days.

## Phase 2: pre-registered lifetime comparison

After Phase 1 passes, run 90 consecutive dates for each predeclared trajectory for the three non-Agent arms. Use three nonoverlapping chronological blocks, not random transaction splits:

- 2020-01-01 onward for a winter/spring block;
- 2020-05-01 onward for a spring/summer block;
- 2021-09-01 onward for an autumn block, subject to complete weather coverage.

For each block, run the three non-Agent arms from the same initial state and retain every failed or nonconverged trajectory. Repeat the full trajectory at initial SOH 0.90 and 0.70. The primary endpoint is cumulative delivered EV energy at the same terminal-state rule; secondary endpoints are cumulative modeled net value, EV/non-EV shortage, curtailment plus electrical loss, aging/replacement cost, final SOH, usable capacity and wall-clock time.

The locked Agent receives a separate four-arm 60-day full-day comparison on the November--December 2021 held-out dates, using the same 60-day trace, initial SOH and SOC in every arm. This tests solver assistance over its actual held-out period. The earlier tiny-checkpoint smoke test inspected 1 November, so do not call the entire block completely untouched. If the full-day adapter needs tuning, do that on the existing validation set and lock it before the 60-day comparison; do not select settings using held-out outcomes.

Compare methods with paired date/block differences and report the trajectory-level differences. Three selected blocks do not establish independent population replication or justify a general 95% lifetime interval; report the three full trajectories and daily paired differences descriptively. An interval requires additional independently selected blocks or a clearly limited within-trajectory uncertainty estimand. Do not pool failed trajectories into a favorable mean.

The completed three-day run took about 3,136--3,156 seconds per dynamic arm, or roughly 17.5 minutes per dynamic arm-day on the available host. A 14-day pilot is therefore about 4 CPU-hours per dynamic arm; the six 90-day SOH/block trajectories imply about 315 CPU-hours per dynamic arm type before fixed-price runs, Agent runs, retries and host pauses. Schedule one block at a time with checkpoints and a daily progress report. Actual wall time must be measured, not promised from this rough estimate.

## Mechanism identification

Within each continuous trajectory, match the initial marginal degradation coefficient between fixed-cost and SOH-aware arms. The cost-feedback effect is identified from the carried daily coefficient after SOH changes; it is not identifiable in an independent single-day reset. Capacity-feedback and cost-feedback switches remain separate. Report planner actions and executed actions because the execution adapter can mask planning differences.

## Stop and reporting rules

- Any source/hash mismatch, missing weather hour, fabricated state, replacement-state discontinuity or solver error stops that trajectory and preserves its checkpoint.
- Any energy gate failure is reported as failed; tightening numerical tolerance can support a sensitivity ledger but cannot retroactively pass the original ledger.
- A policy advantage is reportable only when the same trajectory passes all gates in both arms and the difference is present in delivered energy or a predeclared cost boundary, not tariff arithmetic on rigid requests.
- A final SOH/lifetime claim requires the completed 90-day trajectories, explicit replacement accounting, and a calibrated aging model. Until then, use “continuous state-transfer diagnostic” and “short-horizon health-feedback test.”

## Immediate run order

1. Re-run the 1--3 May numerical-gate validation with the tightened execution tolerance.
2. Run the 14-day two-SOH pilot and inspect all gates before expanding.
3. Run the three 90-day non-Agent blocks one at a time after the pilot passes.
4. Finish and validate the full-day Agent adapter, then run the separate 60-day held-out four-arm comparison without using its outcomes to retune the Agent.
5. Update the manuscript with verified results and retain this plan and every raw ledger in the delivery package.
