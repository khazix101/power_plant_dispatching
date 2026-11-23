"""Paired, rolling replay over a shared multi-site forecast trace.

This consumer intentionally accepts A's JSON wire shape without importing or
copying A's contract implementation. The current executor is a diagnostic
copperplate model with an upstream import/export cap; it rejects declared
tie-lines rather than silently ignoring topology that it cannot model.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


SCHEMA_VERSION = "d-common-replay-v1"
LEDGER_TOLERANCE_KWH = 1e-6
SIGNAL_UNITS = {
    "pv_forecast_kw": "kW", "pv_actual_kw": "kW",
    "wind_forecast_kw": "kW", "wind_actual_kw": "kW",
    "load_forecast_kw": "kW", "load_actual_kw": "kW",
    "ev_forecast_kw": "kW", "ev_actual_kw": "kW",
    "price_yuan_per_kwh": "CNY/kWh", "soc": "fraction",
    "soh": "fraction", "reserve_margin_kwh": "kWh",
    "grid_import_kw": "kW", "tie_flow_kw": "kW",
}
REQUIRED_SIGNALS = {
    "pv_forecast_kw", "wind_forecast_kw", "load_forecast_kw",
    "ev_forecast_kw", "price_yuan_per_kwh", "soc", "soh",
    "reserve_margin_kwh",
}
FORECAST_SIGNALS = ("pv_forecast_kw", "wind_forecast_kw", "load_forecast_kw",
                    "ev_forecast_kw", "price_yuan_per_kwh")
_ALLOWED_SIGNAL_STATUS = {"observed", "forecast", "missing", "assumed", "stale", "invalid"}
_SUCCESS_STATUSES = {"ok", "feasible", "optimal", "heuristic", "completed", "success", "solved",
                     "execution_interface_completed"}


class ReplayContractError(ValueError):
    """Input or method violates the D paired-replay contract."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _json_compatible(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {name: _json_compatible(getattr(value, name)) for name in value.__dataclass_fields__}
    return repr(value)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ReplayContractError(f"{name} must be a finite number")
    return float(value)


