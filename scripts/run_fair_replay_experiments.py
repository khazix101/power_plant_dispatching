"""Auditable common-trace validation and shared DC replay execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.game.dc_microgrid import (
    DCMicrogridPowerFlow,
    DCSOCConfig,
    DCSOCState,
    balance_dc_step,
)


SCHEMA_VERSION = "common-trace-v1"
STEP_FIELDS = {
    "step_index", "timestamp", "dt_hours", "pv_kw", "wind_kw",
    "base_load_kw", "ev_requested_kwh", "ev_rigid_kwh",
}
EV_CAP_SCENARIOS = {480.0: "cap_480kw", 960.0: "cap_960kw"}
EXECUTION_FIELDS = {
    "battery_capacity_kwh", "battery_power_kw", "soc_min", "soc_reserve",
    "soc_max", "eta_charge", "eta_discharge",
}
RESIDUAL_UNITS = {
    "power_balance": "kW",
    "energy_balance": "kWh",
    "soc_transition": "fraction",
}


class ContractError(ValueError):
    """The trace or method configurations do not share one replay contract."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def trace_hash(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "trace_sha256"}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_sample_manifest(ev_service_cap_kw: float = 960.0) -> dict[str, Any]:
    """Small explicit example; values are illustrative, not an experiment result."""
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dt_hours": 0.5,
        "ev_service_cap_kw": float(ev_service_cap_kw),
        "ev_cap_scenario_id": EV_CAP_SCENARIOS.get(float(ev_service_cap_kw)),
        "initial_states": {"soc": 0.5, "soh": 1.0},
        "terminal_states": {"soc_min": 0.2, "soh_min": 0.0},
        "execution_config": {
            "battery_capacity_kwh": 2000.0,
            "battery_power_kw": 800.0,
            "soc_min": 0.1,
            "soc_reserve": 0.2,
            "soc_max": 0.9,
            "eta_charge": 0.95,
            "eta_discharge": 0.95,
        },
        "residual_units": dict(RESIDUAL_UNITS),
        "steps": [{
            "step_index": 0,
            "timestamp": "2024-01-01T00:00:00",
            "dt_hours": 0.5,
            "pv_kw": 0.0,
            "wind_kw": 60.0,
            "base_load_kw": 60.0,
            "ev_requested_kwh": 59.0,
            "ev_rigid_kwh": 0.0,
        }],
    }
    manifest["trace_sha256"] = trace_hash(manifest)
    return manifest


