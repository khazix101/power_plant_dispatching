"""直流微网中的 SOC 状态滚动主从博弈原型。"""

from __future__ import annotations

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
from src.game.forecast import ForecastBundle
from src.game.soh import evaluate_soh


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
    soh: float
    soh_delta: float = 0.0
    degradation_cost: float = 0.0
    pv_curtailed_kw: float = 0.0
    wind_curtailed_kw: float = 0.0


class DynamicDCSFSEController:
    """基础 EV 负荷预测 + 价格响应 + 动态 SOC 的滚动控制器。"""

    def __init__(self, horizon_hours=24, flexibility=0.2,
                 dc_config=None, soc_config=None, price_levels=None,
                 max_sweeps=3, ev_service_limit_kw=None):
        self.horizon_hours = horizon_hours
        self.flexibility = flexibility
        self.dc_config = dc_config or DCNetworkConfig()
        self.soc_config = soc_config or DCSOCConfig()
        self.price_levels = price_levels or [0.6, 0.9, 1.2, 1.5, 1.8]
        self.max_sweeps = max_sweeps
        # 默认取全站装机上限（8 桩 x 60 kW = 480 kW），不再使用 800 kW。
        self.ev_service_limit_kw = (
            self.dc_config.ev_rated_kw
            if ev_service_limit_kw is None else float(ev_service_limit_kw)
        )
        # 保存基准容量，避免 SOH 缩放被重复累乘。
        self.base_capacity_kwh = float(self.soc_config.capacity_kwh)
        self.soc = DCSOCState(self.soc_config, self.soc_config.soc_min)
        self.remaining_energy_kwh = 0.0
        self.soh = 1.0

    def reset(self, soc=0.5, remaining_energy_kwh=0.0, soh=1.0):
        self.soc = DCSOCState(self.soc_config, soc)
        self.remaining_energy_kwh = max(0.0, float(remaining_energy_kwh))
        self.soh = float(soh)

    def finalize_day(self, soc_trajectory, power_trajectory):
        """Apply one day's aging result and return the updated SOH."""
        aging = evaluate_soh(
            soc_trajectory, power_trajectory,
            c_new=self.soc_config.capacity_kwh * 1000.0,
        )
        self.soh = max(0.7, self.soh - aging["delta_soh_total"])
        return {**aging, "soh_end": self.soh}

    def _build_case(self, pv, wind, ev_base, non_ev, current_ev=0.0, dt_hours=1.0):
        h = len(ev_base)
        cap_kw = float(self.dc_config.ev_rated_kw)
        # 当前小时的 EV 需求（已实现 + 尚未实现）属于刚性负荷：
        # 计入领导者功率平衡，但不参与本小时定价。
        base_load = [
            float(non_ev[i]) + (float(ev_base[0]) if i == 0 else 0.0)
            for i in range(h)
        ]

        users = []
        # 未来预测需求：逐小时用户，可在相邻小时之间小幅转移；
        # 需求价格弹性来自 value_per_kwh，若价格高于价值则不充电。
        values = (1.55, 1.35, 1.15)
        for t in range(1, h):
            energy = min(max(0.0, float(ev_base[t])) * dt_hours, cap_kw * dt_hours)
            if energy <= 1e-9:
                continue
            window = tuple(range(max(1, t - 1), min(h - 1, t + 1) + 1))
            users.append(EVUser(
                name=f"fc_{t:02d}",
                value_per_kwh=values[t % len(values)],
                energy_max_kwh=energy,
                energy_min_kwh=0.0,
                available=window,
                max_power_kw=cap_kw,
            ))

        # 未完成的服务需求：可在当前小时立即补足（窗口含 0），
        # 否则滚动执行时积压需求永远无法被兑现。
        if self.remaining_energy_kwh > 0:
            users.append(EVUser(
                name="rolling_ev_backlog",
                value_per_kwh=1.55,
                energy_max_kwh=self.remaining_energy_kwh,
                energy_min_kwh=(1.0 - self.flexibility) * self.remaining_energy_kwh,
                available=tuple(range(h)),
                max_power_kw=cap_kw,
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
            T=h,
            pv=list(pv),
            wind=list(wind),
            base_load=base_load,
            users=users,
            battery=battery,
            price_levels=list(self.price_levels),
            penalty_shed=100.0,
            penalty_curt=0.2,
        )

    def step(self, bundle_or_timestamp, pv_forecast_kw: List[float] | None = None,
             wind_forecast_kw: List[float] | None = None,
             ev_base_forecast_kw: List[float] | None = None,
             non_ev_forecast_kw: List[float] | None = None,
             current_ev_load_kw: float = 0.0, dt_hours=1.0):
        if isinstance(bundle_or_timestamp, ForecastBundle):
            bundle = bundle_or_timestamp
        else:
            h = min(self.horizon_hours, len(ev_base_forecast_kw or []))
            bundle = ForecastBundle(
                timestamp=bundle_or_timestamp, horizon_hours=h,
                pv_forecast_kw=list(pv_forecast_kw or [])[:h],
                wind_forecast_kw=list(wind_forecast_kw or [])[:h],
                base_ev_load_forecast_kw=list(ev_base_forecast_kw or [])[:h],
                non_ev_load_forecast_kw=list(non_ev_forecast_kw or [])[:h],
                current_ev_load_kw=current_ev_load_kw,
                soc_current=self.soc.soc, soh_current=self.soh,
            )
        h = min(self.horizon_hours, bundle.horizon_hours)
        pv = list(bundle.pv_forecast_kw[:h])
        wind = list(bundle.wind_forecast_kw[:h])
        ev = list(bundle.base_ev_load_forecast_kw[:h])
        non_ev = list(bundle.non_ev_load_forecast_kw[:h])
        self.soh = bundle.soh_current
        # 由基准容量按 SOH 缩放，避免在已缩放的容量上重复累乘。
        self.soc.config.capacity_kwh = self.base_capacity_kwh * self.soh
        current_ev = bundle.current_ev_load_kw
        previous_soc = self.soc.soc
        case = self._build_case(pv, wind, ev, non_ev, current_ev, dt_hours)
        horizon = solve_equilibrium_bri(
            case,
            init_prices=[1.2] * case.T,
            max_sweeps=self.max_sweeps,
        )
        # 当前小时已承诺需求（ev[0]）在 case.base_load 中，同时作为
        # 不可削减的 must-serve 送入直流安全层；柔性响应另行叠加。
        flexible_kw = sum(
            horizon["response"][user.name][0] for user in case.users
        )
        requested_kw = float(ev[0]) + flexible_kw
        network = DCMicrogridPowerFlow(self.dc_config)
        flow = balance_dc_step(
            network,
            self.soc,
            pv_kw=pv[0],
            wind_kw=wind[0],
            ev_request_kw=requested_kw,
            non_ev_kw=non_ev[0],
            p_schedule_kw=horizon["p_dis"][0] - horizon["p_ch"][0],
            dt_hours=dt_hours,
            ev_service_limit_kw=self.ev_service_limit_kw,
            ev_must_serve_kw=float(ev[0]),
        )
        served = flow["ev_served_kw"]
        unserved_rigid = flow["unserved_rigid_kw"]
        total_shed = flow["ev_shed_kw"] + unserved_rigid
        # 积压需求两本账：本轮实际补足的部分要扣减，当前小时新缺口要累加。
        served_flexible = sum(
            horizon["response"][user.name][0] for user in case.users
        )
        backlog_served_kw = float(
            horizon["response"].get("rolling_ev_backlog", [0.0])[0]
        )
        current_served_kw = max(0.0, served - served_flexible)
        current_shortfall_kw = max(0.0, float(ev[0]) - current_served_kw)
        self.remaining_energy_kwh = min(
            max(
                0.0,
                self.remaining_energy_kwh
                - backlog_served_kw * dt_hours
                + current_shortfall_kw * dt_hours,
            ),
            self.ev_service_limit_kw * 6.0,
        )
        total_requested_kw = requested_kw
        # 刚性缺额虽仍计入 requested，但并未真正供电，必须从 served 扣除。
        total_served_kw = max(0.0, served - unserved_rigid)
        degradation = evaluate_soh([previous_soc, flow["soc"]], [flow["p_bess_kw"]],
                                   c_new=self.soc_config.capacity_kwh * 1000.0)
        return DynamicDCDecision(
            timestamp=bundle.timestamp,
            price=horizon["prices"][0],
            storage_kw=flow["p_bess_kw"],
            ev_requested_kw=total_requested_kw,
            ev_served_kw=total_served_kw,
            ev_shed_kw=total_shed,
            soc=flow["soc"],
            remaining_energy_kwh=self.remaining_energy_kwh,
            power_flow=flow["power_flow"],
            horizon_result=horizon,
            soh=self.soh,
            soh_delta=degradation["delta_soh_total"],
            degradation_cost=degradation["cost_total"],
            pv_curtailed_kw=flow["pv_curtailed_kw"],
            wind_curtailed_kw=flow["wind_curtailed_kw"],
        )
