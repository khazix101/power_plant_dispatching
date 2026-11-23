# D Data Dictionary and Replay Contract

## Evidence scope

The audit reads only the five tracked example files named in `data/`. It never enumerates or opens `Dataset/`, `output/`, `tmp/`, or experiment result directories. Current files describe one generic single-site scenario; none has a `site_id`, equipment record, or privacy classification. They are not evidence of multiple measured sites.

The D replay consumes the serialized v1 wire shape in A's `src/multisite/contract.py` (`contract_version=1.0.0`, 30-minute intervals, explicit IANA timezone, source-tagged site definitions, interval signals, and tie-line definitions). D keeps the consumer at the JSON boundary and adds replay-only fields below; A remains the owner of the shared contract definition.

## Tracked `data/` files

| File | Meaning in loader/provider | Columns and units | Current type | Known ambiguity |
|---|---|---|---|---|
| `A.csv` | Wind profile | `valid_time` (naive local-like timestamp), `power_kW` (kW) | Forecast/example | No timezone or site ID |
| `B.csv` | PV profile | `valid_time` (naive local-like timestamp), `power_kW` (kW) | Forecast/example | No timezone or site ID |
| `C.csv` | Loader says load; `DailyCsvForecastProvider` uses as base EV demand | `valid_time` (naive local-like timestamp), `power_kW` (kW) | Forecast/example | EV-only vs aggregate load is unresolved |
| `D.csv` | Weather | `valid_time` (naive local-like timestamp), `temperature_c` (°C), `weather_type` (category) | Forecast/example | No station, source, or timezone metadata |
| `E.csv` | Holiday calendar | `date` (calendar date) | Calendar/example | No source or locality metadata |

The current snapshots contain 24 rows each for A–D, spanning `2024-01-01 00:00:00` through `2024-01-01 23:00:00` at a one-hour cadence. The E calendar contains 30 dates. The audited profiles have no missing cells or duplicate time keys. A–D timestamps are timezone-naive; all four contain hourly rather than the shared 30-minute replay interval. These files are not mapped to named sites.

## A v1 signals carried into each half-hour interval

| Signal | Unit | Intended role |
|---|---|---|
| `pv_forecast_kw`, `wind_forecast_kw` | kW | Forecast inputs to every method |
| `load_forecast_kw`, `ev_forecast_kw` | kW | Non-EV/base and EV request forecast, kept distinct |
| `price_yuan_per_kwh` | CNY/kWh | Reference/forecast price information |
| `grid_buy_price_yuan_per_kwh`, `grid_sell_price_yuan_per_kwh` | CNY/kWh | Grid settlement buy/sell price forecasts |
| `ev_delivery_price_yuan_per_kwh` | CNY/kWh | Forecast EV delivery tariff; action still selects from shared legal tiers |
| `soc`, `soh` | fraction | Initial or measured state; Stage 2 evolves SOC and holds SOH constant |
| `reserve_margin_kwh` | kWh | Nominal-capacity reserve margin: `(SOC - soc_min) * battery_capacity_kwh` |
| optional `*_actual_kw` | kW | Measured outcome only; never substituted for forecast input |
| optional `grid_import_kw`, `tie_flow_kw` | kW | Network/system observations where supplied |

Each signal carries `value`, `unit`, `source`, and `status` (`observed`, `forecast`, `missing`, `assumed`, `stale`, or `invalid`); assumed values also carry an explanation. D's default policy rejects missing, stale, or invalid required inputs. `--missing-policy mark` retains the affected interval with its quality status, skips method execution for that interval, and does not impute a zero.

## D replay-only manifest fields

The replay wrapper adds `data_class` (`synthetic`, `forecast`, `measured`, or `mixed`), `seed`, `run_id`, `site_configs`, `price_levels_yuan_per_kwh`, `upstream_import_limit_kw`, known nonnegative `upstream_export_limit_kw` (zero disables export; null is unknown and rejected), `upstream_loss_fraction`, and `input_sha256`. Each `site_configs[site_id]` contains:

- `assets`: battery capacity/power (kWh/kW), SOC bounds/reserve (fraction), charge/discharge efficiencies (fraction), EV service limit (kW), degradation cost (CNY/kWh throughput), and a legacy SOH loss coefficient retained for compatibility but not applied in Stage 2.
- `initial_state`: common start SOC and SOH for every method on that site.

Input hashes cover the whole manifest except the hash and output `run_metadata` fields; configuration hashes cover site/asset/price/system limits and named provider configurations. Put model/checkpoint hashes and model-specific settings in `method_configs[method_id]` so an external Agent or optimizer's identity is reproducible. Result records additionally include source revision, source-tree hash, dirty status, method/provider identity, each step's common input snapshot, raw and projected actions, per-site and system ledgers, solver/execution/failure statuses, seed, runtime, and terminal SOC/SOH. Output folders are create-once; use a new folder for every rerun so failed evidence remains intact.

## Accounting definitions and boundary

- EV `requested_kwh` is the original EV forecast times interval length. `accepted_kwh` is the provider's requested service after the input request and site service cap. `served_kwh` is allocated after base-load priority and upstream limits. `unserved_kwh = requested - served`; `suppressed_kwh = requested - accepted` is also reported separately.
- Delivered revenue and user energy cost are both served EV kWh multiplied by the executed price tier. This is a simple settlement ledger, not B's reward.
- Storage conversion loss uses declared charge/discharge efficiencies and nominal capacity. Stage 2 holds SOH constant and reports zero degradation energy loss; degradation cost uses the declared throughput coefficient. The physical identity includes storage-energy change and conversion losses, without SOH capacity fade.
- The model `synthetic_lossy_tieline_transformer_nominal_capacity_v1` enforces site transformer and shared upstream limits plus declared lossy tie paths. Positive tie flow follows from→to and uses the export rating; reverse uses the import rating. Sender energy minus receiver energy is the path loss. Unknown/null ratings block replay; zero disables a direction. Upstream loss and tie loss are recorded separately; per-site residuals use delivered terminal energy, while the system identity includes both network losses. This transport boundary does not model voltage, reactive power, or protection.
- `constraint_violation_count` counts candidates changed by device/network projection; `projection_rate` divides projected site and tie candidates by all site and tie actions. Method runtime includes provider plus executor; site-level provider runtime is reported only when an adapter can attribute it per site. Solver failure/infeasibility is retained in the method and step status, never replaced by another method's result.

The plain reference suite contains explicitly labeled diagnostic heuristics. `--stage2-smoke` instead runs fixed-price reference, actual B `CheckpointSiteStrategy`, an independent-site synthetic reference heuristic, and actual C `CentralizedCoordinator`. The independent arm is not C's optimizer. B requires the untracked pinned local checkpoint documented in `replay_validation_d.md`; no fallback policy is substituted. Local A/B/C plugin roots, revisions, and source hashes are recorded in `integration_sources`. Common trace/assets/initial-state hashes are written per arm. Synthetic smoke keeps `ranking_authorized=false`; it does not establish Agent profit, policy ranking, calibrated economics, or field electrical safety.
