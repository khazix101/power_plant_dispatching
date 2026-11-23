# B `SiteStrategy` adapter v1

`src.game.site_strategy.CheckpointSiteStrategy` adapts the existing discrete
price policy to A's v1 `SiteStrategy.decide(site_id, forecast_window, state,
constraints)` call. It requires an explicit checkpoint path and exact
`constraints.price_levels`; there is no fixed/rule fallback. The default return
is a `SiteAction`-equivalent mapping with `site_id`, current `timestamp`,
`price_yuan_per_kwh`, `storage_kw`, and `requested_ev_kw`. An `action_factory`
can be supplied to construct A's dataclass where the contract package is
available.

The checked fixed checkpoint is
`local/b-price-smoke-74ee75b/policy-11.pt`, SHA-256
`d667eba06d4c53ce0e3ea1fda6184b89ae0452136211345620492bd44a108c1f`, trained
from source revision `74ee75b32acf775902dcaec0d72ad2bb6d3a1e38`. Its feature
dimension is 21, action horizon is 1, and locked price tiers are
`[1.5, 1.7, 1.9, 2.1, 2.3, 2.5]`. The checkpoint is an ignored local artifact;
provide that artifact explicitly in the integrating checkout. The strategy
exposes version, strategy/checkpoint hashes, revision, price tiers, and feature
and action horizons through `strategy_metadata`.

The model was trained with horizon H=1. For a longer D forecast, the adapter
validates the whole timestamp axis and each row's numeric forecast values and
source/quality summary, then maps only the current H=1 prefix to the existing
21-feature observation. It never reshapes or pads the observation. Shorter
windows, malformed timestamps, unavailable forecast values, missing/stale/
invalid quality, missing SOC/SOH or assets, unknown price tiers, wrong site,
trace hash shape, and invalid step metadata are rejected. Per-call `calls`
records received/used/truncated horizons, observation hash and SOC/SOH feature
values, source-quality hash, step and trace identity, and network/safety summary
availability. Fields such as `pv_actual_kw` are ignored and cannot affect the
observation.

Current SOC and SOH are model inputs. The Stage 2 common replay contract keeps
nominal battery capacity for this model and passes SOH as an observation; it
does not apply SOH a second time to capacity. Candidate `storage_kw` follows A's
sign convention: positive means discharge and negative means charge. It is
derived from the Agent's existing price-response/leader/safety path, then must
still be independently projected and validated by D. `requested_ev_kw` is the
checkpoint-selected price response capped to the current forecast and declared
EV service limit; D owns final execution and measurement. Candidate storage is
the previous-step storage feature on subsequent calls, because the v1 strategy
callback does not receive D's prior execution ledger.

The checkpoint was trained against a synthetic EV utility of 1.9 CNY/kWh, not
a calibrated market-response model. Its tiers must exactly match the shared
replay tiers, which may require the integration fixture to use the checkpoint
tiers across all methods. The checkpoint supports only a one-step model
observation/action; D windows longer than one step are deliberately truncated
to that trained horizon. This is engineering compatibility evidence, not
multi-site performance, field safety, or long-horizon policy validation.

Validation commands from this checkout:

```powershell
& D:/miniconda3/python.exe -m unittest parper.test.test_price_agent -v
& D:/miniconda3/python.exe -m unittest parper.test.test_site_strategy -v
& D:/miniconda3/python.exe -m compileall -q src scripts parper/test
```
