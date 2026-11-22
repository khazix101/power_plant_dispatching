# Team Workspaces

Four local Git worktrees are created inside `workspaces/`. Each is a separate checkout with its own branch and working tree:

| Role | Path | Branch | Scope |
|---|---|---|---|
| A | `workspaces/A` | `team/a-platform` | Architecture, requirements, data/API contracts, integration decisions |
| B | `workspaces/B` | `team/b-agent` | Price and dispatch Agent, training, safety adapter |
| C | `workspaces/C` | `team/c-multisite` | Centralized multi-site model and coordination constraints |
| D | `workspaces/D` | `team/d-validation` | Data audits, shared replay, benchmark harness, integration tests |

## Working Rules

1. Work only in the checkout assigned to your role. Do not edit the integration checkout or another role's worktree.
2. Do not switch branches inside a role worktree. Each worktree is already attached to its role branch.
3. Commit only changes within your task scope. Use focused commits such as `feat(agent): add discrete price adapter` and push the current role branch.
4. Coordinate shared API or model changes through A. Keep contract tests and the corresponding role notes in the same branch as the change.
5. Do not add local/private data, `.env` files, credentials, temporary runs, or review screenshots. Keep experiment inputs and package/solver versions documented.
6. Integrate through reviewed pull requests into the agreed integration branch. Do not force-push shared role branches.

The worktrees provide separate Git checkouts and branch history; they are workflow isolation, not operating-system access-control boundaries. Assign OS accounts/folder permissions separately if hard access restrictions are required.

## Integration Sequence

Create all role branches from the same uploaded project baseline. A owns cross-branch API decisions; B and C can develop in parallel after the contract is frozen; D maintains the paired replay and regression gates. Integrate changes only after the corresponding smoke tests pass and the result manifest records source revision, inputs, random seed, and solver status.
