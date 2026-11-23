# Multi-site integration adapter contract v1

This document freezes the Stage 2 adapter boundary around the existing
`src.multisite.contract` **wire contract v1.0.0**. It does not change the
`MultiSiteInput` schema. Power values are kW, energy values are kWh, prices are
CNY/kWh, SOC/SOH and fractions are unitless, and interval timestamps identify
the start of a 30-minute interval in the declared IANA timezone.

The executable synthetic two-site fixture is
[`parper/test/fixtures/multisite_integration_v1.json`](../parper/test/fixtures/multisite_integration_v1.json).
It contains a valid wire request, coordinator configuration, first-interval
candidate actions, and a signed tie-line flow. Values are test-only and do not
represent site measurements or validated operating settings. It is a schema
and adapter-shape fixture, not a runnable optimization case: unknown site
ratings and upstream limits remain null and must be supplied as confirmed
values or explicitly labeled synthetic values before a solver may run.

## D → B: `SiteStrategy`

Call once per site and decision step:

```text
SiteStrategy.decide(site_id, forecast_window, state, constraints) -> SiteAction
```

| Input | Required shape and meaning |
| --- | --- |
| `site_id` | Configured, stable site ID from the shared request. |
| `forecast_window` | Non-empty sequence beginning at the current half-hour. Each row has `timestamp`; finite numeric `pv_forecast_kw`, `wind_forecast_kw`, `load_forecast_kw`, and `ev_forecast_kw`; and a `source_quality` summary for those signals (source and quality/status must remain distinguishable from values). Rows use the common replay time axis. |
| `state` | Current `soc` and `soh`, each a finite fraction in `[0, 1]`. These are the state at the first forecast interval. |
| `constraints.contract_version` | The shared contract version, currently `1.0.0`. |
| `constraints.trace_sha256` | 64-character lowercase hexadecimal SHA-256 for the replay inputs/trace. |
| `constraints.step_index` | Zero-based non-negative integer decision index. |
| `constraints.dt_hours` | Interval duration in hours; `0.5` for this 30-minute contract. This is not forecast horizon length. |
| `constraints.assets` | The site's declared asset summary, with unknown values left explicitly unknown. |
| `constraints.price_levels` | Non-empty list of finite legal response prices in CNY/kWh. |
| `constraints.network_summary` | Network and site-limit summary. Unavailable information is explicit, e.g. `{"available": false, "reason": "not provided"}`; it must never be replaced with a fabricated zero constraint. |
| `constraints.safety_summary` | Safety/dispatch-limit summary and availability/provenance. It is advisory input to B; D remains the execution safety authority. |

The returned `SiteAction` is a candidate only:

| Field | Type / unit | Meaning |
| --- | --- | --- |
| `site_id` | string | Must equal the requested site ID. |
| `timestamp` | ISO-8601 string with matching timezone offset | Must equal the current interval start. |
| `price_yuan_per_kwh` | finite number, CNY/kWh | Must be one of the supplied legal `price_levels`. It is the strategy/input response tariff, not a price elasticity or a realized delivery settlement. |
| `storage_kw` | finite number, kW | Positive discharges storage; negative charges it. D must independently project and validate it. |
| `requested_ev_kw` | finite number or null, kW | Candidate requested EV service/charging power; D validates and limits it. |

Reject a wrong-site action, wrong-interval timestamp, non-finite value, or
price outside the supplied levels. Do not silently clamp or reinterpret it at
the adapter boundary; execution-layer projection is recorded separately.

## D → C: rolling coordinator request

Each call carries the current and remaining future intervals only. Its input,
initial state and timestamps must come from the same replay manifest and common
time axis. Keep the source `contract_version` at `1.0.0` and retain that
version in the request and result.

`CoordinationRequest.config` must explicitly contain all of these keys:

| Key | Required shape / unit |
| --- | --- |
| `provenance` | Object with exactly `data_class` and `source`; `data_class` is `synthetic`, `measured`, or `anonymized`, and `source` is a non-empty manifest/source identifier. Detailed code/source hashes remain in result metadata. |
| `method` | Supported identifier: `centralized` or `independent`. Unknown methods are rejected as invalid input. |
| `price_levels` | Non-empty list of unique positive finite prices in CNY/kWh; actions must use one of these. |
| `service_gap` | Object with exactly `max_ev_service_fraction_gap`, a finite fraction in `[0, 1]`. It bounds the absolute difference between normalized EV-service fractions for eligible sites. Other or unknown keys are invalid in v1. The calibrated value remains caller-owned. |
| `time_limit_seconds` | Finite non-negative number of seconds. |
| `sites` | Object keyed by exactly the configured site IDs. Each site object includes the fields below. |

