"""Build and validate the SOH x demand experiment matrix without running policies.

The runner is deliberately a configuration/schema gate. It does not instantiate
the dispatch controller or execute expensive strategy comparisons.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "soh-matrix-v1"
SOH_LEVELS = (1.0, 0.9, 0.8, 0.7)
DEMAND_QUANTILES = ("P10", "P50", "P90")
DEFAULT_SEEDS = (11, 23, 37)
MECHANISM_CONFIGS = (
    {"capacity_feedback_enabled": True, "degradation_cost_feedback_enabled": True},
    {"capacity_feedback_enabled": False, "degradation_cost_feedback_enabled": True},
    {"capacity_feedback_enabled": True, "degradation_cost_feedback_enabled": False},
    {"capacity_feedback_enabled": False, "degradation_cost_feedback_enabled": False},
)
SOLVER_RESIDUAL_FIELDS = (
    "price_residual",
    "demand_residual_kw",
    "soh_residual",
    "forecast_price_residual",
)
PHYSICAL_RESIDUAL_FIELDS = (
    "power_balance_residual_kw",
    "energy_balance_residual_kwh",
    "soc_transition_residual_fraction",
)
PERFORMANCE_FIELDS = (
    "requested_kwh", "served_kwh", "shed_kwh", "curtail_kwh",
    "battery_loss_kwh", "throughput_kwh", "aging_cost_yuan",
    "soc_end", "soh_end", "runtime_s",
)


class MatrixContractError(ValueError):
    """Raised when a matrix manifest or result row violates its contract."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def manifest_hash(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items()
               if key != "manifest_sha256"}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_manifest(seeds: tuple[int, ...] = DEFAULT_SEEDS) -> dict[str, Any]:
    """Create a reproducible, execution-free 4x3xreplicatesx4 manifest."""
    rows = []
    for soh in SOH_LEVELS:
        for demand in DEMAND_QUANTILES:
            for seed in seeds:
                for mechanisms in MECHANISM_CONFIGS:
                    rows.append({
                        "initial_soh": soh,
                        "demand_quantile": demand,
                        "replicate": {"kind": "seed", "id": f"seed-{seed}", "seed": seed},
                        "mechanisms": dict(mechanisms),
                        "initial_marginal_degradation_cost_policy": "matched_across_ablation_pairs",
                        "legacy_combined_soh_switch_used": False,
                    })
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "execution": "not_run",
        "design": {
            "initial_soh_levels": list(SOH_LEVELS),
            "demand_quantiles": list(DEMAND_QUANTILES),
            "minimum_replicates_per_soh_demand_cell": 3,
            "replicate_source": "seed",
            "mechanism_factors": [
                "capacity_feedback_enabled", "degradation_cost_feedback_enabled",
            ],
        },
        "result_schema": {
            "status_values": ["converged", "nonconverged", "failed"],
            "solver_residual_fields": list(SOLVER_RESIDUAL_FIELDS),
            "physical_residual_fields": list(PHYSICAL_RESIDUAL_FIELDS),
            "performance_fields": list(PERFORMANCE_FIELDS),
            "nonconverged_in_performance_summary": False,
        },
        "cases": rows,
    }
    manifest["manifest_sha256"] = manifest_hash(manifest)
    return manifest


