# D Paired Replay and Release Checks

## Shared runner

`scripts/run_multisite_replay.py` writes a common input manifest and paired per-step ledgers. With no manifest it creates a four-step, two-site, half-hour synthetic case; all signals are tagged with a source and `data_class=synthetic`. For an external case, the JSON keeps A's v1 `MultiSiteInput` fields and adds D's site asset/initial state, legal price tiers, upstream cap/loss, seed, and provenance fields. D validates identical UTC-normalized timestamps, required units, source/status fields, unique site IDs, half-hour cadence, and equal forecast windows before invoking a method.

All method callbacks receive the same serialized trace hash, current/future forecast window, price levels, initial asset configuration, and shared system constraints. Each method evolves its own SOC/SOH state from the same supplied initial state. The callback returns one action per site with `price_yuan_per_kwh`, signed `storage_kw` (positive discharge, negative charge), optional `requested_ev_kw`, and optional solver status. Prices are projected to the nearest legal tier; storage and EV actions are projected to declared safe bounds and the projection is logged.

`adapt_site_strategies()` binds the v1 `SiteStrategy.decide(site_id, forecast, state, constraints)` protocol. `adapt_coordinator()` binds `Coordinator.coordinate(CoordinationRequest)` when A's contract package is present in the integrating checkout; a request builder/parser can be supplied if C publishes a different call shape. B's present single-site MDP adapter reports a planned reward after projection rather than actual execution, and C has no coordinator in its branch yet. The D branch therefore contains no trained B Agent adapter or C optimizer implementation, so the bundled arms are explicitly diagnostic heuristics. Agent and optimizer ranking is not authorized by this smoke replay.

The execution ledger applies the same dispatch step to every method. It reports EV requested/accepted/served/unserved/suppressed energy, base-load service, delivered-price revenue and user cost, renewable curtailment, storage conversion loss, upstream loss, degradation cost and energy, per-site SOC/SOH changes, constraint checks, projection count/rate, method and step runtime, source revision/tree hash, config/input/method hashes, seed, and failure/solver status. Base load has priority over accepted EV demand. EV service is allocated proportionally across sites when the common upstream resource is scarce.

The v1 runner models a shared copperplate bus and declared upstream import/export cap/loss. It rejects nonempty tie-line data and per-site transformer caps rather than dropping them. This is a synthetic accounting smoke boundary, not a physical topology validation. The energy identity tolerance is the frozen absolute `1e-6 kWh`; no adjustment was made.

## Output retention

Outputs go under ignored `local/results/d_validation/` and are never committed. Each run writes:

- `manifest.json`: exact input plus run hashes, code revision/tree hash, seed, and provider identities/configurations;
- `summary.json`: machine-readable aggregate metrics and method statuses;
- `execution_ledger.json`: per-step common input snapshots, raw/projected actions, per-site/system ledgers, and failure records;
- `service_summary.png`: compact service/unserved chart.

The output directory must be new. A repeat run with the same scenario gets a new path; if the directory already exists, writing fails before touching its contents. Provider exceptions, infeasible/nonconverged statuses, and marked missing intervals are retained and excluded from the steps that successfully executed; no fallback result is substituted. Failed evidence is not eligible for method ranking.

## Repeatable checks

```powershell
D:\miniconda3\python.exe scripts/audit_multisite_data.py --output local/results/d_validation/data_quality_audit.json
D:\miniconda3\python.exe scripts/run_multisite_replay.py --seed 17 --steps 4 --output local/results/d_validation/smoke-paired-<new-run-id>
D:\miniconda3\python.exe -m unittest parper.test.test_multisite_replay parper.test.test_multisite_data_audit -v
D:\miniconda3\python.exe parper/test/run_paper_tests.py
D:\miniconda3\python.exe -m compileall -q src scripts parper/test
```

`--missing-policy reject` is the default. For a diagnostic that must retain incomplete intervals, use `--missing-policy mark`; the runner writes the affected interval and quality status and does not fill values. Repeatability checks compare actions, states, and ledger values while excluding wall-clock runtimes.