def _timestamp(value: Any, timezone: str, context: str) -> datetime:
    if not isinstance(value, str):
        raise ReplayContractError(f"{context} timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ReplayContractError(f"{context} timestamp must include a UTC offset")
        if parsed.utcoffset() != parsed.astimezone(ZoneInfo(timezone)).utcoffset():
            raise ReplayContractError(f"{context} timestamp offset disagrees with {timezone}")
        return parsed.astimezone(ZoneInfo("UTC"))
    except ReplayContractError:
        raise
    except Exception as exc:
        raise ReplayContractError(f"invalid {context} timestamp: {value}") from exc


def validate_replay_input(manifest: Mapping[str, Any], missing_policy: str = "reject") -> list[str]:
    """Validate A's versioned input shape plus D's replay configuration.

    ``missing_policy='mark'`` allows required signals with status ``missing``;
    affected site-steps are marked and omitted from policy execution. Missing
    values are never imputed as zero.
    """
    if missing_policy not in {"reject", "mark"}:
        raise ReplayContractError("missing_policy must be 'reject' or 'mark'")
    if manifest.get("contract_version") != "1.0.0":
        raise ReplayContractError("contract_version must be 1.0.0")
    declared_hash = manifest.get("input_sha256")
    if declared_hash is not None and declared_hash != input_sha256(manifest):
        raise ReplayContractError("input_sha256 mismatch")
    if manifest.get("interval_minutes") != 30:
        raise ReplayContractError("common replay requires interval_minutes=30")
    if manifest.get("data_class") not in {"synthetic", "forecast", "measured", "mixed"}:
        raise ReplayContractError("data_class must distinguish synthetic/forecast/measured/mixed inputs")
    if not isinstance(manifest.get("seed"), int) or isinstance(manifest.get("seed"), bool):
        raise ReplayContractError("seed must be an integer")
    try:
        timezone = manifest["timezone"]
        ZoneInfo(timezone)
    except Exception as exc:
        raise ReplayContractError("timezone must be a valid IANA zone") from exc

    prices = manifest.get("price_levels_yuan_per_kwh")
    if not isinstance(prices, list) or not prices:
        raise ReplayContractError("price_levels_yuan_per_kwh must be a non-empty array")
    prices = [_finite(value, "price level") for value in prices]
    if any(value <= 0 for value in prices) or len(set(prices)) != len(prices):
        raise ReplayContractError("price levels must be unique and positive")
    for name in ("upstream_import_limit_kw",):
        if _finite(manifest.get(name), name) <= 0:
            raise ReplayContractError(f"{name} must be positive")
    export_limit = manifest.get("upstream_export_limit_kw")
    if export_limit is not None and _finite(export_limit, "upstream_export_limit_kw") < 0:
        raise ReplayContractError("upstream_export_limit_kw cannot be negative")
    loss_fraction = _finite(manifest.get("upstream_loss_fraction", 0.0), "upstream_loss_fraction")
    if not 0 <= loss_fraction < 1:
        raise ReplayContractError("upstream_loss_fraction must be in [0, 1)")
    if manifest.get("tie_lines", []):
        raise ReplayContractError("this replay executor does not yet model tie_lines; refusing to ignore topology")
    method_configs = manifest.get("method_configs", {})
    if not isinstance(method_configs, Mapping):
        raise ReplayContractError("method_configs must be an object keyed by method ID")

    sites = manifest.get("sites")
    series = manifest.get("series")
    if not isinstance(sites, list) or not 2 <= len(sites) <= 4:
        raise ReplayContractError("sites must contain 2-4 site definitions")
    site_ids = [site.get("site_id") for site in sites if isinstance(site, Mapping)]
    if len(site_ids) != len(sites) or any(not isinstance(value, str) or not value for value in site_ids):
        raise ReplayContractError("every site requires a non-empty site_id")
    if len(set(site_ids)) != len(site_ids):
        raise ReplayContractError("site_id values must be unique")
    if not isinstance(series, list) or {row.get("site_id") for row in series if isinstance(row, Mapping)} != set(site_ids):
        raise ReplayContractError("series site IDs must exactly match configured site IDs")
    if len(series) != len(site_ids):
        raise ReplayContractError("series must contain exactly one entry per site")

    site_configs = manifest.get("site_configs")
    if not isinstance(site_configs, Mapping) or set(site_configs) != set(site_ids):
        raise ReplayContractError("site_configs must define assets and initial_states for every site")
    for site_id in site_ids:
        config = site_configs[site_id]
        assets = config.get("assets", {})
        initial = config.get("initial_state", {})
        needed_assets = {"battery_capacity_kwh", "battery_power_kw", "soc_min", "soc_reserve",
                         "soc_max", "eta_charge", "eta_discharge", "ev_service_limit_kw",
                         "degradation_cost_yuan_per_kwh", "soh_loss_per_kwh"}
        if not isinstance(assets, Mapping) or needed_assets - assets.keys():
            raise ReplayContractError(f"{site_id} assets are missing {sorted(needed_assets - set(assets))}")
        for name in needed_assets:
            value = _finite(assets[name], f"{site_id}.assets.{name}")
            if name in {"soc_min", "soc_reserve", "soc_max", "eta_charge", "eta_discharge"}:
                continue
            if value < 0 or (name not in {"degradation_cost_yuan_per_kwh", "soh_loss_per_kwh"} and value == 0):
                raise ReplayContractError(f"{site_id}.assets.{name} must be positive")
        if not (0 <= assets["soc_min"] <= assets["soc_reserve"] <= assets["soc_max"] <= 1):
            raise ReplayContractError(f"{site_id} SOC bounds are inconsistent")
        if not (0 < assets["eta_charge"] <= 1 and 0 < assets["eta_discharge"] <= 1):
            raise ReplayContractError(f"{site_id} battery efficiencies must be in (0, 1]")
        if not isinstance(initial, Mapping) or not {"soc", "soh"} <= initial.keys():
            raise ReplayContractError(f"{site_id} initial_state must provide SOC and SOH")
        initial_soc = _finite(initial["soc"], f"{site_id}.initial_state.soc")
        initial_soh = _finite(initial["soh"], f"{site_id}.initial_state.soh")
        if not assets["soc_min"] <= initial_soc <= assets["soc_max"]:
            raise ReplayContractError(f"{site_id} initial SOC is outside asset bounds")
        if not 0.7 <= initial_soh <= 1.0:
            raise ReplayContractError(f"{site_id} initial SOH must be in [0.7, 1]")
        if site_id in {site.get("site_id") for site in sites
                       if site.get("transformer_import_limit_kw") is not None
                       or site.get("transformer_export_limit_kw") is not None}:
            raise ReplayContractError("site transformer limits are not yet modeled; refusing to ignore them")

    reference_axis: list[datetime] | None = None
    reference_count: int | None = None
    marked_quality_issues: list[str] = []
    for row in series:
        site_id = row["site_id"]
        intervals = row.get("intervals")
        if not isinstance(intervals, list) or not intervals:
            raise ReplayContractError(f"{site_id} intervals must be a non-empty array")
        axis = []
        for index, interval in enumerate(intervals):
            stamp = _timestamp(interval.get("timestamp"), timezone, f"{site_id}[{index}]")
            axis.append(stamp)
            signals = interval.get("signals")
            if not isinstance(signals, Mapping):
                raise ReplayContractError(f"{site_id}[{index}] signals must be an object")
            missing = REQUIRED_SIGNALS - signals.keys()
            if missing:
                raise ReplayContractError(f"{site_id}[{index}] missing required signals: {sorted(missing)}")
            for name, signal in signals.items():
                if name not in SIGNAL_UNITS:
                    raise ReplayContractError(f"unsupported signal '{name}'")
                if not isinstance(signal, Mapping) or signal.get("unit") != SIGNAL_UNITS[name]:
                    raise ReplayContractError(f"{site_id}[{index}] {name} unit must be {SIGNAL_UNITS.get(name)}")
                if signal.get("status") not in _ALLOWED_SIGNAL_STATUS:
                    raise ReplayContractError(f"{site_id}[{index}] {name} has an unsupported status")
                if not isinstance(signal.get("source"), str) or not signal["source"].strip():
                    raise ReplayContractError(f"{site_id}[{index}] {name} requires source provenance")
                status = signal["status"]
                value = signal.get("value")
                if status == "missing":
                    if value is not None:
                        raise ReplayContractError(f"{site_id}[{index}] missing {name} must have null value")
                    if name in REQUIRED_SIGNALS:
                        marked_quality_issues.append(f"{site_id}[{index}].{name}:missing")
                    continue
                if value is None:
                    raise ReplayContractError(f"{site_id}[{index}] null {name} requires status='missing'")
                number = _finite(value, f"{site_id}[{index}].{name}")
                if name in FORECAST_SIGNALS and number < 0:
                    raise ReplayContractError(f"{site_id}[{index}].{name} cannot be negative")
                if name == "soc" and not 0 <= number <= 1:
                    raise ReplayContractError(f"{site_id}[{index}].soc must be in [0, 1]")
                if name == "soh" and not 0.7 <= number <= 1:
                    raise ReplayContractError(f"{site_id}[{index}].soh must be in [0.7, 1]")
                if status == "assumed" and not signal.get("assumption"):
                    raise ReplayContractError(f"{site_id}[{index}] assumed {name} needs an assumption note")
                if status in {"stale", "invalid"} and name in REQUIRED_SIGNALS:
                    marked_quality_issues.append(f"{site_id}[{index}].{name}:{status}")
        if any(right <= left for left, right in zip(axis, axis[1:])):
            raise ReplayContractError(f"{site_id} timestamps must be strictly increasing and unique")
        expected_delta = timedelta(minutes=30)
        if any(right - left != expected_delta for left, right in zip(axis, axis[1:])):
            raise ReplayContractError(f"{site_id} timestamps must be complete 30-minute intervals")
        if reference_axis is None:
            reference_axis = axis
            reference_count = len(intervals)
        elif axis != reference_axis:
            raise ReplayContractError("all sites must use the exact same timestamp axis")
    if reference_count is None:
        raise ReplayContractError("no site intervals were provided")
    if marked_quality_issues and missing_policy == "reject":
        raise ReplayContractError(f"required data quality issue: {marked_quality_issues[:5]}")
    return marked_quality_issues


def input_sha256(manifest: Mapping[str, Any]) -> str:
    return sha256_json({key: value for key, value in manifest.items()
                        if key not in {"input_sha256", "run_metadata"}})


def _source_fingerprint() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    include = [Path("src/validation/data_audit.py"), Path("src/validation/multisite_replay.py"),
               Path("scripts/audit_multisite_data.py"), Path("scripts/run_multisite_replay.py")]
    digest = hashlib.sha256()
    for relative in include:
        path = root / relative
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                                  capture_output=True, text=True).stdout.strip()
        status = subprocess.run(["git", "status", "--short", "--",
                                 "scripts/audit_multisite_data.py", "scripts/run_multisite_replay.py",
                                 "src/validation"], cwd=root,
                                check=True, capture_output=True, text=True).stdout
        dirty = bool(status.strip())
    except Exception:
        revision = "unavailable"
        dirty = None
    return {"revision": revision, "source_tree_sha256": digest.hexdigest(),
            "working_tree_dirty": dirty}


def _signal_values(interval: Mapping[str, Any]) -> dict[str, Any]:
    return {name: copy.deepcopy(signal) for name, signal in interval["signals"].items()}


def _action_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    names = ("site_id", "timestamp", "price_yuan_per_kwh", "storage_kw", "requested_ev_kw", "solver_status")
    if any(hasattr(raw, name) for name in names):
        return {name: getattr(raw, name) for name in names if hasattr(raw, name)}
    raise ReplayContractError("provider actions must be mappings or SiteAction-like objects")


