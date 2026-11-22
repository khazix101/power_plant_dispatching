"""Fast ex-post energy-ledger replay of a completed checkpoint arm.

It calls the current DC execution stepper, without BRI or optimization.
The archived checkpoint is preserved as pre-correction evidence; this replay
uses the revised stepper that re-solves voltage after base shedding.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.game.dc_microgrid import DCNetworkConfig, DCMicrogridPowerFlow, DCSOCConfig, DCSOCState, balance_dc_step


def replay(checkpoint, arm="fixed_price_bess"):
    start = time.perf_counter()
    previous = checkpoint["results"][arm]
    if "summary" not in previous:
        raise ValueError("selected arm has no completed full-window summary")
    initial = previous["summary"]
    capacity = 2000.0 * initial["soh_start"]
    soc = DCSOCState(DCSOCConfig(capacity_kwh=capacity, power_kw=800.0,
        soc_min=0.1, soc_reserve=0.2, soc_max=0.9), initial["soc_start"])
    cap = checkpoint["ev_service_cap_kw"]
    network = DCMicrogridPowerFlow(DCNetworkConfig(ev_posts=8, ev_post_kw=cap / 8))
    records = []
    for step in previous["steps"]:
        dt = step["dt_hours"]
        flow = balance_dc_step(network, soc, step["pv_kw"], step["wind_kw"],
            step["ev_requested_kw"], step["base_load_kw"], step["p_bess_kw"],
            dt_hours=dt, ev_service_limit_kw=cap)
        non_ev_unserved = flow["non_ev_unserved_kw"]
        power = flow["p_bess_kw"]
        loss = power * (1 / 0.95 - 1) if power >= 0 else -power * (1 - 0.95)
        records.append({"timestamp": step["timestamp"], "dt_hours": dt,
            "ev_requested_kwh": step["ev_requested_kw"] * dt,
            "ev_served_kwh": flow["ev_served_kw"] * dt,
            "ev_shed_kwh": flow["ev_shed_kw"] * dt,
            "non_ev_requested_kwh": step["base_load_kw"] * dt,
            "non_ev_served_kwh": (step["base_load_kw"] - non_ev_unserved) * dt,
            "non_ev_unserved_kwh": non_ev_unserved * dt,
            "renewable_input_kwh": (step["pv_kw"] + step["wind_kw"]) * dt,
            "curtailment_kwh": (flow["pv_curtailed_kw"] + flow["wind_curtailed_kw"]) * dt,
            "battery_loss_kwh": loss * dt, "network_loss_kwh": flow["losses_kw"] * dt,
            "raw_power_balance_residual_kwh": flow["residual_kw"] * dt,
            "shortage_accounted_residual_kwh": flow["residual_kw"] * dt,
            "ex_post_delivered_revenue_yuan": step["price"] * flow["ev_served_kw"] * dt,
            "p_bess_kw": power, "soc": soc.soc})
    fields = [name for name in records[0] if name.endswith("_kwh") or name.endswith("_yuan")]
    totals = {name: sum(record[name] for record in records) for name in fields}
    stored = (soc.soc - initial["soc_start"]) * capacity
    identity = totals["renewable_input_kwh"] - totals["ev_served_kwh"] - totals["non_ev_served_kwh"] - totals["curtailment_kwh"] - totals["battery_loss_kwh"] - totals["network_loss_kwh"] - stored
    physical_ok = abs(identity) <= 1e-6 and max(abs(r["raw_power_balance_residual_kwh"]) for r in records) <= 1e-6
    return {"status": "service_shortfall_completed" if physical_ok and totals["non_ev_unserved_kwh"] > 1e-6 else ("ledger_replay_completed" if physical_ok else "failed_physical_gate"),
        "eligible_for_policy_ranking": False, "method": "fast_execution_ledger_replay",
        "source_arm": arm, "provenance": checkpoint["provenance"],
        "ev_cap_scenario_id": checkpoint["ev_cap_scenario_id"], "steps": records,
        "summary": {**totals, "stored_energy_change_kwh": stored,
            "energy_identity_residual_kwh": identity,
            "useful_delivery_kwh": totals["ev_served_kwh"] + totals["non_ev_served_kwh"],
            "soc_end": soc.soc, "runtime_s": time.perf_counter() - start},
        "limitations": ["revised execution re-solves voltage after base shedding; archived source checkpoint used the earlier stepper",
            "pre-correction planned action optimized requested-energy revenue; revenue here is delivered-energy only",
            "no Agent arm and no paired uncertainty estimate"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(json.loads(args.checkpoint.read_text(encoding="utf-8")))
    result["source_checkpoint_sha256"] = hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