def _number(value: Any, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MatrixContractError(f"{field} must be a finite number")
    value = float(value)
    if nonnegative and value < 0:
        raise MatrixContractError(f"{field} cannot be negative")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    required = {"schema_version", "execution", "design", "result_schema", "cases", "manifest_sha256"}
    missing = required - manifest.keys()
    if missing:
        raise MatrixContractError(f"manifest missing fields: {sorted(missing)}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise MatrixContractError(f"unsupported schema_version: {manifest['schema_version']}")
    if manifest["execution"] != "not_run":
        raise MatrixContractError("manifest must explicitly state execution=not_run")
    design = manifest["design"]
    if design.get("initial_soh_levels") != list(SOH_LEVELS):
        raise MatrixContractError("initial_soh_levels must be [1.0, 0.9, 0.8, 0.7]")
    if design.get("demand_quantiles") != list(DEMAND_QUANTILES):
        raise MatrixContractError("demand_quantiles must be [P10, P50, P90]")
    if design.get("minimum_replicates_per_soh_demand_cell", 0) < 3:
        raise MatrixContractError("at least three date/seed replicates are required per SOH-demand cell")
    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases:
        raise MatrixContractError("cases must be a non-empty array")
    cell_replicates: dict[tuple[float, str], set[tuple[str, str]]] = {}
    identities = set()
    required_mechanisms = {tuple(sorted(x.items())) for x in MECHANISM_CONFIGS}
    observed_mechanisms = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        soh = _number(case.get("initial_soh"), f"{prefix}.initial_soh")
        demand = case.get("demand_quantile")
        if soh not in SOH_LEVELS or demand not in DEMAND_QUANTILES:
            raise MatrixContractError(f"{prefix} has unsupported SOH or demand quantile")
        replicate = case.get("replicate")
        if not isinstance(replicate, dict) or replicate.get("kind") not in {"seed", "date"}:
            raise MatrixContractError(f"{prefix}.replicate must identify a date or seed")
        replicate_id = replicate.get("id")
        if not isinstance(replicate_id, str) or not replicate_id.strip():
            raise MatrixContractError(f"{prefix}.replicate.id must be non-empty")
        if replicate["kind"] == "seed":
            seed = replicate.get("seed")
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise MatrixContractError(f"{prefix}.replicate.seed must be an integer")
        mechanisms = case.get("mechanisms")
        if not isinstance(mechanisms, dict) or set(mechanisms) != {
            "capacity_feedback_enabled", "degradation_cost_feedback_enabled"
        } or any(not isinstance(v, bool) for v in mechanisms.values()):
            raise MatrixContractError(f"{prefix}.mechanisms must declare two independent boolean factors")
        mechanism_key = tuple(sorted(mechanisms.items()))
        observed_mechanisms.add(mechanism_key)
        if case.get("legacy_combined_soh_switch_used") is not False:
            raise MatrixContractError(f"{prefix} must not use the legacy combined SOH switch")
        if "use_soh_in_game" in case:
            raise MatrixContractError(f"{prefix} cannot contain use_soh_in_game")
        if case.get("initial_marginal_degradation_cost_policy") != "matched_across_ablation_pairs":
            raise MatrixContractError(f"{prefix} must match initial marginal degradation cost across ablations")
        cell = (soh, demand)
        rid = (replicate["kind"], replicate_id)
        cell_replicates.setdefault(cell, set()).add(rid)
        identity = (soh, demand, rid, mechanism_key)
        if identity in identities:
            raise MatrixContractError(f"duplicate case identity at {prefix}")
        identities.add(identity)
    if observed_mechanisms != required_mechanisms:
        raise MatrixContractError("all four capacity/cost mechanism combinations are required")
    for soh in SOH_LEVELS:
        for demand in DEMAND_QUANTILES:
            count = len(cell_replicates.get((soh, demand), set()))
            if count < 3:
                raise MatrixContractError(f"SOH={soh} {demand} has {count} replicates; requires at least 3")
    if manifest["manifest_sha256"] != manifest_hash(manifest):
        raise MatrixContractError("manifest_sha256 mismatch")


def validate_result_record(record: dict[str, Any]) -> None:
    """Validate one execution result; failure diagnostics remain explicit."""
    for field in ("initial_soh",):
        if _number(record.get(field), field) not in SOH_LEVELS:
            raise MatrixContractError(f"unsupported {field}")
    if record.get("demand_quantile") not in DEMAND_QUANTILES:
        raise MatrixContractError("unsupported demand_quantile")
    if record.get("status") not in {"converged", "nonconverged", "failed"}:
        raise MatrixContractError("status must be converged, nonconverged, or failed")
    mechanisms = record.get("mechanisms")
    if not isinstance(mechanisms, dict) or set(mechanisms) != {
        "capacity_feedback_enabled", "degradation_cost_feedback_enabled"
    } or any(not isinstance(v, bool) for v in mechanisms.values()):
        raise MatrixContractError("mechanisms must declare independent capacity and cost booleans")
    for field in SOLVER_RESIDUAL_FIELDS + PHYSICAL_RESIDUAL_FIELDS:
        _number(record.get(field), field, nonnegative=True)
    if record["status"] in {"nonconverged", "failed"}:
        diagnostic = record.get("failure_diagnostic")
        if not isinstance(diagnostic, str) or not diagnostic.strip():
            raise MatrixContractError("nonconverged and failed records require failure_diagnostic")
        return
    if "failure_diagnostic" in record:
        raise MatrixContractError("failure_diagnostic is reserved for failed records")
    for field in PERFORMANCE_FIELDS:
        value = _number(record.get(field), field, nonnegative=field not in {"soc_end", "soh_end"})
        if field in {"soc_end", "soh_end"} and not 0 <= value <= 1:
            raise MatrixContractError(f"{field} must be in [0, 1]")


def summarize_results(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return cell counts and converged-only performance means."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        validate_result_record(record)
        mech = record["mechanisms"]
        key = (float(record["initial_soh"]), record["demand_quantile"],
               mech["capacity_feedback_enabled"], mech["degradation_cost_feedback_enabled"])
        groups.setdefault(key, []).append(record)
    summaries = []
    for key, rows in sorted(groups.items()):
        converged = [r for r in rows if r["status"] == "converged"]
        nonconverged = [r for r in rows if r["status"] == "nonconverged"]
        summary = {
            "initial_soh": key[0], "demand_quantile": key[1],
            "mechanisms": {
                "capacity_feedback_enabled": key[2],
                "degradation_cost_feedback_enabled": key[3],
            },
            "n": len(rows), "converged": len(converged),
            "nonconverged": len(nonconverged), "failed": len(rows) - len(converged) - len(nonconverged),
            "performance_n": len(converged),
            "performance_mean_converged_only": {
                field: (sum(float(r[field]) for r in converged) / len(converged) if converged else None)
                for field in PERFORMANCE_FIELDS
            },
            "nonconverged_failure_diagnostics": [r.get("failure_diagnostic") for r in nonconverged],
            "failed_diagnostics": [r.get("failure_diagnostic") for r in rows if r["status"] == "failed"],
            "residual_max_all_attempts": {
                field: max(float(r[field]) for r in rows) for field in SOLVER_RESIDUAL_FIELDS + PHYSICAL_RESIDUAL_FIELDS
            },
        }
        summaries.append(summary)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="validate an existing SOH matrix manifest")
    parser.add_argument("--write-manifest", type=Path, help="write the default matrix manifest")
    args = parser.parse_args()
    if args.manifest and args.write_manifest:
        parser.error("choose either --manifest or --write-manifest")
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    else:
        manifest = build_manifest()
    validate_manifest(manifest)
    if args.write_manifest:
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cell_count = len(SOH_LEVELS) * len(DEMAND_QUANTILES)
    replicates = manifest["design"]["minimum_replicates_per_soh_demand_cell"]
    print(json.dumps({
        "status": "dry_run_validated",
        "manifest_sha256": manifest["manifest_sha256"],
        "soh_levels": list(SOH_LEVELS), "demand_quantiles": list(DEMAND_QUANTILES),
        "soh_demand_cells": cell_count,
        "minimum_replicates_per_cell": replicates,
        "mechanism_configs": len(MECHANISM_CONFIGS),
        "planned_case_count": len(manifest["cases"]),
        "policy_execution": "not_run",
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