def _project_action(raw: Any, site_id: str, timestamp: str, site_config: Mapping[str, Any],
                    forecast: Mapping[str, Any], state: Mapping[str, float],
                    price_levels: list[float], dt_hours: float) -> dict[str, Any]:
    action = _action_dict(raw)
    if action.get("site_id", site_id) != site_id:
        raise ReplayContractError(f"provider action site_id does not match {site_id}")
    assets = site_config["assets"]
    requested_price = _finite(action.get("price_yuan_per_kwh"), f"{site_id}.action.price")
    price = min(price_levels, key=lambda level: abs(level - requested_price))
    requested_ev = forecast["ev_forecast_kw"]
    accepted_request = _finite(action.get("requested_ev_kw", requested_ev), f"{site_id}.action.requested_ev_kw")
    if "accepted_ev_kw" in action:
        accepted_request = _finite(action["accepted_ev_kw"], f"{site_id}.action.accepted_ev_kw")
    accepted_ev = min(max(accepted_request, 0.0), requested_ev, assets["ev_service_limit_kw"])
    requested_storage = _finite(action.get("storage_kw", 0.0), f"{site_id}.action.storage_kw")
    capacity = assets["battery_capacity_kwh"] * state["soh"]
    storage = max(-assets["battery_power_kw"], min(assets["battery_power_kw"], requested_storage))
    if storage >= 0:
        available = max(0.0, (state["soc"] - assets["soc_reserve"]) * capacity
                        * assets["eta_discharge"] / dt_hours)
        storage = min(storage, available)
    else:
        room = max(0.0, (assets["soc_max"] - state["soc"]) * capacity
                   / (assets["eta_charge"] * dt_hours))
        storage = max(storage, -room)
    changed = (abs(price - requested_price) > 1e-9
               or abs(accepted_ev - accepted_request) > 1e-9
               or abs(storage - requested_storage) > 1e-9)
    return {
        "site_id": site_id,
        "timestamp": action.get("timestamp", timestamp),
        "requested_price_yuan_per_kwh": requested_price,
        "price_yuan_per_kwh": price,
        "requested_ev_kw": requested_ev,
        "action_ev_kw": accepted_request,
        "accepted_ev_kw": accepted_ev,
        "requested_storage_kw": requested_storage,
        "storage_kw": storage,
        "projected": changed,
        "projection_reasons": [reason for condition, reason in (
            (abs(price - requested_price) > 1e-9, "price_not_in_allowed_tiers"),
            (abs(accepted_ev - accepted_request) > 1e-9, "EV_request_or_service_limit"),
            (abs(storage - requested_storage) > 1e-9, "battery_power_or_SOC_limit"),
        ) if condition],
        "solver_status": action.get("solver_status", "not_applicable"),
    }


def _proportional_allocate(total: float, weights: Mapping[str, float]) -> dict[str, float]:
    weight_sum = sum(max(0.0, value) for value in weights.values())
    if weight_sum <= 1e-15 or total <= 0:
        return {key: 0.0 for key in weights}
    return {key: total * max(0.0, value) / weight_sum for key, value in weights.items()}


