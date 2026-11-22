# Role C Workspace

You own the multi-site centralized coordination model. Work only in this checkout on branch `team/c-multisite`.

## Scope

- Extend the existing site model through explicit site-indexed components and the shared contract. The first deliverable is a centralized Pyomo model using the configured HiGHS-compatible solver.
- Model local balances and storage bounds plus only the inter-site coupling supported by configured topology and ratings: shared import/export limits, tie-line exchange, and agreed fairness or priority constraints.
- Keep independent per-site operation as a comparison baseline. Do not label independent schedules as jointly optimal.
- Use synthetic topology only for clearly labeled tests. Do not claim field feasibility until real line and device ratings are validated.
- Document model size, solver status, infeasibility, and runtime. Treat distributed coordination as a later decision unless the centralized benchmark demonstrates a concrete scaling need.

## Validation and Commits

- Test hand-computable two-site cases and four-site cases; verify site/global balances, SOC and power limits, tie-line bounds, infeasible status, and expected response to tightened constraints.
- Run relevant regression tests and record exact commands and outcomes. Commit only multi-site optimization work on `team/c-multisite`; use focused messages and never force-push.
