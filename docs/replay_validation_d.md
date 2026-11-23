# D Paired Replay and Release Checks

## Shared runner

`scripts/run_multisite_replay.py` writes a common input manifest and paired per-step ledgers. With no manifest it creates a four-step, two-site, half-hour synthetic case; all signals are tagged with a source and `data_class=synthetic`. For an external case, the JSON keeps A's v1 `MultiSiteInput` fields and adds D's site asset/initial state, legal price tiers, upstream cap/loss, seed, and provenance fields. D validates identical UTC-normalized timestamps, required units, source/status fields, unique site IDs, half-hour cadence, and equal forecast windows before invoking a method.

All method callbacks receive the same serialized trace hash, current/future forecast window, price levels, initial asset configuration, and shared system constraints. Each method evolves its own SOC from the same supplied initial state and holds SOH constant. The callback returns one action per site with `price_yuan_per_kwh`, signed `storage_kw` (positive discharge, negative charge), optional `requested_ev_kw`, and optional solver status. Prices are projected to the nearest legal tier; storage and EV actions are projected to declared safe bounds and the projection is logged.

`adapt_site_strategies()` supplies B's frozen callback with the common forecast window, signal source/quality, current SOC/SOH, contract/trace/step/time fields, legal price levels, assets, and explicit network/safety availability. `adapt_coordinator()` builds A's rolling `CoordinationRequest` with declared provenance, method, price levels, service gap, time limit, and per-site business parameters; it retains C's first-interval `metadata.actions`, signed `tie_line_flows_kw`, full solver ledger, source metadata, and failure status. D independently validates candidates before execution.

The Stage-2 CLI runs four arms: fixed-price reference, actual B `CheckpointSiteStrategy`, an independent-site **synthetic reference heuristic**, and actual C `CentralizedCoordinator`. The independent arm is not C's independent optimizer. Plain runs without `--stage2-smoke` still use the explicitly labeled diagnostic reference policies. All synthetic results keep `ranking_authorized=false` and establish integration/accounting correctness only.

The execution model is `synthetic_lossy_tieline_transformer_nominal_capacity_v1`. Signed tie flow is positive from `from_site` to `to_site`, capped by `export_limit_kw`; reverse flow is capped by `import_limit_kw`. Each path loses its declared `loss_fraction`. Per-site transformer and shared upstream direction limits are enforced. Zero direction ratings disable the direction; null ratings remain unknown and block replay. Storage uses nominal capacity, declared charge/discharge efficiencies, and constant SOH; throughput degradation cost remains reported, while SOH capacity fade and degradation energy loss are outside this Stage-2 model.

The ledger records input snapshots; raw, projected, and executed site/tie actions; tie terminal energy and losses; source/config/input hashes; solver/failure statuses; and per-site/system residuals. Each site's base load has priority over its accepted EV demand; constrained upstream imports are allocated proportionally to capped site deficits. Failed provider or accounting results are retained and never replaced by a baseline. The absolute energy tolerance remains `1e-6 kWh`. This synthetic transport model does not validate voltage, reactive power, electrical protection, or field safety.

## Stage-2 CLI prerequisites and command

Run from D's checkout using Python with NumPy, PyTorch, SciPy/HiGHS, and the project's normal dependencies. Supply A, B, and C checkout roots explicitly: A must contain `src/multisite/contract.py`; B must contain `src/game/site_strategy.py` and its price Agent dependencies; C must contain `src/game/multisite_coordinator.py` and `multisite.py`. The CLI extends `src` and `src.game` search paths for these local plugin checkouts without copying their code. The manifest records each root, Git revision, and actual Python source-tree hash, including uncommitted source differences.

B's local checkpoint must exist at `local/b-price-smoke-74ee75b/policy-11.pt` beneath one supplied root. Its pinned SHA-256 is `d667eba06d4c53ce0e3ea1fda6184b89ae0452136211345620492bd44a108c1f`, with ordered price levels `[1.5, 1.7, 1.9, 2.1, 2.3, 2.5]` and H=1. This checkpoint is intentionally untracked; a fresh clone cannot reproduce the actual B smoke without separately provisioned matching local evidence. The runner fails if it is absent or has a different hash.

```powershell
D:/miniconda3/python.exe scripts/run_multisite_replay.py --stage2-smoke --integration-root ../A --integration-root ../B --integration-root ../C
```

The default synthetic manifest has two sites, four half-hour intervals, and one lossy bidirectional line. Default outputs receive a unique ignored local path. Each arm reports identical common trace, asset, and initial-state hashes. The command prints provider identities, completion/failure status, projection/violation counts, residuals, and output path. `--output` can name a new directory explicitly; existing directories are rejected.

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

The command-level test `parper.test.test_multisite_stage2_cli` is opt-in. Set
`D_STAGE2_INTEGRATION_ROOTS` to explicit A/B/C root paths separated by the
platform path separator (`;` on Windows), then run
`python -m unittest parper.test.test_multisite_stage2_cli -v`.
It clearly skips when roots or the local checkpoint/modules are unavailable;
configured runs retain unique ignored evidence and check actual provider types,
four completed steps per arm, common hashes, and the frozen residual threshold.
