# D Data Dictionary and Replay Contract

## Evidence scope

The audit reads only the five tracked example files named in `data/`. It never enumerates or opens `Dataset/`, `output/`, `tmp/`, or experiment result directories. Current files describe one generic single-site scenario; none has a `site_id`, equipment record, or privacy classification. They are not evidence of multiple measured sites.

The D replay consumes the serialized v1 wire shape in A's `src/multisite/contract.py` (`contract_version=1.0.0`, 30-minute intervals, explicit IANA timezone, site definitions, interval signals, and tie-line definitions). At this checkout A's contract source is uncommitted and its planned `docs/multi_site_contract.md` is absent. D therefore keeps the consumer at the JSON boundary and adds replay-only fields below; A remains the owner of the shared contract definition.

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
| `soc`, `soh` | fraction | Initial or measured state; execution evolves state per method |
| `reserve_margin_kwh` | kWh | Reserve metadata |
| optional `*_actual_kw` | kW | Measured outcome only; never substituted for forecast input |
| optional `grid_import_kw`, `tie_flow_kw` | kW | Network/system observations where supplied |

Each signal carries `value`, `unit`, `source`, and `status` (`observed`, `forecast`, `missing`, `assumed`, `stale`, or `invalid`); assumed values also carry an explanation. D's default policy rejects missing, stale, or invalid required inputs. `--missing-policy mark` retains the affected interval with its quality status, skips method execution for that interval, and does not impute a zero.

## D replay-only manifest fields

The replay wrapper adds `data_class` (`synthetic`, `forecast`, `measured`, or `mixed`), `seed`, `run_id`, `site_configs`, `price_levels_yuan_per_kwh`, `upstream_import_limit_kw`, optional `upstream_export_limit_kw` / `upstream_loss_fraction`, and `input_sha256`. Each `site_configs[site_id]` contains:

- `assets`: battery capacity/power (kWh/kW), SOC bounds/reserve (fraction), charge/discharge efficiencies (fraction), EV service limit (kW), degradation cost (CNY/kWh throughput), and SOH loss coefficient (fraction/kWh throughput).
- `initial_state`: common start SOC and SOH for every method on that site.

Input hashes cover the whole manifest except the hash and output `run_metadata` fields; configuration hashes cover site/asset/price/system limits and named provider configurations. Put model/checkpoint hashes and model-specific settings in `method_configs[method_id]` so an external Agent or optimizer's identity is reproducible. Result records additionally include source revision, source-tree hash, dirty status, method/provider identity, each step's common input snapshot, raw and projected actions, per-site and system ledgers, solver/execution/failure statuses, seed, runtime, and terminal SOC/SOH. Output folders are create-once; use a new folder for every rerun so failed evidence remains intact.

## Accounting definitions and boundary

- EV `requested_kwh` is the original EV forecast times interval length. `accepted_kwh` is the provider's requested service after the input request and site service cap. `served_kwh` is allocated after base-load priority and upstream limits. `unserved_kwh = requested - served`; `suppressed_kwh = requested - accepted` is also reported separately.
- Delivered revenue and user energy cost are both served EV kWh multiplied by the executed price tier. This is a simple settlement ledger, not B's reward.
- Storage conversion loss uses declared charge/discharge efficiencies. Degradation cost and SOH loss use explicit per-site manifest coefficients. System energy identity also includes SOH-related stored-energy loss and state-of-charge change.
- Network loss currently models only a declared upstream loss fraction. It is allocated to importing sites for site-level reporting; site energy residual is measured at the ideal shared-bus boundary and the system residual includes that upstream loss. `tie_lines` and per-site transformer limits are rejected until a runner can enforce them; they are never silently ignored. The shared bus is an explicit synthetic copperplate simplification, not C's topology or optimization model.
- `constraint_violation_count` records executor hard-constraint violations; `projection_rate` is projected actions divided by all site actions. Method runtime includes provider plus executor; site-level provider runtime is reported only when an adapter can attribute it per site. Solver failure/infeasibility is retained in the method and step status, never replaced by another method's result.

Fixed-price, rule, independent-site, and centralized-joint labels in the bundled smoke suite name transparent diagnostic heuristic policies. They are not B's trained Agent, C's independent optimizer, or C's centralized objective. B currently has a single-site `RollingSFSEMDPEnv` whose reward is explicitly planned after safe projection, not measured DC execution; it is not an execution adapter for this ledger. Provider adapters allow the production APIs to be bound once those decision/execution boundaries are available in the integrating branch.
