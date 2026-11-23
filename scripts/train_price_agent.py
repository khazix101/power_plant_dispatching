"""Train five price-only seeds and pair with baselines through D's replay.

Example: python scripts/train_price_agent.py --synthetic --episodes 2 --out artifacts/b-price-smoke
Real inputs: --partitions split.json (train/validation/test arrays of common-trace-v1).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from importlib.metadata import version
from pathlib import Path
import platform
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import torch
from scripts.run_fair_replay_experiments import build_sample_manifest, trace_hash
from src.game.price_agent import (SEEDS, SCHEMA, RewardConfig, digest, train,
                                  replay_policy, save_policy, validate_split)


def synthetic_partitions():
    """Three different days, two half-hour steps; perfect forecasts declared."""
    result = {}
    for day, name in enumerate(("train", "validation", "test"), 1):
        trace = build_sample_manifest(480)
        rows = []
        start = datetime(2024, 1, day, 12, tzinfo=timezone(timedelta(hours=8)))
        for i in range(2):
            ts = (start + timedelta(minutes=30 * i)).isoformat()
            pv = [180.0 + day * 5, 100.0 + day * 5][i]
            rows.append({"step_index": i, "timestamp": ts, "dt_hours": 0.5,
                         "pv_kw": pv, "wind_kw": 20.0, "base_load_kw": 60.0,
                         "ev_requested_kwh": 40.0, "ev_rigid_kwh": 5.0,
                         "forecast": {"issued_at": ts, "timestamps": [ts], "pv_kw": [pv],
                                      "wind_kw": [20.0], "base_kw": [60.0], "ev_kw": [80.0],
                                      "congestion": [0.0, 0.0, 0.0]}})
        trace["steps"] = rows
        trace["trace_sha256"] = trace_hash(trace)
        result[name] = [trace]
    return result


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true")
    source.add_argument("--partitions", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--bc-epochs", type=int, default=0)
    # One unified table, persisted in config; no per-baseline overrides.
    parser.add_argument("--price-levels", nargs="+", type=float, default=[1.5, 1.7, 1.9, 2.1, 2.3, 2.5])
    args = parser.parse_args()
    out = args.out.resolve()
    forbidden = {"dataset", "tmp", "output", "experiment_runs"}
    if forbidden.intersection(p.lower() for p in out.parts):
        raise ValueError("output path is in a protected data/generated directory")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("output directory must be empty")
    partitions = synthetic_partitions() if args.synthetic else json.loads(args.partitions.read_text(encoding="utf-8"))
    split_hash = validate_split(partitions)
    config = RewardConfig()
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    sources = {str(p.relative_to(ROOT)): __import__("hashlib").sha256(p.read_bytes()).hexdigest()
               for p in [Path(__file__), ROOT / "scripts/run_fair_replay_experiments.py",
                         *sorted((ROOT / "src/game").glob("*.py")),
                         *sorted((ROOT / "src/battery").glob("*.py"))]}
    manifest = {"schema": SCHEMA, "status": "running", "seeds": list(SEEDS),
                "split_sha256": split_hash, "source_revision": revision, "source_hashes": sources,
                "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
                "python": platform.python_version(), "numpy": np.__version__, "torch": torch.__version__,
                "solver": "appsi_highs", "highspy": version("highspy"), "pyomo": version("pyomo"),
                "reward": asdict(config), "price_levels": args.price_levels,
                "episodes": args.episodes, "bc_epochs": args.bc_epochs, "hidden": 64, "lr": 0.001,
                "return_scale": 100, "gradient_clip": 1.0,
                "response_assumption": "simulated EV utility=1.9 yuan/kWh; not real market causality",
                "forecast_assumption": "synthetic perfect forecast" if args.synthetic else "explicit input forecasts; provenance must be audited",
                "scope": "single-site price-only; D nominal-capacity frozen-SOH execution; thermal limits unverified",
                "test_lock": "test used only after validation checkpoint selection; no tuning on test"}
    dump(out / "manifest.json", manifest)
    dump(out / "partitions.json", partitions)
    results = []
    try:
        for seed in SEEDS:
            policy, history = train(partitions, args.price_levels, seed, args.episodes, args.bc_epochs, config)
            save_policy(out / f"policy-{seed}.pt", policy)
            dump(out / f"training-{seed}.json", history)
            for index, trace in enumerate(partitions["test"]):
                for method in ("agent", "fixed", "rule", "optimization"):
                    _, replay = replay_policy(trace, args.price_levels, method, policy, seed, config=config)
                    row = {"seed": seed, "test_episode": index, "method": method, **replay}
                    results.append(row)
                    dump(out / f"test-{seed}-{index}-{method}.json", row)
        aggregates = {}
        for method in ("agent", "fixed", "rule", "optimization"):
            group = [r for r in results if r["method"] == method]
            keys = [k for k, v in group[0]["summary"].items() if type(v) in (float, int)]
            aggregates[method] = {}
            for key in keys:
                # Date means within seed, then uncertainty across independent seeds.
                values = np.array([np.mean([r["summary"][key] for r in group if r["seed"] == s]) for s in SEEDS])
                mean, sd = float(values.mean()), float(values.std(ddof=1))
                half = 2.776445 * sd / np.sqrt(len(SEEDS))  # t(4), 95% interval
                aggregates[method][key] = {"mean": mean, "std": sd,
                                          "ci95_t4": [mean - half, mean + half],
                                          "seed_values": values.tolist()}
        paired = {}
        for method in ("fixed", "rule", "optimization"):
            a = np.array(aggregates["agent"]["operating_value_yuan"]["seed_values"])
            b = np.array(aggregates[method]["operating_value_yuan"]["seed_values"])
            delta = a - b
            half = float(2.776445 * delta.std(ddof=1) / np.sqrt(len(SEEDS)))
            paired[method] = {"mean_delta_yuan": float(delta.mean()),
                              "ci95_t4": [float(delta.mean()) - half, float(delta.mean()) + half]}
        dump(out / "summary.json", {"methods": aggregates, "paired_agent_delta": paired,
                                    "hard_counts": [{"seed": r["seed"], "method": r["method"],
                                                     "test_episode": r["test_episode"],
                                                     "counts": r["summary"]["hard_violation_counts"],
                                                     "terminal": r["summary"]["terminal_violation_count"]} for r in results]})
        manifest["status"] = "completed"
    except Exception:
        manifest.update(status="failed", failure=traceback.format_exc())
        raise
    finally:
        manifest["result_files"] = {p.name: __import__("hashlib").sha256(p.read_bytes()).hexdigest()
                                    for p in out.iterdir() if p.name != "manifest.json"}
        manifest["manifest_payload_sha256"] = digest(manifest)
        dump(out / "manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "out": str(out), "paired": paired}, indent=2))


if __name__ == "__main__":
    main()
