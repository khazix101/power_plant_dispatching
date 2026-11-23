# Multi-Site Scheduling Contract v1.0.0

Implementation: `src.multisite.contract`. Use `MultiSiteInput.from_dict(payload)` to validate and `to_dict()` for JSON-compatible serialization. `ContractError` messages are stable, human-readable input failures. Contract version is `1.0.0`.

## Frozen boundary

2-4 sites; 30-minute intervals timestamped at interval start; configurable common prediction horizon (48 intervals/24 h is a candidate experiment, not an asserted field fact). IDs are caller-owned, stable identifiers. Time strings must include an offset matching the input IANA timezone. Sites share the same strictly increasing interval axis.

Power uses kW, energy kWh, SOC/SOH fractions in [0,1], price CNY/kWh, and duration minutes. Loads, EV charging, grid import, and storage charging are positive consumption; `SiteAction.storage_kw` is positive for discharge and negative for charge. Tie-line flow is positive from `from_site` to `to_site` and bounded by `export_limit_kw`; reverse flow is bounded by `import_limit_kw`. A zero directional limit disables that direction; null means unknown and blocks optimization. `price_yuan_per_kwh` is the published/planned EV response tariff. Grid buy, grid sell, and realized EV delivery prices are separate optional signals.

Required signals per interval: `pv_forecast_kw`, `wind_forecast_kw`, `load_forecast_kw`, `ev_forecast_kw`, response `price_yuan_per_kwh`, `grid_buy_price_yuan_per_kwh`, `grid_sell_price_yuan_per_kwh`, `ev_delivery_price_yuan_per_kwh`, `soc`, `soh`, `reserve_margin_kwh`. The three settlement prices are distinct. A required price can be explicitly marked missing, but a solver requiring it must reject that input. Actual measurements and grid/tie-line flow signals are optional. Every signal carries `value`, `unit`, `source`, and `status`. `missing` requires null; `assumed` requires an explanation. Missing never means zero. `reserve_margin_kwh` is current available energy above the configured technical SOC minimum, not an additional target; extra reserve requirements belong in configuration as `reserve_requirement_kwh`.

Site and tie-line definitions require a non-empty `source` provenance field. They expose optional, named static values for PV/wind ratings, transformer import/export ratings, battery energy/power/efficiencies/SOC-SOH bounds, EV power limit and minimum service fraction. For transformer import/export ratings, `null` means unknown and blocks any execution-feasibility claim; `0` is a known rating that explicitly disables that direction; a positive value is its known kW cap. Unknown field values stay null and must not be treated as measured defaults. v1 replay uses nominal battery capacity for SOC transitions and reports SOH separately; it does not multiply capacity by SOH. `thermal_limits_verified=false` means thermal feasibility is unverified.

## Interfaces

- `SiteStrategy.decide(site_id, forecast, state, constraints) -> SiteAction` returns a candidate action, not an executable safety guarantee. Integrators should pass existing `ForecastBundle` and `BatteryState` through an adapter. For half-hour operation the downstream `GameCase.interval_hours` must be 0.5; current ForecastBundle field name `horizon_hours` is legacy and must not be interpreted as the interval duration.
- `Coordinator.coordinate(CoordinationRequest) -> CoordinationResult` accepts the validated shared input and caller configuration. `config` carries legal price levels, site assets/initial states/priority and fairness policy, constraints, method/time limit and strategy versions.
- Ledger rows are mappings for extensibility and must record run/contract/config/model versions, interval, site and tie-line flows, requested and safe actions, measurements/forecasts with source/status, cost components, constraint residuals, and outcome. Failed, partial, infeasible, and invalid-input runs retain an explicit status and failure code.

See [architecture_decisions.md](architecture_decisions.md) for decisions, unknown parameters and limitations. A valid synthetic two-site payload and negative cases are executable in `parper/test/test_multisite_contract.py`.
