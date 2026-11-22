# Manuscript Evidence Audit

This audit records the first-pass wording and experiment-contract changes for the manuscript at `local/论文/manuscript/WEVJ_initial_draft.md`. The manuscript directory is ignored by the repository's `local/` rule, so this tracked index preserves the review trail without copying result files.

## Wording Changes Applied

| Manuscript location | Evidence-supported interpretation |
|---|---|
| Abstract, line 5 | Describes a bounded implementation demonstration under tested scenarios; no general policy or lifetime benefit is claimed. |
| Representative-day results, lines 222 and 235 | Calls the stopping test an implementation diagnostic and labels the median-day SOH difference as a scenario-specific one-day effect with higher modeled wear. |
| Thirty-day price comparison, line 245 | Labels the result a descriptive bounded-iteration comparison because request totals differ and four dynamic steps are nonconverged. |
| Tariff variation, line 261 | Treats tier variation as an output of the finite discrete-price procedure, not evidence of elasticity validity or optimality. |
| Common-request replay, line 279 | Retains the MILP termination and energy-residual evidence without extending it to the rolling BRI controller. |
| Agent discussion, line 341 | Reports no improvement and service failures for this benchmark only; does not generalize to all learning methods. |

## Mandatory Experiment Contracts

The detailed release gates are in `parper/experiment_plan.md`, sections F.1-F.5. The minimum requirements are:

1. Dynamic and fixed pricing must use common-request paired replays or explicitly report endogenous request differences; service rate cannot rank policies by itself.
2. Report requested, served, shed, curtailment, battery loss, curtailment-plus-loss, throughput, terminal SOC/SOH, and runtime for every method.
3. Cover low/P10, median/P50, and high/P90 demand at SOH 1.00, 0.90, 0.80, and 0.70, with at least three dates or seeds per cell. Nonconverged low-SOH cells remain diagnostics only.
4. Separate price, demand, SOH, and forecast-price residuals. A finite BRI sweep or completed output file is not a Stackelberg/global-optimality certificate; small cases require an independent KKT/MILP optimality gap.
5. Treat curtailment together with battery and network losses and stored-energy change. A reduction in curtailment without useful-delivery gain is an energy-dissipation transfer, not a utilization improvement.
6. Keep post-dispatch voltage/current checks separate from embedded network constraints, and label repeated or date-mismatched renewable profiles as assumed/synthetic.

## Current Release Status

- Representative-day SOH=1 runs: descriptive evidence only; no general SOH benefit.
- SOH=0.80 high-demand SOH-aware run: convergence failure; excluded from performance claims.
- Thirty-day dynamic/fixed run: descriptive only; four dynamic outer-limit steps and unequal endogenous requests.
- Common-request energy replay: usable for the stated fixed-request audit, not a proof of the rolling controller's global optimum.

## Open Configuration Discrepancy

- The current implementation default is `DCNetworkConfig.ev_posts=8` and `ev_post_kw=120`, hence 960 kW aggregate EV nameplate. Existing regression coverage also expects 960 kW.
- The manuscript's study configuration and some project notes use 8 x 60 = 480 kW. This remains an unresolved modeling assumption; neither value is silently substituted for the other.
- Before any new dynamic/fixed policy comparison, record EV capacity, input date and demand profile, tariff mode, time step, initial/terminal state, and source trace hash. Treat 480 and 960 kW as separate sensitivity cases until the project model definition is explicitly reconciled.
- The prior comparison also mixes 24 hourly fixed-price decisions with 48 half-hour dynamic decisions and has inconsistent price/revenue time integration. It is descriptive only; no causal or policy-ranking claim is authorized from it.
- Further unit audit: the dynamic runner integrates requested/served energy as `kW * 0.5 h` but computes EV revenue as `price * served_kW` without multiplying by `0.5 h`; the fixed hourly runner sums kW values as kWh only because its interval is exactly one hour. Both must use explicit interval integration in a shared stepper.
- The experiment runner must preserve separate physical EV request and price-responsive/accepted EV demand fields. Replaying a dynamic arm's endogenous request as a common physical request is not automatically valid because it mixes rigid load, flexible response, and backlog.
- `balance_dc_step` reports a power residual in kW and a separate rigid unserved amount. Energy-balance acceptance must integrate residual by `dt` and account for rigid unserved demand; the instantaneous residual alone is not an energy closure certificate.
