"""Run a paired multi-site replay and persist an append-only local ledger."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.validation.multisite_replay import (
    adapt_coordinator,
    adapt_site_strategies,
    build_synthetic_manifest,
    reference_providers,
    run_paired_replay,
    write_replay_artifacts,
)


def _load_object(spec: str) -> Any:
    if ":" not in spec:
        raise ValueError("provider spec must be module:callable")
    module_name, attribute = spec.split(":", 1)
    value = getattr(importlib.import_module(module_name), attribute)
    return value() if callable(value) and not hasattr(value, "decide") and not hasattr(value, "coordinate") else value


def _extend_integration_package_paths(workspace_roots: list[Path]) -> dict[str, Any]:
    """Expose separate linked worktrees as explicit plugins without copying code."""
    import src
    import src.game

    resolved = [path.resolve() for path in workspace_roots]
    for workspace in resolved:
        source = workspace / "src"
        if not source.is_dir():
            raise FileNotFoundError(f"integration workspace has no src directory: {workspace}")
        if str(source) not in src.__path__:
            src.__path__.append(str(source))
        game_path = source / "game"
        if game_path.is_dir() and str(game_path) not in src.game.__path__:
            src.game.__path__.append(str(game_path))

    evidence = {}
    for workspace in resolved:
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace,
                                      check=True, capture_output=True, text=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            revision = "unavailable"
        digest = hashlib.sha256()
        source_root = workspace / "src"
        source_files = sorted(source_root.rglob("*.py"))
        for path in source_files:
            relative = path.relative_to(workspace).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        evidence[workspace.name] = {
            "workspace": str(workspace),
            "revision": revision,
            "src_tree_sha256": digest.hexdigest(),
        }
    return evidence


def _build_stage2_methods(manifest: dict[str, Any], roots: list[Path]) -> dict[str, Any]:
    evidence = _extend_integration_package_paths(roots)
    from src.game.site_strategy import CheckpointSiteStrategy
    from src.game.multisite_coordinator import CentralizedCoordinator

    price_levels = list(manifest["price_levels_yuan_per_kwh"])
    checkpoint = next((root / "local/b-price-smoke-74ee75b/policy-11.pt"
                       for root in roots if (root / "local/b-price-smoke-74ee75b/policy-11.pt").is_file()), None)
    if checkpoint is None:
        raise FileNotFoundError("B checkpoint local/b-price-smoke-74ee75b/policy-11.pt was not found in integration roots")
    checkpoint_sha256 = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    expected_checkpoint_sha256 = "d667eba06d4c53ce0e3ea1fda6184b89ae0452136211345620492bd44a108c1f"
    if checkpoint_sha256 != expected_checkpoint_sha256:
        raise ValueError("B checkpoint hash does not match the pinned Stage-2 policy")
    strategies = {
        site["site_id"]: CheckpointSiteStrategy(site["site_id"], checkpoint, price_levels)
        for site in manifest["sites"]
    }
    agent = adapt_site_strategies(strategies)
    agent.provider_type = "B_checkpoint_site_strategy"
    agent.provider_details = {
        "class": "src.game.site_strategy.CheckpointSiteStrategy",
        "checkpoint_sha256": checkpoint_sha256,
        "checkpoint_path": str(checkpoint.resolve()),
        "price_levels": price_levels,
    }
    baselines = reference_providers()
    fixed = baselines["fixed-price"]
    independent = baselines["independent-site"]
    fixed.provider_type = "fixed_price_reference"
    independent.provider_type = "independent_site_synthetic_reference_heuristic"
    coordinator = adapt_coordinator(CentralizedCoordinator())
    coordinator.provider_type = "C_CentralizedCoordinator"
    coordinator.provider_details = {"class": "src.game.multisite_coordinator.CentralizedCoordinator"}
    manifest["method_configs"] = {
        "fixed-price": {"price_yuan_per_kwh": price_levels[0], "provider_kind": "fixed-price"},
        "b-checkpoint-agent": agent.provider_details,
        "independent-site": {"provider_kind": "synthetic_reference_heuristic"},
        "c-centralized": {"method": "centralized", "provider_kind": "C CentralizedCoordinator"},
    }
    manifest["integration_sources"] = evidence
    from src.validation.multisite_replay import input_sha256
    manifest["input_sha256"] = input_sha256(manifest)
    return {
        "fixed-price": fixed,
        "b-checkpoint-agent": agent,
        "independent-site": independent,
        "c-centralized": coordinator,
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        help="JSON input matching A's multi-site v1 wire shape plus D replay configuration")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--missing-policy", choices=("reject", "mark"), default="reject")
    parser.add_argument("--site-strategies", help="module:factory returning {site_id: SiteStrategy}")
    parser.add_argument("--coordinator", help="module:factory returning a Coordinator")
    parser.add_argument("--integration-root", type=Path, action="append", default=[],
                        help="A/B/C linked worktree root for local Stage-2 integration; repeat per workspace")
    parser.add_argument("--stage2-smoke", action="store_true",
                        help="run fixed-price, real B checkpoint, independent-site reference, and real C coordinator")
    parser.add_argument("--include-reference-baselines", action="store_true",
                        help="add diagnostic fixed-price/rule/independent/joint heuristic arms")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    else:
        manifest = build_synthetic_manifest(seed=args.seed, step_count=args.steps)
    providers = _build_stage2_methods(manifest, args.integration_root) if args.stage2_smoke else {}
    if args.site_strategies:
        providers["agent"] = adapt_site_strategies(_load_object(args.site_strategies))
    if args.coordinator:
        providers["centralized-joint"] = adapt_coordinator(_load_object(args.coordinator))
    if args.include_reference_baselines or not args.manifest and not providers:
        providers.update(reference_providers())
    if not providers:
        parser.error("provide an Agent, coordinator, or diagnostic reference arm")

    result = run_paired_replay(manifest, providers, missing_policy=args.missing_policy)
    suffix = result["trace_sha256"][:12]
    run_suffix = uuid4().hex[:8]
    output = args.output or root / "local/results/d_validation/replay" / (
        f"{result['run_id']}-{suffix}-{run_suffix}")
    write_replay_artifacts(result, manifest, output)
    print(json.dumps({
        "status": result["status"],
        "run_id": result["run_id"],
        "data_class": result["data_class"],
        "ranking_authorized": result["ranking_authorized"],
        "trace_sha256": result["trace_sha256"],
        "configuration_sha256": result["configuration_sha256"],
        "common_input_hashes": result["common_input_hashes"],
        "source_code_revision": result["source_code_revision"],
        "integration_sources": manifest.get("integration_sources", {}),
        "output": str(output),
        "results": {name: {
            "status": value["status"],
            "provider": result["method_providers"][name],
            "common_input_hashes": value["common_input_hashes"],
            "steps_executed": value["summary"]["steps_executed"],
            "max_abs_energy_residual_kwh": value["summary"]["max_abs_energy_residual_kwh"],
            "projection_count": value["summary"]["projection_count"],
            "constraint_violation_count": value["summary"]["constraint_violation_count"],
        } for name, value in result["results"].items()},
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
