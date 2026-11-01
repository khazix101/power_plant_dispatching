"""Run one 24-hour DC rolling experiment and write comparable metrics."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.game.dc_microgrid import DCMicrogridPowerFlow, DCSOCConfig, DCSOCState, balance_dc_step
from src.game.baseline import run_fixed_price_baseline
from src.game.case import BatteryParams, EVUser, GameCase
from src.game.dynamic_dc import DynamicDCSFSEController
from src.game.forecast_provider import CalibratedForecastProvider
from src.game.soh import evaluate_soh


def run() -> dict:
    provider = CalibratedForecastProvider(horizon_hours=24)
    controller = DynamicDCSFSEController(horizon_hours=24, max_sweeps=1)
    controller.reset(soc=0.5, soh=1.0)
    start = provider.start
    decisions = []
    for hour in range(24):
        timestamp = start + timedelta(hours=hour)
        bundle = provider.bundle(
            timestamp, controller.soc.soc, controller.soh,
            current_ev_load_kw=provider.ev_load[hour % 24],
        )
        decisions.append(controller.step(bundle))

    soc = [0.5] + [d.soc for d in decisions]
    power = [d.storage_kw for d in decisions]
    aging = evaluate_soh(soc, power, c_new=controller.soc_config.capacity_kwh * 1000.0)
    flow = [d.power_flow for d in decisions]
    return {
        "method": "dynamic_dc_bri",
        "hours": 24,
        "price": [d.price for d in decisions],
        "storage_kw": power,
        "ev_requested_kwh": sum(d.ev_requested_kw for d in decisions),
        "ev_served_kwh": sum(d.ev_served_kw for d in decisions),
        "ev_shed_kwh": sum(d.ev_shed_kw for d in decisions),
        "renewable_curtailed_kwh": sum(
            d.pv_curtailed_kw + d.wind_curtailed_kw for d in decisions
        ),
        "soc_start": soc[0],
        "soc_end": soc[-1],
        "soh_delta": aging["delta_soh_total"],
        "degradation_cost": aging["cost_total"],
        "voltage_min_pu": min(f["vmin_pu"] for f in flow),
        "voltage_max_pu": max(f["vmax_pu"] for f in flow),
        "line_current_max_a": max(
            (branch["current_a"] for f in flow for branch in f["branches"]),
            default=0.0,
        ),
        "ev_revenue_yuan": sum(d.price * d.ev_served_kw for d in decisions),
        "baseline_fixed_price_bess": run_fixed_price_bess_baseline(provider),
        "baseline_no_storage": run_no_storage_baseline(provider),
    }


def _build_daily_case(provider: CalibratedForecastProvider) -> GameCase:
    """Build the common 24-hour case used by the fixed-price baseline."""
    horizon = 24
    cap_kw = 480.0
    users = []
    for hour, load_kw in enumerate(provider.ev_load[:horizon]):
        energy = max(0.0, float(load_kw))
        if energy <= 1e-9:
            continue
        users.append(EVUser(
            name=f"forecast_{hour:02d}",
            value_per_kwh=1.55,
            energy_max_kwh=energy,
            energy_min_kwh=0.8 * energy,
            # 固定电价基线不应允许用户把全天需求自由集中到某一小时，
            # 否则 LP 会退化为角点解并扭曲基线。
            available=(hour,),
            max_power_kw=cap_kw,
        ))
    return GameCase(
        T=horizon,
        pv=[float(v) for v in provider.pv[:horizon]],
        wind=[float(v) for v in provider.wind[:horizon]],
        base_load=[float(v) for v in provider.non_ev[:horizon]],
        users=users,
        battery=BatteryParams(
            capacity_kwh=2000.0, power_kw=800.0, soc0=0.5,
            soc_min=0.1, soc_max=0.9, soc_end_min=0.2,
        ),
        price_levels=[0.6, 0.9, 1.2, 1.5, 1.8],
        penalty_shed=100.0,
        penalty_curt=0.2,
    )


def run_fixed_price_bess_baseline(provider: DailyCsvForecastProvider,
                                  fixed_price: float = 1.2) -> dict:
    """Fixed price + optimal BESS baseline executed on the DC network."""
    case = _build_daily_case(provider)
    planned = run_fixed_price_baseline(case, fixed_price=fixed_price)
    response = planned["response"]
    ev_profile = [
        sum(response[user.name][hour] for user in case.users)
        for hour in range(case.T)
    ]
    state = DCSOCState(DCSOCConfig(capacity_kwh=2000.0, power_kw=800.0,
                                   soc_min=0.1, soc_reserve=0.2, soc_max=0.9), 0.5)
    network = DCMicrogridPowerFlow()
    flows = []
    for hour in range(case.T):
        flows.append(balance_dc_step(
            network, state, case.pv[hour], case.wind[hour], ev_profile[hour],
            case.base_load[hour], planned["p_dis"][hour] - planned["p_ch"][hour],
            ev_service_limit_kw=800.0,
        ))
    return {
        "method": "fixed_price_plus_optimal_bess",
        "fixed_price": fixed_price,
        "planned_profit_yuan": planned["profit"],
        "planned_storage_kw": [
            pdis - pch for pdis, pch in zip(planned["p_dis"], planned["p_ch"])
        ],
        "ev_requested_kwh": sum(ev_profile),
        "ev_served_kwh": sum(f["ev_served_kw"] for f in flows),
        "ev_shed_kwh": sum(f["ev_shed_kw"] for f in flows),
        "renewable_curtailed_kwh": sum(
            f["pv_curtailed_kw"] + f["wind_curtailed_kw"] for f in flows
        ),
        "soc_start": 0.5,
        "soc_end": flows[-1]["soc"],
        "voltage_min_pu": min(f["power_flow"]["vmin_pu"] for f in flows),
        "voltage_max_pu": max(f["power_flow"]["vmax_pu"] for f in flows),
        "line_current_max_a": max(
            (b["current_a"] for f in flows for b in f["power_flow"]["branches"]),
            default=0.0,
        ),
    }


def run_no_storage_baseline(provider: DailyCsvForecastProvider) -> dict:
    """Serve the same base EV demand without BESS or price shifting."""
    config = DCSOCConfig(capacity_kwh=1.0, power_kw=0.0, soc_min=0.1,
                         soc_reserve=0.1, soc_max=0.9)
    state = DCSOCState(config, 0.5)
    network = DCMicrogridPowerFlow()
    served = 0.0
    requested = 0.0
    shed = 0.0
    curtailed = 0.0
    vmins, vmaxs = [], []
    for hour in range(24):
        timestamp = provider.start + timedelta(hours=hour)
        bundle = provider.bundle(timestamp, state.soc, 1.0)
        request = bundle.base_ev_load_forecast_kw[0]
        flow = balance_dc_step(
            network, state, bundle.pv_forecast_kw[0], bundle.wind_forecast_kw[0],
            request, bundle.non_ev_load_forecast_kw[0], 0.0,
            ev_service_limit_kw=800.0,
        )
        requested += request
        served += flow["ev_served_kw"]
        shed += flow["ev_shed_kw"]
        curtailed += flow["pv_curtailed_kw"] + flow["wind_curtailed_kw"]
        vmins.append(flow["power_flow"]["vmin_pu"])
        vmaxs.append(flow["power_flow"]["vmax_pu"])
    return {
        "method": "no_storage",
        "ev_requested_kwh": requested,
        "ev_served_kwh": served,
        "ev_shed_kwh": shed,
        "renewable_curtailed_kwh": curtailed,
        "voltage_min_pu": min(vmins),
        "voltage_max_pu": max(vmaxs),
    }


def main() -> None:
    result = run()
    output_dir = ROOT / "local" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "dc_24h_experiment.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"结果已保存: {path}")


if __name__ == "__main__":
    main()