def default_method_configs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return two named placeholders bound to the identical trace contract."""
    common = {
        "trace_sha256": manifest["trace_sha256"],
        "dt_hours": manifest["dt_hours"],
        "ev_service_cap_kw": manifest["ev_service_cap_kw"],
        "ev_cap_scenario_id": manifest["ev_cap_scenario_id"],
        "initial_states": manifest["initial_states"],
        "terminal_states": manifest["terminal_states"],
        "execution_config": manifest["execution_config"],
        "residual_units": manifest["residual_units"],
    }
    return [
        {**common, "method_id": "method_a", "parameters": {}},
        {**common, "method_id": "method_b", "parameters": {}},
    ]


def validate_manifest(manifest: dict[str, Any]) -> None:
    required = {"schema_version", "dt_hours", "ev_service_cap_kw",
                "ev_cap_scenario_id", "execution_config",
                "initial_states", "terminal_states", "residual_units",
                "steps", "trace_sha256"}
    missing = required - manifest.keys()
    if missing:
        raise ContractError(f"manifest missing fields: {sorted(missing)}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ContractError(f"unsupported schema_version: {manifest['schema_version']}")
    dt = _positive_number(manifest["dt_hours"], "dt_hours")
    if not math.isclose(dt, 0.5, rel_tol=0.0, abs_tol=1e-12):
        raise ContractError("common-trace execution requires dt_hours=0.5")
    _positive_number(manifest["ev_service_cap_kw"], "ev_service_cap_kw")
    cap = float(manifest["ev_service_cap_kw"])
    expected_scenario = EV_CAP_SCENARIOS.get(cap)
    if expected_scenario is None or manifest["ev_cap_scenario_id"] != expected_scenario:
        raise ContractError("EV cap must explicitly select isolated cap_480kw or cap_960kw scenario")
    execution = manifest["execution_config"]
    if not isinstance(execution, dict) or set(execution) != EXECUTION_FIELDS:
        raise ContractError("execution_config must explicitly define the shared DC battery parameters")
    for name, value in execution.items():
        _positive_number(value, f"execution_config.{name}")
    if not 0 <= execution["soc_min"] < execution["soc_reserve"] <= execution["soc_max"] <= 1:
        raise ContractError("execution_config SOC bounds/reserve are inconsistent")
    if execution["eta_charge"] > 1 or execution["eta_discharge"] > 1:
        raise ContractError("battery efficiencies must be in (0, 1]")
    if manifest["residual_units"] != RESIDUAL_UNITS:
        raise ContractError("residual_units do not match required physical units")
    initial = manifest["initial_states"]
    terminal = manifest["terminal_states"]
    if not isinstance(initial, dict) or not isinstance(terminal, dict):
        raise ContractError("initial_states and terminal_states must be objects")
    if not initial or not terminal:
        raise ContractError("initial_states and terminal_states must be explicit")
    for state, value in initial.items():
        _finite_number(value, f"initial_states.{state}")
    for state, value in terminal.items():
        _finite_number(value, f"terminal_states.{state}")
    if "soc" in initial and not 0 <= initial["soc"] <= 1:
        raise ContractError("initial SOC must be in [0, 1]")
    if "soc_min" in terminal and not 0 <= terminal["soc_min"] <= 1:
        raise ContractError("terminal SOC minimum must be in [0, 1]")
    steps = manifest["steps"]
    if not isinstance(steps, list) or not steps:
        raise ContractError("steps must be a non-empty array")
    for index, step in enumerate(steps):
        missing_step = STEP_FIELDS - step.keys()
        if missing_step:
            raise ContractError(f"step {index} missing fields: {sorted(missing_step)}")
        if step["step_index"] != index:
            raise ContractError(f"step_index must be contiguous; expected {index}")
        if not isinstance(step["timestamp"], str) or not step["timestamp"]:
            raise ContractError(f"step {index} timestamp must be a non-empty string")
        step_dt = _positive_number(step["dt_hours"], f"steps[{index}].dt_hours")
        if not math.isclose(step_dt, dt, rel_tol=0.0, abs_tol=1e-12):
            raise ContractError(f"step {index} dt_hours differs from manifest dt_hours")
        for name in STEP_FIELDS - {"step_index", "timestamp", "dt_hours"}:
            number = _finite_number(step[name], f"steps[{index}].{name}")
            if name in {"ev_requested_kwh", "ev_rigid_kwh"} and number < 0:
                raise ContractError(f"step {index} {name} cannot be negative")
        if step["ev_rigid_kwh"] > step["ev_requested_kwh"] + 1e-9:
            raise ContractError(f"step {index} rigid EV energy exceeds physical request")
    expected_hash = trace_hash(manifest)
    if manifest["trace_sha256"] != expected_hash:
        raise ContractError("trace_sha256 mismatch")


def validate_method_configs(manifest: dict[str, Any],
                            methods: list[dict[str, Any]]) -> None:
    validate_manifest(manifest)
    if not isinstance(methods, list) or len(methods) != 2:
        raise ContractError("exactly two method configurations are required")
    expected = {
        "trace_sha256": manifest["trace_sha256"],
        "dt_hours": manifest["dt_hours"],
        "ev_service_cap_kw": manifest["ev_service_cap_kw"],
        "ev_cap_scenario_id": manifest["ev_cap_scenario_id"],
        "initial_states": manifest["initial_states"],
        "terminal_states": manifest["terminal_states"],
        "execution_config": manifest["execution_config"],
        "residual_units": manifest["residual_units"],
    }
    ids = set()
    for index, method in enumerate(methods):
        if not isinstance(method, dict):
            raise ContractError(f"method {index} must be an object")
        if not isinstance(method.get("method_id"), str) or not method["method_id"]:
            raise ContractError(f"method {index} requires method_id")
        ids.add(method["method_id"])
        for key, value in expected.items():
            if method.get(key) != value:
                raise ContractError(f"method {method['method_id']} has inconsistent {key}")
        if not isinstance(method.get("parameters"), dict):
            raise ContractError(f"method {method['method_id']} parameters must be an object")
    if len(ids) != 2:
        raise ContractError("method_id values must be distinct")


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f"{field} must be a finite number")
    return float(value)


def _positive_number(value: Any, field: str) -> float:
    number = _finite_number(value, field)
    if number <= 0:
        raise ContractError(f"{field} must be positive")
    return number


def load_and_validate(manifest_path: Path, methods_path: Path | None = None) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    methods = (json.loads(methods_path.read_text(encoding="utf-8"))
               if methods_path else default_method_configs(manifest))
    validate_method_configs(manifest, methods)
    return {"status": "dry_run_validated", "trace_sha256": manifest["trace_sha256"],
            "step_count": len(manifest["steps"]), "dt_hours": manifest["dt_hours"],
            "ev_service_cap_kw": manifest["ev_service_cap_kw"],
            "methods": [method["method_id"] for method in methods],
            "policy_execution": "not_run"}


def _dispatch_action(provider, step, state):
    action = provider(step, dict(state)) if callable(provider) else provider[step["step_index"]]
    if not isinstance(action, dict):
        raise ContractError("each dispatch action must be an object")
    accepted = _finite_number(action.get("accepted_ev_kw", step["ev_requested_kwh"] / step["dt_hours"]),
                              "action.accepted_ev_kw")
    schedule = _finite_number(action.get("p_schedule_kw", 0.0), "action.p_schedule_kw")
    price = _finite_number(action.get("price", 0.0), "action.price")
    if accepted < 0:
        raise ContractError("accepted EV power must be non-negative")
    requested = step["ev_requested_kwh"] / step["dt_hours"]
    rigid = step["ev_rigid_kwh"] / step["dt_hours"]
    if accepted < rigid - 1e-9 or accepted > requested + 1e-9:
        raise ContractError("accepted_ev_kw must lie between rigid request and physical request")
    converged = action.get("converged", True)
    if not isinstance(converged, bool):
        raise ContractError("action.converged must be boolean")
    residuals = action.get("residuals", {})
    if not isinstance(residuals, dict):
        raise ContractError("action.residuals must be an object")
    return accepted, schedule, price, converged, residuals


def _make_soc_config(execution):
    return DCSOCConfig(
        capacity_kwh=execution["battery_capacity_kwh"],
        power_kw=execution["battery_power_kw"],
        soc_min=execution["soc_min"],
        soc_reserve=execution["soc_reserve"],
        soc_max=execution["soc_max"],
        eta_charge=execution["eta_charge"],
        eta_discharge=execution["eta_discharge"],
    )


def _execute_method(manifest, provider):
    execution = manifest["execution_config"]
    soc_config = _make_soc_config(execution)
    soc = DCSOCState(soc_config, soc=manifest["initial_states"]["soc"])
    soh = manifest["initial_states"]["soh"]
    network = DCMicrogridPowerFlow()
    records = []
    totals = {name: 0.0 for name in (
        "requested_kwh", "accepted_kwh", "served_kwh", "shed_kwh",
        "suppressed_kwh", "curtailment_kwh", "battery_loss_kwh",
        "network_loss_kwh", "energy_residual_kwh", "unserved_rigid_kwh",
        "served_revenue",
    )}
    included_steps = 0
    excluded_steps = 0
    for step in manifest["steps"]:
        dt = step["dt_hours"]
        state = {"soc": soc.soc, "soh": soh}
        accepted_kw, schedule_kw, price, converged, residuals = _dispatch_action(
            provider, step, state)
        requested_kw = step["ev_requested_kwh"] / dt
        rigid_kw = step["ev_rigid_kwh"] / dt
        common = {
            "step_index": step["step_index"], "timestamp": step["timestamp"],
            "dt_hours": dt, "price": price,
            "requested_kw": requested_kw, "accepted_kw": accepted_kw,
            "suppressed_kw": requested_kw - accepted_kw,
            "soc_before": soc.soc, "soh": soh,
            "residuals": residuals, "converged": converged,
        }
        if not converged:
            excluded_steps += 1
            records.append({**common, "execution_status": "nonconverged_skipped",
                            "included_in_performance": False})
            continue
        soc_before = soc.soc
        flow = balance_dc_step(
            network, soc,
            pv_kw=step["pv_kw"], wind_kw=step["wind_kw"],
            ev_request_kw=accepted_kw, non_ev_kw=step["base_load_kw"],
            p_schedule_kw=schedule_kw, dt_hours=dt,
            ev_service_limit_kw=manifest["ev_service_cap_kw"],
            ev_must_serve_kw=rigid_kw,
        )
        rigid_unserved_kw = min(rigid_kw, flow["unserved_rigid_kw"])
        served_kw = max(0.0, flow["ev_served_kw"] - rigid_unserved_kw)
        shed_kw = max(0.0, accepted_kw - served_kw)
        curtail_kw = flow["pv_curtailed_kw"] + flow["wind_curtailed_kw"]
        battery_power = flow["p_bess_kw"]
        if battery_power >= 0:
            battery_loss_kw = battery_power * (1.0 / soc_config.eta_discharge - 1.0)
        else:
            battery_loss_kw = -battery_power * (1.0 - soc_config.eta_charge)
        energy_residual = flow["residual_kw"] * dt
        soc_expected = soc_before - battery_power * dt / (
            (soc_config.eta_discharge if battery_power >= 0 else 1.0 / soc_config.eta_charge)
            * soc_config.capacity_kwh
        )
        soc_residual = soc.soc - soc_expected
        values = {
            "requested_kwh": requested_kw * dt,
            "accepted_kwh": accepted_kw * dt,
            "served_kwh": served_kw * dt,
            "shed_kwh": shed_kw * dt,
            "suppressed_kwh": (requested_kw - accepted_kw) * dt,
            "curtailment_kwh": curtail_kw * dt,
            "battery_loss_kwh": battery_loss_kw * dt,
            "network_loss_kwh": flow["losses_kw"] * dt,
            "energy_residual_kwh": energy_residual,
            "unserved_rigid_kwh": rigid_unserved_kw * dt,
            "served_revenue": price * served_kw * dt,
        }
        for name, value in values.items():
            totals[name] += value
        included_steps += 1
        records.append({
            **common, "execution_status": "executed", "included_in_performance": True,
            "served_kw": served_kw, "shed_kw": shed_kw,
            "ev_shed_kw": flow["ev_shed_kw"], "unserved_rigid_kw": rigid_unserved_kw,
            "p_bess_kw": battery_power,
            "curtailment_kw": curtail_kw,
            "battery_loss_kw": battery_loss_kw,
            "network_loss_kw": flow["losses_kw"],
            "energy_residual_kw": flow["residual_kw"],
            "energy_residual_kwh": energy_residual,
            "soc": soc.soc, "soc_residual": soc_residual,
            "soh_after": soh,
            "voltage_feasible": flow["voltage_feasible"],
            "thermal_limits_verified": flow["power_flow"]["thermal_limits_verified"],
            **values,
        })
    terminal_min = manifest["terminal_states"].get("soc_min", 0.0)
    summary = {
        **totals,
        "steps_total": len(records), "steps_included": included_steps,
        "steps_nonconverged_excluded": excluded_steps,
        "terminal_soc": soc.soc, "terminal_soh": soh,
        "terminal_soc_min": terminal_min,
        "terminal_soc_feasible": soc.soc + 1e-12 >= terminal_min,
        "rigid_unserved_kwh": totals["unserved_rigid_kwh"],
    }
    return {"steps": records, "summary": summary}


def execute_common_trace(manifest: dict[str, Any], methods: list[dict[str, Any]],
                         action_providers: dict[str, Any]) -> dict[str, Any]:
    """Execute supplied dispatch callbacks on the same exogenous trace/stepper.

    A provider returns price, accepted_ev_kw, p_schedule_kw, converged and
    residuals for each step. This is an interface only: no policy is built or
    ranked here. Each method starts from the identical manifest state/config.
    """
    validate_method_configs(manifest, methods)
    method_ids = {method["method_id"] for method in methods}
    if set(action_providers) != method_ids:
        raise ContractError("action_providers must exactly match the two method_id values")
    outputs = {
        method_id: _execute_method(manifest, action_providers[method_id])
        for method_id in sorted(method_ids)
    }
    return {
        "status": "execution_interface_completed",
        "trace_sha256": manifest["trace_sha256"],
        "dt_hours": manifest["dt_hours"],
        "ev_service_cap_kw": manifest["ev_service_cap_kw"],
        "ev_cap_scenario_id": manifest["ev_cap_scenario_id"],
        "results": outputs,
        "policy_comparison": "not_performed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        help="common-trace-v1 manifest JSON; omitted uses an in-memory example")
    parser.add_argument("--methods", type=Path,
                        help="JSON array containing exactly two bound method configurations")
    args = parser.parse_args()
    if args.manifest:
        result = load_and_validate(args.manifest, args.methods)
    else:
        if args.methods:
            parser.error("--methods requires --manifest")
        manifest = build_sample_manifest()
        methods = default_method_configs(manifest)
        validate_method_configs(manifest, methods)
        result = {"status": "dry_run_validated", "trace_sha256": manifest["trace_sha256"],
                  "step_count": len(manifest["steps"]), "dt_hours": manifest["dt_hours"],
                  "ev_service_cap_kw": manifest["ev_service_cap_kw"],
                  "methods": [method["method_id"] for method in methods],
                  "policy_execution": "not_run"}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
