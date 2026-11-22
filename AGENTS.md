# Role D Workspace

You own data audits, the common rolling replay, baselines, and integration validation. Work only in this checkout on branch `team/d-validation`.

## Scope

- Audit each site's data coverage, timestamps, units, missingness, provenance, and parameter sources. Keep synthetic, forecast, and measured values distinguishable.
- Build or maintain a paired replay harness that supplies identical time windows, forecasts, initial states, assets, and accounting to the Agent and centralized optimizer.
- Preserve per-step ledgers, input/config manifests, source revisions, random seeds, solver statuses, and failed runs. Do not overwrite raw result evidence.
- Maintain fixed-price, rule, independent-site, and centralized-joint baselines; do not redefine B's reward or C's optimization objective.

## Validation and Commits

- Test time alignment, data quality failures, energy accounting, common-input parity, and repeatability on a small synthetic case before long runs.
- Keep local/private data and bulky generated runs out of Git. Save manifests and concise summaries instead.
- Run the relevant smoke/regression tests and record exact commands and outcomes. Commit only validation/replay work on `team/d-validation`; use focused messages and never force-push.
