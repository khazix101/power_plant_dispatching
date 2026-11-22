# Power Plant Dispatching

Python research code for renewable-powered charging microgrid forecasting, storage dispatch, price response, battery health evaluation, and rolling optimization. The current implementation is primarily a **single-site DC microgrid prototype**. Multi-site coordination and a production-ready dispatch Agent are planned work, not completed capabilities.

## Project Areas

- `src/game/`: price response, optimization, rolling dispatch, DC network, safety projection, and Agent prototypes.
- `src/battery/`: battery state, degradation, and economic evaluation.
- `src/strategy/`: rule-based and MPC strategy components.
- `scripts/`: experiment, audit, replay, and reporting entry points.
- `parper/`: research manuscripts, experiment protocols, source figures, and focused regression tests.
- `docs/`: model notes, project context, and the [A-D development plan](docs/联合研发计划_A到D分工.md).
- `data/`: small example profiles. Private/raw datasets under `Dataset/` are intentionally excluded from Git.

## Environment

The codebase uses Python, NumPy/Pandas, Pyomo, and a compatible MILP solver such as HiGHS. PyTorch is required for neural Agent training. The available repository does not yet provide a single dependency lock file; record the Python/package/solver versions with each experiment. Do not assume that a locally available solver or data file exists on another machine.

## Tests

Run the repository's maintained test entry point from the repository root:

```powershell
python parper/test/run_paper_tests.py
```

Some experiment scripts require local datasets or solver installations and are not part of the short smoke suite.

## Team Workspaces

Use the linked checkouts under `workspaces/A`, `workspaces/B`, `workspaces/C`, and `workspaces/D`. Each checkout is attached to one role branch. Read `TEAM_WORKSPACES.md` before editing; make and push changes only from the assigned workspace and branch. The Git worktrees are local and ignored by the integration checkout.
