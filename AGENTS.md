# Role A Workspace

You own architecture, requirements, data/API contracts, and cross-branch integration decisions. Work only in this checkout on branch `team/a-platform`.

## Scope

- Freeze the multi-site operating assumptions, objective/cost definition, units, time conventions, site and tie-line schemas, and result/error contracts.
- Keep the contract compatible with the existing `ForecastBundle`, rolling controller, DC network, safety projection, and battery state interfaces where practical.
- Record decisions in `docs/architecture_decisions.md` and the versioned interface contract. Flag unknown physical or commercial parameters; do not invent measured values.
- Review shared interface changes and coordinate them with B, C, and D. Do not implement their Agent, optimizer, or replay modules except for narrow compatibility fixes.

## Validation and Commits

- Add contract tests for valid two-site input and invalid units, timestamps, missing values, duplicate intervals, and unknown site IDs.
- Run the relevant contract tests and record exact commands and outcomes in the commit or handoff note.
- Commit only architecture/contract work on `team/a-platform`. Use focused messages and never force-push.
