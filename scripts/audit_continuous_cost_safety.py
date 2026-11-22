"""Read-only paired cost and prototype network audit for completed days."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.game.dc_microgrid import DCMicrogridPowerFlow, DCNetworkConfig


ARMS = ("fixed_price_bess", "dynamic_fixed_cost", "soh_aware")
FIELDS = (
    "requested_kwh", "served_kwh", "shed_kwh", "non_ev_unserved_kwh",
    "revenue_yuan", "aging_cost_yuan", "replacement_cost_yuan",
    "curtailment_kwh", "battery_loss_kwh", "network_loss_kwh",
    "curtailment_plus_loss_kwh", "throughput_kwh",
    "modeled_operating_value_yuan", "runtime_s",
)


def completed_days(run):
    return {day["date"]: day for day in run["days"]
            if day["status"] == "diagnostic_completed"}


def totals(days):
    return {field: sum(day["summary"].get(field, 0.0) for day in days)
            for field in FIELDS}


def network_audit(days):
    network = DCMicrogridPowerFlow(DCNetworkConfig(ev_posts=8, ev_post_kw=60))
    result = {
        "steps": 0, "unverified_thermal_steps": 0,
        "voltage_violations": 0, "min_voltage_pu": float("inf"),
        "max_voltage_pu": float("-inf"), "max_network_loss_error_kwh": 0.0,
        "soc_min": float("inf"), "soc_max": float("-inf"),
        "terminal_soc_failures": 0, "nonconverged_steps": 0,
        "max_daily_identity_residual_kwh": 0.0,
    }
    for day in days:
        summary = day["summary"]
        result["terminal_soc_failures"] += not summary["terminal_soc_feasible"]
        result["nonconverged_steps"] += summary["nonconverged_steps"]
        result["max_daily_identity_residual_kwh"] = max(
            result["max_daily_identity_residual_kwh"],
            abs(summary["energy_identity_residual_kwh"]),
        )
        for step in day["steps"]:
            dt = step["dt_hours"]
            curtail_kw = step["curtailment_kwh"] / dt
            pv_used = max(0.0, step["pv_kw"] - min(step["pv_kw"], curtail_kw))
            wind_used = max(0.0, step["wind_kw"] -
                            max(0.0, curtail_kw - step["pv_kw"]))
            load_kw = (step["served_kwh"] + step["non_ev_served_kwh"]) / dt
            flow = network.solve({
                "PV_Bus": pv_used,
                "Wind_Bus": wind_used,
                "Load_Bus": -load_kw,
            })
            result["steps"] += 1
            result["unverified_thermal_steps"] += not flow["thermal_limits_verified"]
            result["voltage_violations"] += not flow["voltage_feasible"]
            result["min_voltage_pu"] = min(result["min_voltage_pu"], flow["vmin_pu"])
            result["max_voltage_pu"] = max(result["max_voltage_pu"], flow["vmax_pu"])
            result["max_network_loss_error_kwh"] = max(
                result["max_network_loss_error_kwh"],
                abs(flow["losses_kw"] * dt - step["network_loss_kwh"]),
            )
            result["soc_min"] = min(result["soc_min"], step["soc"])
            result["soc_max"] = max(result["soc_max"], step["soc"])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    runs = {
        arm: json.loads((args.run_directory / f"{arm}.json").read_text(
            encoding="utf8"))
        for arm in ARMS
    }
    days = {arm: completed_days(run) for arm, run in runs.items()}
    paired_dates = sorted(set(days["dynamic_fixed_cost"]) &
                          set(days["soh_aware"]))
    paired = {
        arm: [days[arm][date] for date in paired_dates]
        for arm in ARMS
    }
    paired_totals = {arm: totals(records) for arm, records in paired.items()}
    delta = {field: paired_totals["soh_aware"][field] -
             paired_totals["dynamic_fixed_cost"][field] for field in FIELDS}
    avoided_non_ev = -delta["non_ev_unserved_kwh"]
    without_non_ev_penalty = (
        delta["modeled_operating_value_yuan"] - 1000 * avoided_non_ev)
    report = {
        "completed_days": {arm: len(records) for arm, records in days.items()},
        "paired_days": len(paired_dates),
        "paired_first_date": paired_dates[0],
        "paired_last_date": paired_dates[-1],
        "paired_totals": paired_totals,
        "soh_minus_dynamic_fixed_cost": delta,
        "non_ev_penalty_sensitivity_yuan": {
            str(penalty): without_non_ev_penalty + penalty * avoided_non_ev
            for penalty in (0, 5, 10, 50, 100, 1000)
        },
        "break_even_non_ev_penalty_yuan_per_kwh":
            -without_non_ev_penalty / avoided_non_ev if avoided_non_ev else None,
        "network_reconstruction": {
            arm: network_audit(records) for arm, records in paired.items()
        },
    }
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
