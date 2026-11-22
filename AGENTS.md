# Role B Workspace

You own the price and dispatch Agent. Work only in this checkout on branch `team/b-agent`.

## Scope

- Reuse the current Python/PyTorch policy, rolling environment, price-response model, and safety projection; avoid building a second physical simulator.
- Specify and implement legal price actions, observed state, reward accounting, episode boundaries, training split, seeds, checkpoint format, and an adapter to the shared replay contract.
- Start with price actions. Add storage actions only after a separate ablation and ensure every physical action passes through the safety layer.
- Keep fixed-price, rule, and optimization baselines under the same information set and asset/state assumptions. Keep synthetic price-response assumptions clearly labeled.

## Validation and Commits

- Test legal action bounds, safety projection, deterministic seeded runs, checkpoint round trips, date-level train/validation/test isolation, and evaluation ledger fields.
- Report service, unmet demand, delivered-energy revenue, user cost, curtailment, storage/network losses, degradation cost, violations, projection rate, runtime, and multi-seed uncertainty.
- Run the relevant tests and record exact commands and outcomes. Commit only Agent work on `team/b-agent`; use focused messages and never force-push.