def _execute_step(manifest: Mapping[str, Any], forecasts: Mapping[str, Mapping[str, Any]],
                  states: dict[str, dict[str, float]], actions: Mapping[str, Any],
                  dt_hours: float, timestamp: str) -> dict[str, Any]:
    site_configs = manifest["site_configs"]
    price_levels = [float(value) for value in manifest["price_levels_yuan_per_kwh"]]
    projected = {}
    raw_actions = {}
    for site_id in site_configs:
        raw_actions[site_id] = _action_dict(actions[site_id])
        projected[site_id] = _project_action(
            raw_actions[site_id], site_id, timestamp, site_configs[site_id],
            forecasts[site_id], states[site_id], price_levels, dt_hours)

    generation = {}
    base_request = {}
    ev_request = {}
    discharge = {}
    charge = {}
    battery_loss = {}
    degradation_loss = {}
    degradation_cost = {}
    delta_storage = {}
    next_states = copy.deepcopy(states)
    for site_id, forecast in forecasts.items():
        action = projected[site_id]
        cfg = site_configs[site_id]
        asset = cfg["assets"]
        state = states[site_id]
        gen = (forecast["pv_forecast_kw"] + forecast["wind_forecast_kw"]) * dt_hours
        p_storage = action["storage_kw"]
        out_kwh = max(p_storage, 0.0) * dt_hours
        in_kwh = max(-p_storage, 0.0) * dt_hours
        conversion = (out_kwh * (1.0 / asset["eta_discharge"] - 1.0)
                      + in_kwh * (1.0 - asset["eta_charge"]))
        capacity_before = asset["battery_capacity_kwh"] * state["soh"]
        stored_before = state["soc"] * capacity_before
        stored_mid = stored_before + in_kwh * asset["eta_charge"] - out_kwh / asset["eta_discharge"]
        throughput = out_kwh + in_kwh
        soh_after = max(0.7, state["soh"] - throughput * asset["soh_loss_per_kwh"])
        stored_after = stored_mid * (soh_after / state["soh"])
        degrade_energy = max(0.0, stored_mid - stored_after)
        capacity_after = asset["battery_capacity_kwh"] * soh_after
        soc_after = stored_after / capacity_after if capacity_after else state["soc"]
        if not asset["soc_min"] - 1e-10 <= soc_after <= asset["soc_max"] + 1e-10:
            raise ReplayContractError(f"internal SOC transition outside bounds at {site_id}")
        delta_storage[site_id] = stored_after - stored_before
        battery_loss[site_id] = conversion
        degradation_loss[site_id] = degrade_energy
        degradation_cost[site_id] = throughput * asset["degradation_cost_yuan_per_kwh"]
        generation[site_id] = gen
        base_request[site_id] = forecast["load_forecast_kw"] * dt_hours
        ev_request[site_id] = action["accepted_ev_kw"] * dt_hours
        discharge[site_id] = out_kwh
        charge[site_id] = in_kwh
        next_states[site_id] = {"soc": soc_after, "soh": soh_after}

    total_generation = sum(generation.values()) + sum(discharge.values())
    total_charge = sum(charge.values())
    total_user_request = sum(base_request.values()) + sum(ev_request.values())
    net_before_grid = total_user_request + total_charge - total_generation
    import_loss_fraction = float(manifest.get("upstream_loss_fraction", 0.0))
    import_source_cap = float(manifest["upstream_import_limit_kw"]) * dt_hours
    delivery_cap = import_source_cap * (1.0 - import_loss_fraction)
    delivered_import = min(max(net_before_grid, 0.0), delivery_cap)
    upstream_import = delivered_import / (1.0 - import_loss_fraction)
    network_loss = upstream_import - delivered_import
    available_for_users = max(0.0, total_generation + delivered_import - total_charge)
    served_base_total = min(sum(base_request.values()), available_for_users)
    base_served = _proportional_allocate(served_base_total, base_request)
    remaining = max(0.0, available_for_users - served_base_total)
    served_ev_total = min(sum(ev_request.values()), remaining)
    ev_served = _proportional_allocate(served_ev_total, ev_request)
    surplus = max(0.0, total_generation + delivered_import - total_charge
                  - sum(base_served.values()) - sum(ev_served.values()))
    export_limit = manifest.get("upstream_export_limit_kw")
    export_cap = 0.0 if export_limit is None else float(export_limit) * dt_hours
    exported = min(surplus, export_cap)
    curtailed = surplus - exported

    # Site settlement balances each site through one ideal shared bus. This
    # is an explicit synthetic copperplate simplification, not a tie-line model.
    site_surplus_weights = {
        site_id: max(0.0, generation[site_id] + discharge[site_id] - base_served[site_id]
                     - ev_served[site_id] - charge[site_id])
        for site_id in site_configs
    }
    site_export = _proportional_allocate(exported, site_surplus_weights)
    site_curtail = _proportional_allocate(curtailed, site_surplus_weights)
    site_ledgers = {}
    system_residual = (sum(generation.values()) + upstream_import
                       - sum(base_served.values()) - sum(ev_served.values())
                       - exported - curtailed - network_loss
                       - sum(battery_loss.values()) - sum(degradation_loss.values())
                       - sum(delta_storage.values()))
    for site_id in site_configs:
        action = projected[site_id]
        cfg = site_configs[site_id]
        # Positive means imported from the shared bus; negative means supplied
        # to the shared bus. Site exchanges net to post-loss upstream delivery.
        exchange = (base_served[site_id] + ev_served[site_id] + charge[site_id]
                    + site_export[site_id] + site_curtail[site_id]
                    - generation[site_id] - discharge[site_id])
        site_residual = (generation[site_id] + exchange - base_served[site_id]
                         - ev_served[site_id] - site_export[site_id]
                         - site_curtail[site_id] - battery_loss[site_id]
                         - degradation_loss[site_id] - delta_storage[site_id])
        ev_requested_total = forecasts[site_id]["ev_forecast_kw"] * dt_hours
        ev_served_energy = ev_served[site_id]
        revenue = ev_served_energy * action["price_yuan_per_kwh"]
        state_before = states[site_id]
        state_after = next_states[site_id]
        site_ledgers[site_id] = {
            "input_forecast": copy.deepcopy(forecasts[site_id]),
            "action": copy.deepcopy(action),
            "initial_state": dict(state_before),
            "final_state": dict(state_after),
            "requested_kwh": ev_requested_total,
            "accepted_kwh": ev_request[site_id],
            "served_kwh": ev_served_energy,
            "unserved_kwh": max(0.0, ev_requested_total - ev_served_energy),
            "suppressed_kwh": max(0.0, ev_requested_total - ev_request[site_id]),
            "base_load_requested_kwh": base_request[site_id],
            "base_load_served_kwh": base_served[site_id],
            "base_load_unserved_kwh": max(0.0, base_request[site_id] - base_served[site_id]),
            "delivered_revenue_yuan": revenue,
            "user_cost_yuan": revenue,
            "curtailment_kwh": site_curtail[site_id],
            "storage_charge_input_kwh": charge[site_id],
            "storage_discharge_output_kwh": discharge[site_id],
            "storage_conversion_loss_kwh": battery_loss[site_id],
            "network_loss_kwh_allocated": 0.0,
            "shared_bus_exchange_kwh": exchange,
            "degradation_energy_loss_kwh": degradation_loss[site_id],
            "degradation_cost_yuan": degradation_cost[site_id],
            "soc_change": state_after["soc"] - state_before["soc"],
            "soh_change": state_after["soh"] - state_before["soh"],
            "constraint_violation_count": 0,
            "energy_residual_kwh": site_residual,
        }
    positive_exchange_sum = sum(max(0.0, record["shared_bus_exchange_kwh"])
                                for record in site_ledgers.values())
    for record in site_ledgers.values():
        weight = max(0.0, record["shared_bus_exchange_kwh"])
        record["network_loss_kwh_allocated"] = (
            network_loss * weight / positive_exchange_sum if positive_exchange_sum > 1e-15 else 0.0
        )

    return {
        "actions": projected,
        "site_ledgers": site_ledgers,
        "system_ledger": {
            "requested_kwh": sum(forecasts[s]["ev_forecast_kw"] * dt_hours for s in site_configs),
            "accepted_kwh": sum(ev_request.values()),
            "served_kwh": sum(ev_served.values()),
            "unserved_kwh": sum(max(0.0, forecasts[s]["ev_forecast_kw"] * dt_hours - ev_served[s])
                                 for s in site_configs),
            "suppressed_kwh": sum(max(0.0, forecasts[s]["ev_forecast_kw"] * dt_hours - ev_request[s])
                                  for s in site_configs),
            "base_load_requested_kwh": sum(base_request.values()),
            "base_load_served_kwh": sum(base_served.values()),
            "base_load_unserved_kwh": max(0.0, sum(base_request.values()) - sum(base_served.values())),
            "delivered_revenue_yuan": sum(record["delivered_revenue_yuan"] for record in site_ledgers.values()),
            "user_cost_yuan": sum(record["user_cost_yuan"] for record in site_ledgers.values()),
            "service_rate": (sum(ev_served.values())
                             / sum(forecast["ev_forecast_kw"] * dt_hours for forecast in forecasts.values())
                             if any(forecast["ev_forecast_kw"] > 0 for forecast in forecasts.values()) else None),
            "curtailment_kwh": curtailed,
            "storage_conversion_loss_kwh": sum(battery_loss.values()),
            "network_loss_kwh": network_loss,
            "upstream_import_delivered_kwh": delivered_import,
            "upstream_import_source_kwh": upstream_import,
            "upstream_export_kwh": exported,
            "degradation_cost_yuan": sum(degradation_cost.values()),
            "degradation_energy_loss_kwh": sum(degradation_loss.values()),
            "soc_change_by_site": {s: next_states[s]["soc"] - states[s]["soc"] for s in site_configs},
            "soh_change_by_site": {s: next_states[s]["soh"] - states[s]["soh"] for s in site_configs},
            "constraint_violation_count": 0,
            "projection_count": sum(1 for action in projected.values() if action["projected"]),
            "action_count": len(projected),
            "energy_residual_kwh": system_residual,
            "energy_identity_tolerance_kwh": LEDGER_TOLERANCE_KWH,
            "energy_identity_passed": abs(system_residual) < LEDGER_TOLERANCE_KWH,
            "constraint_scope": {
                "evaluated": ["battery_SOC", "battery_power", "EV_service_limit",
                              "upstream_import_limit", "upstream_export_limit"],
                "not_evaluated": [],
                "model_boundary": "copperplate shared bus; no tie-line flow model",
            },
        },
        "next_states": next_states,
    }