Every `config.sites[site_id]` object must include:

| Field | Unit / domain |
| --- | --- |
| `soc_terminal` | SOC fraction in `[0, 1]`. |
| `reserve_requirement_kwh` | Non-negative kWh. This is an explicit configured reserve target, not the wire signal `reserve_margin_kwh`. |
| `degradation_per_kwh` | Non-negative caller-supplied CNY/kWh cost. |
| `base_min_fraction` | Fraction in `[0, 1]`. |
| `base_unserved_cost` | Non-negative caller-supplied CNY/kWh cost for unserved base load. |
| `ev_unserved_cost` | Non-negative caller-supplied CNY/kWh cost for unserved EV service. |

All values above must be declared; C must not silently substitute business or
device parameters. Cost calibration, terminal target, and reserve target are
not inferred from the wire schema.

## C → D: result and first-interval metadata

`CoordinationResult.ledger` retains the full rolling optimization detail,
including solver status and enough source/version context to audit each
interval. `metadata.actions` and `metadata.tie_line_flows_kw` describe only the
first interval of the current request:

| Result field | Required shape / meaning |
| --- | --- |
| `contract_version`, `run_id` | Match the request's version and run identity. |
| `status` | Preserve the actual solver/run outcome, including infeasible, timeout, partial, or failure states. Never relabel a failed run as success. |
| `failure_code` | Null only for a successful result; otherwise retain a stable, non-empty failure reason/code. |
| `ledger` | Sequence of full-run interval/site/line optimization records; do not truncate it to the first interval. |
| `metadata.actions` | Object keyed by site ID. Each value has the `SiteAction` shape above and timestamp equal to the first interval in the rolling request. Actions are optimizer candidates, not safe dispatch commands. |
| `metadata.tie_line_flows_kw` | Object keyed by configured line ID, with one finite signed kW value per line for the first interval. Positive is `from_site → to_site`; negative is reverse direction. Enforce the corresponding directional limit and loss model. |
| `metadata.safe_to_dispatch` | Always `false` in Stage 2. D independently revalidates device, inter-site, and upstream constraints before execution. |

The returned action price is supplied by the request/strategy. C optimizes EV
service as a demand-response target and must not invent a price-elasticity
curve. Any malformed or missing required input/config/action/flow is rejected
or retained as an explicit failed result; it must not produce a fabricated
feasible result. Infeasible and timeout outcomes remain visible with their
solver status and failure information. D does not execute a failed or
unvalidated result.

## Conformance examples

The fixture file is a minimal valid example. These are representative invalid
adapter fragments; each must fail conformance before execution:

```json
{"config": {"method": "centralized"}}
```

It omits the required coordinator configuration fields.

```json
{"site_id": "site-1", "timestamp": "2026-01-01T00:30:00+08:00",
 "price_yuan_per_kwh": 0.7, "storage_kw": 1.0, "requested_ev_kw": 0.5}
```

It is invalid as the first action when the replay starts at
`2026-01-01T00:00:00+08:00` (wrong timestamp; it is also invalid if `0.7` is
not a legal price level).

```json
{"tie_line_flows_kw": {"link-1": "reverse"}}
```

It is invalid because a tie-line flow must be a finite signed number in kW;
use a negative numeric value to express reverse flow.

## Version, state, and unresolved inputs

The wire schema remains `MultiSiteInput` v1.0.0. The adapter contract in this
document is also v1 and adds no wire fields. Candidate actions and solver
metadata do not authorize dispatch; only D's independently checked execution
result can do that. Validation failures stay failures, and solver infeasibility
or timeout is never converted to a feasible/success status.

No real site or commercial values are asserted here. The following still
require an owner/source confirmation before field use: transformer and
upstream import/export limits; tie-line endpoints, directional ratings,
efficiencies/losses; PV/wind and battery ratings, efficiencies and SOC/SOH
boundaries; EV service and minimum-service rules; current price tiers and the
three settlement price series; terminal SOC/reserve targets; degradation and
unserved-service costs; any fairness/priority policy beyond the configured
EV-service fraction gap; network/safety-summary completeness; and the
source/quality criteria for forecasts. For site transformer import/export
limits, `null` means unknown and blocks any execution-feasibility claim; `0`
explicitly disables that direction and a positive value is its known kW cap.
Unknown network parameters must remain explicitly unavailable and must block
claims of feasibility.

The required config values `service_gap`, `base_unserved_cost`, and
`ev_unserved_cost` still require owner confirmation and calibration before
field use. The shared interpretation of `service_gap` is the normalized EV
service-fraction spread above; the fixture's synthetic values are examples
only.
