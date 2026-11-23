"""Run a paired multi-site replay and persist an append-only local ledger."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

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
    parser.add_argument("--include-reference-baselines", action="store_true",
                        help="add diagnostic fixed-price/rule/independent/joint heuristic arms")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    else:
        manifest = build_synthetic_manifest(seed=args.seed, step_count=args.steps)
    providers = {}
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
    output = args.output or root / "local/results/d_validation/replay" / f"{result['run_id']}-{suffix}"
    write_replay_artifacts(result, manifest, output)
    print(json.dumps({
        "status": result["status"],
        "run_id": result["run_id"],
        "trace_sha256": result["trace_sha256"],
        "configuration_sha256": result["configuration_sha256"],
        "source_code_revision": result["source_code_revision"],
        "output": str(output),
        "results": {name: {"status": value["status"], "summary": value["summary"]}
                    for name, value in result["results"].items()},
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