def run_paired_replay(manifest: Mapping[str, Any], methods: Mapping[str, Callable[..., Any]],
                      *, missing_policy: str = "reject") -> dict[str, Any]:
    """Replay every provider against the exact same trace and initial assets.

    Each provider is called as ``provider(context)`` and returns a mapping
    from site ID to action. The context contains the full common future window,
    current per-method SOC/SOH, assets, legal prices, and system constraints.
    """
    marked_missing = validate_replay_input(manifest, missing_policy=missing_policy)
    if not methods or any(not isinstance(name, str) or not name for name in methods):
        raise ReplayContractError("methods must be a non-empty mapping with named arms")
    trace_digest = input_sha256(manifest)
    series = {row["site_id"]: row["intervals"] for row in manifest["series"]}
    site_ids = [site["site_id"] for site in manifest["sites"]]
    n_steps = len(next(iter(series.values())))
    dt_hours = float(manifest["interval_minutes"]) / 60.0
    run_results = {}
    for method_id, provider in methods.items():
        states = {site_id: dict(manifest["site_configs"][site_id]["initial_state"])
                  for site_id in site_ids}
        records = []
        projection_count = action_count = 0
        method_started = time.perf_counter()
        status = "completed"
        failure = None
        for step_index in range(n_steps):
            timestamp = series[site_ids[0]][step_index]["timestamp"]
            site_intervals = {site_id: series[site_id][step_index] for site_id in site_ids}
            current_issues = [f"{site_id}.{name}:{interval['signals'][name]['status']}"
                              for site_id, interval in site_intervals.items()
                              for name in REQUIRED_SIGNALS
                              if interval["signals"][name]["status"] in {"missing", "stale", "invalid"}]
            if current_issues:
                records.append({
                    "step_index": step_index, "timestamp": timestamp,
                    "dt_hours": dt_hours, "input_quality_status": "missing_required_signal",
                    "quality_issues": current_issues, "actions": {}, "site_ledgers": {},
                    "system_ledger": None, "solver_status": "not_run",
                    "execution_status": "input_missing_marked",
                })
                status = "completed_with_marked_missing"
                continue

            forecasts = {}
            for site_id, interval in site_intervals.items():
                signal_values = _signal_values(interval)
                forecasts[site_id] = {name: _finite(signal_values[name]["value"], f"{site_id}.{name}")
                                      for name in FORECAST_SIGNALS}
                forecasts[site_id]["signal_provenance"] = {
                    name: {"source": signal_values[name]["source"],
                           "status": signal_values[name]["status"]}
                    for name in FORECAST_SIGNALS
                }
            windows = {}
            for site_id in site_ids:
                windows[site_id] = []
                for future in series[site_id][step_index:]:
                    if any(future["signals"][name]["status"] in {"missing", "stale", "invalid"}
                           for name in REQUIRED_SIGNALS):
                        windows[site_id].append({"timestamp": future["timestamp"], "quality_status": "missing"})
                    else:
                        windows[site_id].append({
                            "timestamp": future["timestamp"],
                            **{name: future["signals"][name]["value"] for name in FORECAST_SIGNALS},
                        })
            context = {
                "trace_sha256": trace_digest,
                "step_index": step_index,
                "timestamp": timestamp,
                "dt_hours": dt_hours,
                "data_class": manifest["data_class"],
                "seed": manifest["seed"],
                "forecasts": copy.deepcopy(forecasts),
                "forecast_windows": copy.deepcopy(windows),
                "input_payload": {
                    "contract_version": manifest["contract_version"],
                    "timezone": manifest["timezone"],
                    "interval_minutes": manifest["interval_minutes"],
                    "prediction_horizon_steps": n_steps - step_index,
                    "sites": copy.deepcopy(manifest["sites"]),
                    "tie_lines": copy.deepcopy(manifest.get("tie_lines", [])),
                    "series": [{"site_id": site_id,
                                "intervals": copy.deepcopy(series[site_id][step_index:])}
                               for site_id in site_ids],
                    "upstream_import_limit_kw": manifest["upstream_import_limit_kw"],
                    "upstream_export_limit_kw": manifest.get("upstream_export_limit_kw"),
                },
                "states": copy.deepcopy(states),
                "site_configs": copy.deepcopy(manifest["site_configs"]),
                "price_levels_yuan_per_kwh": list(manifest["price_levels_yuan_per_kwh"]),
                "system_constraints": {
                    "upstream_import_limit_kw": manifest["upstream_import_limit_kw"],
                    "upstream_export_limit_kw": manifest.get("upstream_export_limit_kw"),
                    "upstream_loss_fraction": manifest.get("upstream_loss_fraction", 0.0),
                    "tie_lines": copy.deepcopy(manifest.get("tie_lines", [])),
                },
            }
            provider_runtime = None
            site_provider_runtime = {}
            provider_result = None
            provider_started = time.perf_counter()
            try:
                provider_result = provider(context)
                provider_runtime = time.perf_counter() - provider_started
                site_provider_runtime = {}
                solver_status = "not_applicable"
                if isinstance(provider_result, Mapping) and (
                        "actions" in provider_result or "status" in provider_result):
                    solver_status = str(provider_result.get("solver_status", "not_applicable"))
                    site_provider_runtime = provider_result.get("site_runtime_seconds", {}) or {}
                    outcome = str(provider_result.get("status", "ok"))
                    if outcome not in _SUCCESS_STATUSES:
                        status = outcome
                        failure = {"step_index": step_index, "timestamp": timestamp,
                                   "status": outcome, "solver_status": solver_status,
                                   "message": str(provider_result.get("message", "provider reported failure"))}
                        records.append({"step_index": step_index, "timestamp": timestamp,
                                        "execution_status": "failed", "solver_status": solver_status,
                                        "failure": failure, "input_snapshot": context,
                                        "provider_runtime_seconds": provider_runtime,
                                        "provider_result": _json_compatible(provider_result)})
                        break
                    action_map = provider_result.get("actions", {})
                else:
                    action_map = provider_result
                if not isinstance(action_map, Mapping) or set(action_map) != set(site_ids):
                    raise ReplayContractError("provider must return exactly one action for every configured site")
                for site_id in site_ids:
                    raw = _action_dict(action_map[site_id])
                    action_timestamp = raw.get("timestamp", timestamp)
                    if action_timestamp != timestamp:
                        raise ReplayContractError(f"{site_id} action timestamp is not aligned with common input")
                    if "solver_status" in raw and raw["solver_status"] not in (_SUCCESS_STATUSES | {"not_applicable"}):
                        solver_status = str(raw["solver_status"])
                        status = "nonconverged" if "converg" in solver_status.lower() else solver_status
                        failure = {"step_index": step_index, "timestamp": timestamp,
                                   "status": status, "solver_status": solver_status,
                                   "message": "provider action reported a non-success solver status"}
                        records.append({"step_index": step_index, "timestamp": timestamp,
                                        "execution_status": "failed", "solver_status": solver_status,
                                        "failure": failure, "input_snapshot": context,
                                        "raw_actions": {k: _action_dict(v) for k, v in action_map.items()},
                                        "provider_runtime_seconds": provider_runtime})
                        break
                if failure:
                    break
                action_solver_statuses = sorted({
                    str(_action_dict(action).get("solver_status", "not_applicable"))
                    for action in action_map.values()
                })
                if solver_status == "not_applicable" and action_solver_statuses:
                    solver_status = (action_solver_statuses[0] if len(action_solver_statuses) == 1
                                     else "mixed:" + ",".join(action_solver_statuses))
                started = time.perf_counter()
                step_result = _execute_step(manifest, forecasts, states, action_map,
                                            dt_hours, timestamp)
                step_runtime = time.perf_counter() - started
                states = step_result.pop("next_states")
                projection_count += step_result["system_ledger"]["projection_count"]
                action_count += step_result["system_ledger"]["action_count"]
                records.append({
                    "step_index": step_index, "timestamp": timestamp, "dt_hours": dt_hours,
                    "input_quality_status": "complete", "input_snapshot": context,
                    "raw_actions": {key: _action_dict(value) for key, value in action_map.items()},
                    **step_result,
                    "site_provider_runtime_seconds": site_provider_runtime,
                    "solver_status": solver_status,
                    "execution_status": "executed",
                    "step_runtime_seconds": step_runtime,
                    "provider_runtime_seconds": provider_runtime,
                    "execution_runtime_seconds": step_runtime,
                })
            except Exception as exc:  # Preserve failures in the raw per-step result.
                if provider_runtime is None:
                    provider_runtime = time.perf_counter() - provider_started
                status = "failed"
                failure = {"step_index": step_index, "timestamp": timestamp,
                           "status": "failed", "exception_type": type(exc).__name__,
                           "message": str(exc)}
                records.append({"step_index": step_index, "timestamp": timestamp,
                                "execution_status": "failed", "solver_status": "not_available",
                                "failure": failure, "input_snapshot": context,
                                "provider_runtime_seconds": provider_runtime,
                                "provider_result": _json_compatible(provider_result)})
                break
        runtime = time.perf_counter() - method_started
        executed = [record for record in records if record.get("execution_status") == "executed"]
        totals = {
            name: sum(record["system_ledger"][name] for record in executed)
            for name in ("requested_kwh", "accepted_kwh", "served_kwh", "unserved_kwh",
                         "suppressed_kwh", "base_load_requested_kwh", "base_load_served_kwh",
                         "base_load_unserved_kwh", "delivered_revenue_yuan", "user_cost_yuan",
                         "curtailment_kwh", "storage_conversion_loss_kwh", "network_loss_kwh",
                         "upstream_import_delivered_kwh", "upstream_import_source_kwh",
                         "upstream_export_kwh", "degradation_cost_yuan", "degradation_energy_loss_kwh",
                         "constraint_violation_count")
        }
        residuals = [abs(record["system_ledger"]["energy_residual_kwh"]) for record in executed]
        site_summaries = {}
        for site_id in site_ids:
            site_steps = [record["site_ledgers"][site_id] for record in executed]
            numeric_fields = (
                "requested_kwh", "accepted_kwh", "served_kwh", "unserved_kwh", "suppressed_kwh",
                "base_load_requested_kwh", "base_load_served_kwh", "base_load_unserved_kwh",
                "delivered_revenue_yuan", "user_cost_yuan", "curtailment_kwh",
                "storage_charge_input_kwh", "storage_discharge_output_kwh",
                "storage_conversion_loss_kwh", "network_loss_kwh_allocated",
                "degradation_energy_loss_kwh", "degradation_cost_yuan", "constraint_violation_count",
            )
            site_totals = {name: sum(step[name] for step in site_steps) for name in numeric_fields}
            initial_state = manifest["site_configs"][site_id]["initial_state"]
            final_state = states[site_id]
            projected_actions = sum(1 for step in site_steps if step["action"]["projected"])
            site_runtime_values = [record.get("site_provider_runtime_seconds", {}).get(site_id)
                                   for record in executed
                                   if record.get("site_provider_runtime_seconds", {}).get(site_id) is not None]
            site_summaries[site_id] = {
                **site_totals,
                "service_rate": site_totals["served_kwh"] / site_totals["requested_kwh"]
                if site_totals["requested_kwh"] else None,
                "initial_state": dict(initial_state),
                "terminal_state": dict(final_state),
                "soc_change": final_state["soc"] - initial_state["soc"],
                "soh_change": final_state["soh"] - initial_state["soh"],
                "projection_count": projected_actions,
                "action_count": len(site_steps),
                "projection_rate": projected_actions / len(site_steps) if site_steps else 0.0,
                "provider_runtime_seconds": sum(site_runtime_values) if site_runtime_values else None,
            }
        totals.update({
            "steps_total": n_steps,
            "steps_executed": len(executed),
            "steps_missing_marked": sum(record.get("execution_status") == "input_missing_marked" for record in records),
            "max_abs_energy_residual_kwh": max(residuals, default=None),
            "energy_ledger_passed": bool(residuals) and all(value < LEDGER_TOLERANCE_KWH for value in residuals),
            "projection_count": projection_count,
            "action_count": action_count,
            "projection_rate": projection_count / action_count if action_count else 0.0,
            "runtime_seconds": runtime,
            "terminal_states": copy.deepcopy(states),
            "site_summaries": site_summaries,
            "soc_change_by_site": {
                site_id: states[site_id]["soc"] - manifest["site_configs"][site_id]["initial_state"]["soc"]
                for site_id in site_ids
            },
            "soh_change_by_site": {
                site_id: states[site_id]["soh"] - manifest["site_configs"][site_id]["initial_state"]["soh"]
                for site_id in site_ids
            },
            "service_rate": (totals["served_kwh"] / totals["requested_kwh"]
                             if totals["requested_kwh"] else None),
            "failure": failure,
        })
        if status == "completed" and totals["steps_missing_marked"]:
            status = "completed_with_marked_missing"
        run_results[method_id] = {
            "status": status,
            "provider": getattr(provider, "provider_type", "external_callback"),
            "trace_sha256": trace_digest,
            "initial_states": {site_id: dict(manifest["site_configs"][site_id]["initial_state"])
                               for site_id in site_ids},
            "summary": totals,
            "steps": records,
        }

    source = _source_fingerprint()
    provider_specs = {
        method_id: {
            "provider_type": getattr(provider, "provider_type", "external_callback"),
            "provider_identity": f"{getattr(provider, '__module__', type(provider).__module__)}."
                                f"{getattr(provider, '__qualname__', type(provider).__qualname__)}",
            "configuration": manifest.get("method_configs", {}).get(method_id, {}),
        }
        for method_id, provider in methods.items()
    }
    try:
        method_config_digest = sha256_json(provider_specs)
    except (TypeError, ValueError) as exc:
        raise ReplayContractError("method_configs must be canonical JSON-serializable values") from exc
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest.get("run_id", "unnamed"),
        "status": "completed" if all(value["status"] == "completed" for value in run_results.values()) else "completed_with_failures_or_missing",
        "data_class": manifest["data_class"],
        "seed": manifest["seed"],
        "trace_sha256": trace_digest,
        "configuration_sha256": sha256_json({
            "sites": manifest["sites"], "site_configs": manifest["site_configs"],
            "price_levels_yuan_per_kwh": manifest["price_levels_yuan_per_kwh"],
            "upstream_import_limit_kw": manifest["upstream_import_limit_kw"],
            "upstream_export_limit_kw": manifest.get("upstream_export_limit_kw"),
            "upstream_loss_fraction": manifest.get("upstream_loss_fraction", 0.0),
            "method_config_sha256": method_config_digest,
        }),
        "method_config_sha256": method_config_digest,
        "method_providers": provider_specs,
        "source_code_revision": source["revision"],
        "source_tree_sha256": source["source_tree_sha256"],
        "working_tree_dirty": source["working_tree_dirty"],
        "missing_policy": missing_policy,
        "marked_data_quality_issues": marked_missing,
        "ranking_authorized": False,
        "accounting_model": "diagnostic_copperplate_shared_bus_v1",
        "results": run_results,
    }


