"""直流微网中的 SOC 状态滚动主从博弈原型。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from src.game.bri import solve_equilibrium_bri
from src.game.case import BatteryParams, EVUser, GameCase
from src.game.dc_microgrid import (
    DCNetworkConfig,
    DCMicrogridPowerFlow,
    DCSOCConfig,
    DCSOCState,
    balance_dc_step,
)


@dataclass
class DynamicDCDecision:
    timestamp: datetime
    price: float
    storage_kw: float
    ev_requested_kw: float
    ev_served_kw: float
    ev_shed_kw: float
    soc: float
    remaining_energy_kwh: float
    power_flow: Dict
    horizon_result: Dict


class DynamicDCSFSEController:
    """基础 EV 负荷预测 + 价格响应 + 动态 SOC 的滚动控制器。"""

    def __init__(self, horizon_hours=24, flexibility=0.2,
                 dc_config=None, soc_config=None, price_levels=None,
                 max_sweeps=3):
        self.horizon_hours = horizon_hours
        self.flexibility = flexibility
        self.dc_config = dc_config or DCNetworkConfig()
        self.soc_config = soc_config or DCSOCConfig()
        self.price_levels = price_levels or [0.6, 0.9, 1.2, 1.5, 1.8]
        self.max_sweeps = max_sweeps
        self.soc = DCSOCState(self.soc_config, self.soc_config.soc_min)
        self.remaining_energy_kwh = 0.0

    def reset(self, soc=0.5, remaining_energy_kwh=0.0):
        self.soc = DCSOCState(self.soc_config, soc)
        self.remaining_energy_kwh = max(0.0, float(remaining_energy_kwh))

    def _build_case(self, pv, wind, ev_base, non_ev, dt_hours=1.0):
        current_base_energy = max(0.0, ev_base[0]) * dt_hours
        users = []
        if current_base_energy > 0:
            # The current-hour base demand is already committed; price cannot
            # move it into a future hour.
            users.append(EVUser(
                name="current_ev_demand",
                value_per_kwh=1.55,
                energy_max_kwh=current_base_energy,
                energy_min_kwh=current_base_energy,
                available=(0,),
                max_power_kw=max(current_base_energy / max(dt_hours, 1e-9), 1.0),
            ))
        if self.remaining_energy_kwh > 0:
            # Backlog demand remains flexible inside the forecast window.
            users.append(EVUser(
                name="rolling_ev_backlog",
                value_per_kwh=1.55,
                energy_max_kwh=self.remaining_energy_kwh,
                energy_min_kwh=(1.0 - self.flexibility) * self.remaining_energy_kwh,
                available=tuple(range(len(ev_base))),
                max_power_kw=max(self.remaining_energy_kwh / max(dt_hours, 1e-9), 1.0),
            ))
        battery = BatteryParams(
            capacity_kwh=self.soc_config.capacity_kwh,
            power_kw=self.soc_config.power_kw,
            charge_eff=self.soc_config.eta_charge,
            discharge_eff=self.soc_config.eta_discharge,
            soc0=self.soc.soc,
            soc_min=self.soc_config.soc_min,
            soc_max=self.soc_config.soc_max,
            soc_end_min=max(
                self.soc_config.soc_reserve,
                self.soc_config.soc_min,
            ),
            deg_cost_per_kwh=0.05,
        )
        return GameCase(
            T=len(ev_base),
            pv=list(pv),
            wind=list(wind),
            base_load=list(non_ev),
            users=users,
            battery=battery,
            price_levels=list(self.price_levels),
            penalty_shed=100.0,
            penalty_curt=0.2,
        )

    def step(self, timestamp, pv_forecast_kw: List[float],
             wind_forecast_kw: List[float], ev_base_forecast_kw: List[float],
             non_ev_forecast_kw: List[float], dt_hours=1.0):
        h = min(self.horizon_hours, len(ev_base_forecast_kw))
        pv = list(pv_forecast_kw[:h])
        wind = list(wind_forecast_kw[:h])
        ev = list(ev_base_forecast_kw[:h])
        non_ev = list(non_ev_forecast_kw[:h])
        case = self._build_case(pv, wind, ev, non_ev, dt_hours)
        horizon = solve_equilibrium_bri(
            case,
            init_prices=[1.2] * case.T,
            max_sweeps=self.max_sweeps,
        )
        requested = sum(
            horizon["response"][user.name][0]
            for user in case.users
        )
        network = DCMicrogridPowerFlow(self.dc_config)
        flow = balance_dc_step(
            network,
            self.soc,
            pv_kw=pv[0],
            wind_kw=wind[0],
            ev_request_kw=requested,
            non_ev_kw=non_ev[0],
            p_schedule_kw=horizon["p_dis"][0] - horizon["p_ch"][0],
            dt_hours=dt_hours,
        )
        served = flow["ev_served_kw"]
        self.remaining_energy_kwh = max(
            0.0,
            self.remaining_energy_kwh + ev[0] * dt_hours - served * dt_hours,
        )
        return DynamicDCDecision(
            timestamp=timestamp,
            price=horizon["prices"][0],
            storage_kw=flow["p_bess_kw"],
            ev_requested_kw=flow["ev_requested_kw"],
            ev_served_kw=served,
            ev_shed_kw=flow["ev_shed_kw"],
            soc=flow["soc"],
            remaining_energy_kwh=self.remaining_energy_kwh,
            power_flow=flow["power_flow"],
            horizon_result=horizon,
        )