def adapt_site_strategies(strategies: Mapping[str, Any]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapt A-style ``SiteStrategy.decide`` objects to the D provider callback.

    Each strategy receives the full shared forecast window and its own state;
    site-specific constraints are passed explicitly. The selected policy may
    still choose to use only that site's information.
    """
    def provider(context: dict[str, Any]) -> dict[str, Any]:
        actions = {}
        site_runtime = {}
        for site_id, strategy in strategies.items():
            if site_id not in context["site_configs"]:
                raise ReplayContractError(f"strategy configured for unknown site {site_id}")
            started = time.perf_counter()
            actions[site_id] = strategy.decide(
                site_id=site_id,
                forecast=context["forecast_windows"][site_id],
                state=context["states"][site_id],
                constraints={**context["system_constraints"],
                             "assets": context["site_configs"][site_id]["assets"],
                             "price_levels_yuan_per_kwh": context["price_levels_yuan_per_kwh"]},
            )
            site_runtime[site_id] = time.perf_counter() - started
        return {"status": "ok", "actions": actions,
                "site_runtime_seconds": site_runtime}
    provider.provider_type = "site_strategy_adapter"
    return provider


def adapt_coordinator(coordinator: Any, request_builder: Callable[[dict[str, Any]], Any] | None = None,
                      result_parser: Callable[[Any], Mapping[str, Any]] | None = None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Adapt a coordinator without binding D to an unmerged A/B/C checkout.

    ``request_builder`` can construct A's ``CoordinationRequest`` once its
    contract package is present in the integrating branch. By default the
    serialized common request is passed through as a mapping. The parser must
    return ``{site_id: SiteAction-like}``; status and solver status are retained.
    """
    def provider(context: dict[str, Any]) -> dict[str, Any]:
        if request_builder:
            request = request_builder(context)
        else:
            try:
                from src.multisite.contract import (
                    CONTRACT_VERSION,
                    CoordinationRequest,
                    MultiSiteInput,
                )
            except ImportError:
                request = context
            else:
                input_data = MultiSiteInput.from_dict(context["input_payload"])
                request = CoordinationRequest(
                    contract_version=CONTRACT_VERSION,
                    run_id=f"{context['trace_sha256'][:12]}-{context['step_index']:05d}",
                    input=input_data,
                    config={
                        "site_configs": context["site_configs"],
                        "price_levels_yuan_per_kwh": context["price_levels_yuan_per_kwh"],
                        "system_constraints": context["system_constraints"],
                        "seed": context["seed"],
                    },
                )
        result = coordinator.coordinate(request)
        if result_parser:
            parsed = result_parser(result)
        elif isinstance(result, Mapping):
            parsed = result
        elif all(hasattr(result, name) for name in ("status", "ledger")):
            metadata = getattr(result, "metadata", {}) or {}
            parsed = {
                "status": getattr(result, "status"),
                "solver_status": metadata.get("solver_status", getattr(result, "status")),
                "failure_code": getattr(result, "failure_code", None),
                "actions": metadata.get("actions", {}),
                "message": metadata.get("message", ""),
            }
            if parsed["failure_code"]:
                parsed["status"] = "failed"
            if not parsed["actions"]:
                entries = getattr(result, "ledger", ())
                parsed["actions"] = {
                    (getattr(entry, "site_id", None) if not isinstance(entry, Mapping)
                     else entry.get("site_id")):
                    (getattr(entry, "action", None) if not isinstance(entry, Mapping)
                     else entry.get("action"))
                    for entry in entries
                    if ((getattr(entry, "site_id", None) if not isinstance(entry, Mapping)
                         else entry.get("site_id")) is not None)
                }
        else:
            parsed = result
        if not isinstance(parsed, Mapping):
            raise ReplayContractError("coordinator adapter result_parser must return a mapping")
        return dict(parsed)
    provider.provider_type = "coordinator_adapter"
    return provider


def build_synthetic_manifest(seed: int = 17, step_count: int = 4) -> dict[str, Any]:
    """Create a small fully tagged 2-site synthetic half-hour trace."""
    if step_count <= 0:
        raise ValueError("step_count must be positive")
    from random import Random

    rng = Random(seed)
    start = datetime(2024, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    site_ids = ["SYN-01", "SYN-02"]
    sites = [{"site_id": site_id, "timezone": "Asia/Shanghai"} for site_id in site_ids]
    site_configs = {}
    for index, site_id in enumerate(site_ids):
        site_configs[site_id] = {
            "assets": {
                "battery_capacity_kwh": 40.0,
                "battery_power_kw": 20.0,
                "soc_min": 0.1,
                "soc_reserve": 0.2,
                "soc_max": 0.9,
                "eta_charge": 0.95,
                "eta_discharge": 0.95,
                "ev_service_limit_kw": 30.0,
                "degradation_cost_yuan_per_kwh": 0.03,
                "soh_loss_per_kwh": 1e-7,
            },
            "initial_state": {"soc": 0.55 + 0.05 * index, "soh": 0.98},
        }
    series = []
    for index, site_id in enumerate(site_ids):
        intervals = []
        for step_index in range(step_count):
            stamp = (start + timedelta(minutes=30 * step_index)).isoformat()
            pv = max(0.0, (12.0 if index == 0 else 6.0) + (4.0 if step_index == 1 else 0.0)
                     + rng.uniform(-0.5, 0.5))
            wind = max(0.0, (4.0 if index == 0 else 5.0) + rng.uniform(-0.25, 0.25))
            base = 5.0 + index + (2.0 if step_index == 3 else 0.0)
            ev = 8.0 + (2.0 * index) + (2.0 if step_index in {1, 2} else 0.0)
            values = {
                "pv_forecast_kw": (pv, "kW", "forecast"),
                "wind_forecast_kw": (wind, "kW", "forecast"),
                "load_forecast_kw": (base, "kW", "forecast"),
                "ev_forecast_kw": (ev, "kW", "forecast"),
                "price_yuan_per_kwh": (1.8, "CNY/kWh", "forecast"),
                "soc": (site_configs[site_id]["initial_state"]["soc"], "fraction", "assumed"),
                "soh": (0.98, "fraction", "assumed"),
                "reserve_margin_kwh": (8.0, "kWh", "assumed"),
            }
            signals = {}
            for name, (value, unit, status) in values.items():
                signals[name] = {
                    "value": value,
                    "unit": unit,
                    "source": f"synthetic-generator:seed={seed}",
                    "status": status,
                    **({"assumption": "Synthetic scenario initial-state/config reference; replay state is evolved by the executor."}
                       if status == "assumed" else {}),
                }
            intervals.append({"timestamp": stamp, "signals": signals})
        series.append({"site_id": site_id, "intervals": intervals})
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": "1.0.0",
        "run_id": f"synthetic-2site-seed-{seed}",
        "data_class": "synthetic",
        "seed": seed,
        "timezone": "Asia/Shanghai",
        "interval_minutes": 30,
        "prediction_horizon_steps": step_count,
        "price_levels_yuan_per_kwh": [1.2, 1.5, 1.8, 2.1, 2.4],
        "upstream_import_limit_kw": 60.0,
        "upstream_export_limit_kw": 0.0,
        "upstream_loss_fraction": 0.02,
        "sites": sites,
        "tie_lines": [],
        "site_configs": site_configs,
        "series": series,
        "method_scope": "Methods are diagnostic reference policies until B/C adapters are supplied.",
    }
    manifest["input_sha256"] = input_sha256(manifest)
    return manifest


def reference_providers() -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """Return four labeled diagnostic reference arms; not B/C policy results."""
    def make_action(site_id: str, context: Mapping[str, Any], *, price: float,
                    storage: float, ev_fraction: float = 1.0) -> dict[str, Any]:
        forecast = context["forecasts"][site_id]
        return {
            "site_id": site_id,
            "timestamp": context["timestamp"],
            "price_yuan_per_kwh": price,
            "requested_ev_kw": forecast["ev_forecast_kw"] * ev_fraction,
            "storage_kw": storage,
            "solver_status": "heuristic",
        }

    def fixed_price(context):
        price = 1.2
        return {site_id: make_action(site_id, context, price=price, storage=0.0)
                for site_id in context["forecasts"]}

    def rule_based(context):
        actions = {}
        for site_id, forecast in context["forecasts"].items():
            state = context["states"][site_id]
            net = forecast["pv_forecast_kw"] + forecast["wind_forecast_kw"] - forecast["load_forecast_kw"]
            storage = -8.0 if net > 4.0 and state["soc"] < 0.82 else (
                8.0 if net < 0.0 and state["soc"] > 0.3 else 0.0)
            price = 1.2 if net >= 0.0 else 2.1
            actions[site_id] = make_action(site_id, context, price=price, storage=storage)
        return actions

    def independent_site(context):
        actions = {}
        for site_id, forecast in context["forecasts"].items():
            state = context["states"][site_id]
            local_net = forecast["pv_forecast_kw"] + forecast["wind_forecast_kw"] - forecast["load_forecast_kw"] - forecast["ev_forecast_kw"]
            storage = 6.0 if local_net < -3.0 and state["soc"] > 0.3 else (
                -6.0 if local_net > 3.0 and state["soc"] < 0.8 else 0.0)
            actions[site_id] = make_action(site_id, context, price=1.8, storage=storage)
        return actions

    def centralized_joint(context):
        forecasts = context["forecasts"]
        states = context["states"]
        aggregate_net = sum(forecast["pv_forecast_kw"] + forecast["wind_forecast_kw"]
                            - forecast["load_forecast_kw"] - forecast["ev_forecast_kw"]
                            for forecast in forecasts.values())
        actions = {}
        # Shared signal only coordinates the direction; this is a transparent
        # heuristic baseline, not C's optimizer or objective.
        for site_id, forecast in forecasts.items():
            state = states[site_id]
            if aggregate_net < -4.0 and state["soc"] > 0.3:
                storage = 7.0
            elif aggregate_net > 4.0 and state["soc"] < 0.8:
                storage = -7.0
            else:
                storage = 0.0
            actions[site_id] = make_action(site_id, context,
                                           price=1.8 if aggregate_net >= 0 else 2.1,
                                           storage=storage)
        return actions

    providers = {
        "fixed-price": fixed_price,
        "rule": rule_based,
        "independent-site": independent_site,
        "centralized-joint": centralized_joint,
    }
    for provider in providers.values():
        provider.provider_type = "synthetic_reference_heuristic"
    return providers


def write_replay_artifacts(result: Mapping[str, Any], manifest: Mapping[str, Any],
                           output_dir: str | Path) -> Path:
    """Write manifest, summary, per-step ledger and a compact figure once.

    Existing directories are rejected so an earlier failed run cannot be
    overwritten by a rerun with the same run ID.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    run_manifest = copy.deepcopy(dict(manifest))
    run_manifest["run_metadata"] = {
        "schema_version": result["schema_version"],
        "trace_sha256": result["trace_sha256"],
        "configuration_sha256": result["configuration_sha256"],
        "method_config_sha256": result["method_config_sha256"],
        "method_providers": result["method_providers"],
        "source_code_revision": result["source_code_revision"],
        "source_tree_sha256": result["source_tree_sha256"],
        "working_tree_dirty": result["working_tree_dirty"],
        "seed": result["seed"],
        "missing_policy": result["missing_policy"],
        "accounting_model": result["accounting_model"],
    }
    (output / "manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False,
        indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    summaries = {key: {"status": value["status"], **value["summary"]}
                 for key, value in result["results"].items()}
    (output / "summary.json").write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "run_id": result["run_id"],
        "data_class": result["data_class"],
        "seed": result["seed"],
        "trace_sha256": result["trace_sha256"],
        "configuration_sha256": result["configuration_sha256"],
        "source_code_revision": result["source_code_revision"],
        "source_tree_sha256": result["source_tree_sha256"],
        "working_tree_dirty": result["working_tree_dirty"],
        "method_config_sha256": result["method_config_sha256"],
        "method_providers": result["method_providers"],
        "ranking_authorized": result["ranking_authorized"],
        "results": summaries,
    }, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    ledger = {method_id: {"status": value["status"], "steps": value["steps"]}
              for method_id, value in result["results"].items()}
    (output / "execution_ledger.json").write_text(json.dumps(ledger, ensure_ascii=False,
        indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        names = list(result["results"])
        served = [result["results"][name]["summary"]["served_kwh"] for name in names]
        unserved = [result["results"][name]["summary"]["unserved_kwh"] for name in names]
        figure, axes = plt.subplots(figsize=(8, 3.6))
        pos = list(range(len(names)))
        axes.bar([value - 0.18 for value in pos], served, width=0.36, label="EV served (kWh)")
        axes.bar([value + 0.18 for value in pos], unserved, width=0.36, label="EV unserved (kWh)")
        axes.set_xticks(pos, names, rotation=15, ha="right")
        axes.set_ylabel("kWh")
        axes.set_title("Synthetic paired replay: service ledger")
        axes.legend(frameon=False)
        figure.tight_layout()
        figure.savefig(output / "service_summary.png", dpi=140)
        plt.close(figure)
    except ImportError:
        pass
    return output
