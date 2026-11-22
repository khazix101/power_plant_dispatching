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
from src.battery.state import BatteryState
from src.battery.degradation import marginal_cycle_cost_per_kwh
from src.game.state_transition import transition_battery_state


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
    overflow_unserved_kwh: float = 0.0
    price_demand_iterations: int = 1
    price_demand_residual: float = 0.0
    unserved_rigid_kw: float = 0.0
    price_residual: float = 0.0
    demand_residual: float = 0.0
    soh_residual: float = 0.0
    price_demand_converged: bool = True
    forecast_price_residual: float = 0.0
    price_forecast_relaxation: float = 1.0


class DynamicDCSFSEController:
    """基础 EV 负荷预测 + 价格响应 + 动态 SOC 的滚动控制器。"""

    def __init__(self, horizon_hours=24, flexibility=0.2,
                 dc_config=None, soc_config=None, price_levels=None,
                 max_sweeps=3, ev_service_limit_kw=None,
                 dispatch_interval_hours=0.5, backlog_limit_hours=6.0,
                 response_value_levels=None, ev_congestion_penalty=0.0,
                 current_flexible_fraction=1.0,
                 price_demand_max_iter=5, price_demand_tol=1e-3,
                 use_soh_in_game=True, ev_demand_elasticity=1.157,
                 demand_reference_price=1.4, curtailment_penalty=0.2,
                 price_forecast_relaxation=1.0,
                 adaptive_price_forecast_relaxation=False,
                 use_soh_capacity_in_game=None):
        self.horizon_hours = horizon_hours
        self.flexibility = flexibility
        self.dc_config = dc_config or DCNetworkConfig()
        self.soc_config = soc_config or DCSOCConfig()
        self.price_levels = price_levels or [1.5, 1.7, 1.9, 2.1, 2.3, 2.5]
        self.max_sweeps = max_sweeps
        # These are behavioral willingness-to-pay proxies, not identified
        # market prices.  Keep them configurable for calibration/sensitivity
        # studies while preserving the historical default.
        self.response_value_levels = tuple(
            float(v) for v in (response_value_levels or (1.55, 1.35, 1.15))
        )
        if not self.response_value_levels or any(v <= 0 for v in self.response_value_levels):
            raise ValueError("response_value_levels 必须包含正数")
        self.ev_congestion_penalty = float(ev_congestion_penalty)
        if self.ev_congestion_penalty < 0:
            raise ValueError("ev_congestion_penalty 不允许为负")
        self.current_flexible_fraction = float(current_flexible_fraction)
        if not 0.0 <= self.current_flexible_fraction <= 1.0:
            raise ValueError("current_flexible_fraction 必须在 [0, 1] 内")
        self.price_demand_max_iter = int(price_demand_max_iter)
        self.price_demand_tol = float(price_demand_tol)
        self.use_soh_in_game = bool(use_soh_in_game)
        # Capacity and degradation-cost feedback are separate experimental
        # factors.  Preserve the historical behavior when omitted, while
        # allowing fixed-cost arms to retain physical SOH capacity limits.
        self.use_soh_capacity_in_game = (
            self.use_soh_in_game if use_soh_capacity_in_game is None
            else bool(use_soh_capacity_in_game)
        )
        self.ev_demand_elasticity = float(ev_demand_elasticity)
        self.demand_reference_price = float(demand_reference_price)
        self.curtailment_penalty = float(curtailment_penalty)
        self.price_forecast_relaxation = float(price_forecast_relaxation)
        self.adaptive_price_forecast_relaxation = bool(
            adaptive_price_forecast_relaxation
        )
        if self.curtailment_penalty < 0:
            raise ValueError("curtailment_penalty 不允许为负")
        if not 0.0 < self.price_forecast_relaxation <= 1.0:
            raise ValueError("price_forecast_relaxation 必须在 (0, 1] 内")
        if self.ev_demand_elasticity < 0 or self.demand_reference_price <= 0:
            raise ValueError("需求弹性必须非负，参考电价必须为正数")
        if self.price_demand_max_iter <= 0 or self.price_demand_tol <= 0:
            raise ValueError("price_demand_max_iter 和 price_demand_tol 必须为正")
        self.last_price = min(self.price_levels)
        self.dispatch_interval_hours = float(dispatch_interval_hours)
        if self.dispatch_interval_hours <= 0 or self.dispatch_interval_hours > 1:
            raise ValueError("dispatch_interval_hours 必须在 (0, 1] 内")
        # 默认取全站装机上限（8 桩 x 120 kW = 960 kW），不等同于 BESS PCS 800 kW。
        self.ev_service_limit_kw = (
            self.dc_config.ev_rated_kw
            if ev_service_limit_kw is None else float(ev_service_limit_kw)
        )
        # 保存基准容量，避免 SOH 缩放被重复累乘。
        self.base_capacity_kwh = float(self.soc_config.capacity_kwh)
        self.backlog_limit_kwh = self.ev_service_limit_kw * float(backlog_limit_hours)
        self.soc = DCSOCState(self.soc_config, self.soc_config.soc_min)
        self.remaining_energy_kwh = 0.0
        self.overflow_unserved_kwh = 0.0
        self.last_price = min(self.price_levels)
        self.soh = 1.0
        self.battery_state = BatteryState.new(
            total_batteries=1, eb_kwh=self.base_capacity_kwh, init_soh=[1.0]
        )

    def reset(self, soc=0.5, remaining_energy_kwh=0.0, soh=1.0):
        self.soc = DCSOCState(self.soc_config, soc)
        self.remaining_energy_kwh = max(0.0, float(remaining_energy_kwh))
        self.soh = float(soh)
        if not 0.7 <= self.soh <= 1.0:
            raise ValueError("soh 必须在 [0.7, 1.0] 内")
        self.battery_state = BatteryState.new(
            total_batteries=1, eb_kwh=self.base_capacity_kwh, init_soh=[self.soh]
        )
        self.soc.config.capacity_kwh = self.battery_state.usable_capacity_kwh
        self.overflow_unserved_kwh = 0.0

    def finalize_day(self, soc_trajectory, power_trajectory,
                     interval_hours=1.0):
        """Apply one day's group-wise aging and update effective capacity."""
        c_new = self.battery_state.group_manager.eb_kwh * 1000.0
        next_state, exact = transition_battery_state(
            self.battery_state, power_trajectory,
            [float(s) * 100.0 for s in soc_trajectory],
            c_new=c_new, interval_hours=interval_hours,
        )
        aging = evaluate_soh(
            soc_trajectory, power_trajectory, c_new=c_new,
            interval_hours=interval_hours,
        )
        self.battery_state = next_state
        self.soh = self.battery_state.soh_system
        self.soc.config.capacity_kwh = self.battery_state.usable_capacity_kwh
        return {**aging, "soh_end": self.soh,
                "replacement_cost_yuan": exact.replacement_cost_yuan,
                "aging_cost_yuan": exact.cost_yuan}

    def _build_case(self, pv, wind, ev_base, non_ev, current_ev=0.0,
                    dt_hours=1.0, current_ev_elapsed_hours=0.0,
                    soh_override=None, demand_forecast_price=None):
        h = len(ev_base)
        cap_kw = float(self.dc_config.ev_rated_kw)
        dt_current = float(dt_hours)
        if dt_current <= 0.0:
            raise ValueError("dt_hours 必须为正数")

        # Slot-zero is an interval arrival, not the unserved remainder of a
        # one-hour forecast.  This makes the two half-hour slots additive:
        # with a constant price, each receives its own price-conditioned
        # arrival energy (rate * dt), rather than re-pricing an hourly total
        # after subtracting an incompatible observed rate.  Unserved energy is
        # carried only by ``remaining_energy_kwh`` below.  ForecastBundle keeps
        # current_ev_load_kw/current_ev_elapsed_hours for other controllers;
        # DynamicDC intentionally does not use them in the slot-zero demand.
        current_ev_kw = float(ev_base[0])
        # 第一段是未来 dispatch_interval_hours；其余段是小时级预测。
        current_rigid_kw = current_ev_kw * (1.0 - self.current_flexible_fraction)
        base_load = [
            float(non_ev[i]) + (current_rigid_kw if i == 0 else 0.0)
            for i in range(h)
        ]
        intervals = [dt_current] + [1.0] * max(0, h - 1)
        values = self.response_value_levels
        demand_value = max(max(self.price_levels) + 0.01, max(values))

        users = []
        if self.current_flexible_fraction > 0 and current_ev_kw > 1e-9:
            users.append(EVUser(
                name="current_ev_flexible",
                value_per_kwh=demand_value,
                energy_max_kwh=current_ev_kw * dt_current * self.current_flexible_fraction,
                energy_min_kwh=0.0,
                available=(0,),
                max_power_kw=cap_kw,
            ))
        # 未来预测需求：逐小时用户，可在相邻小时之间小幅转移；
        # 需求价格弹性来自 value_per_kwh，若价格高于价值则不充电。
        for t in range(1, h):
            interval = intervals[t]
            energy = min(max(0.0, float(ev_base[t])) * interval, cap_kw * interval)
            if energy <= 1e-9:
                continue
            # The next-hour forecast may be served in the current remaining
            # dispatch interval.  Excluding slot 0 made the executed price
            # economically irrelevant because only the already-observed rigid
            # load could appear at the current slot.
            window = tuple(range(max(0, t - 1), min(h - 1, t + 1) + 1))
            users.append(EVUser(
                name=f"fc_{t:02d}",
                value_per_kwh=demand_value,
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
                value_per_kwh=demand_value,
                energy_max_kwh=self.remaining_energy_kwh,
                energy_min_kwh=(1.0 - self.flexibility) * self.remaining_energy_kwh,
                available=tuple(range(h)),
                max_power_kw=cap_kw,
            ))

        effective_soh = (
            (self.soh if soh_override is None else float(soh_override))
            if self.use_soh_capacity_in_game else 1.0
        )
        game_battery_state = None
        dynamic_deg_cost = 0.05
        if self.use_soh_in_game:
            if soh_override is None:
                game_battery_state = self.battery_state
            else:
                game_battery_state = BatteryState.new(
                    total_batteries=1,
                    eb_kwh=self.base_capacity_kwh,
                    init_soh=[effective_soh],
                )
            dynamic_deg_cost = marginal_cycle_cost_per_kwh(game_battery_state)
        battery = BatteryParams(
            capacity_kwh=self.base_capacity_kwh * effective_soh,
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
            deg_cost_per_kwh=dynamic_deg_cost,
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
            penalty_curt=self.curtailment_penalty,
            price_ramp_penalty=0.0,
            ev_congestion_penalty=self.ev_congestion_penalty,
            ev_service_limit_kw=self.ev_service_limit_kw,
            ev_rigid_load_kw=[current_rigid_kw] + [0.0] * (h - 1),
            ev_demand_elasticity=self.ev_demand_elasticity,
            demand_reference_price=self.demand_reference_price,
            demand_forecast_price=demand_forecast_price or self.demand_reference_price,
            battery_state=game_battery_state,
            interval_hours=intervals,
        )

    def _solve_price_demand_loop(self, pv, wind, ev, non_ev, current_ev,
                                 dt_current, current_ev_elapsed_hours,
                                 previous_soc):
        """Iterate price, price-conditioned EV load, and provisional SOH."""
        reference_forecast = [max(0.0, float(value)) for value in ev]
        forecast_price = float(self.last_price)
        prices = [forecast_price] * len(reference_forecast)
        soh_iter = float(self.soh) if self.use_soh_capacity_in_game else 1.0
        residual = float("inf")
        price_residual = float("inf")
        demand_residual = float("inf")
        soh_residual = float("inf")
        horizon = None
        case = None
        previous_response = None
        previous_forecast_residual = None
        forecast = list(reference_forecast)
        self.price_forecast_trace = []
        relaxation = self.price_forecast_relaxation
        for iteration in range(1, self.price_demand_max_iter + 1):
            forecast_price_used = forecast_price
            forecast_scale = (forecast_price / self.demand_reference_price) ** (
                -self.ev_demand_elasticity
            )
            forecast = [value * forecast_scale for value in reference_forecast]
            case = self._build_case(
                pv, wind, forecast, non_ev, current_ev, dt_current,
                current_ev_elapsed_hours, soh_override=soh_iter,
                demand_forecast_price=forecast_price,
            )
            horizon = solve_equilibrium_bri(
                case, init_prices=prices, max_sweeps=self.max_sweeps,
            )
            response_profile = [
                sum(horizon["response"][user.name][t] for user in case.users)
                / max(case.interval_hours[t], 1e-9)
                for t in range(case.T)
            ]
            storage_kw = horizon["p_dis"][0] - horizon["p_ch"][0]
            soc_trial = horizon["soc"][1]
            aging = evaluate_soh(
                [previous_soc, soc_trial], [storage_kw],
                c_new=self.base_capacity_kwh * 1000.0,
                interval_hours=dt_current,
            )
            soh_next = (
                max(0.7, self.soh - aging["delta_soh_total"])
                if self.use_soh_capacity_in_game else 1.0
            )
            demand_residual = (
                max(abs(response_profile[t] - previous_response[t])
                    for t in range(case.T))
                if previous_response is not None else float("inf")
            )
            price_residual = max(
                abs(float(horizon["prices"][t]) - prices[t])
                for t in range(case.T)
            )
            soh_residual = abs(soh_next - soh_iter)
            residual = max(price_residual, demand_residual, soh_residual)
            previous_response = response_profile
            prices = list(horizon["prices"])
            target_forecast_price = sum(prices) / len(prices)
            forecast_price_residual = target_forecast_price - forecast_price_used
            self.price_forecast_trace.append({
                "iteration": iteration,
                "forecast_price": forecast_price_used,
                "target_price": target_forecast_price,
                "fixed_point_residual": forecast_price_residual,
                "relaxation": relaxation,
                "price_residual": price_residual,
                "demand_residual_kw": demand_residual,
                "soh_residual": soh_residual,
            })
            if self.adaptive_price_forecast_relaxation and previous_forecast_residual is not None:
                sign_flip = forecast_price_residual * previous_forecast_residual < 0
                residual_grew = (
                    abs(forecast_price_residual)
                    > 1.05 * abs(previous_forecast_residual)
                )
                residual_shrank = (
                    abs(forecast_price_residual)
                    < 0.5 * abs(previous_forecast_residual)
                )
                if sign_flip or residual_grew:
                    relaxation = max(0.125, relaxation * 0.5)
                elif residual_shrank:
                    relaxation = min(1.0, relaxation * 1.25)
            previous_forecast_residual = forecast_price_residual
            forecast_price = forecast_price_used + relaxation * forecast_price_residual
            soh_iter = soh_next
            if residual <= self.price_demand_tol:
                break
        residuals = {
            "price": price_residual,
            "demand": demand_residual,
            "soh": soh_residual,
            "converged": residual <= self.price_demand_tol,
        }
        residuals["forecast_price"] = abs(forecast_price_residual)
        residuals["final_relaxation"] = relaxation
        return case, horizon, iteration, residual, soh_iter, forecast, residuals

    def step(self, bundle_or_timestamp, pv_forecast_kw: List[float] | None = None,
             wind_forecast_kw: List[float] | None = None,
             ev_base_forecast_kw: List[float] | None = None,
             non_ev_forecast_kw: List[float] | None = None,
             current_ev_load_kw: float = 0.0, dt_hours=None,
             current_ev_elapsed_hours: float = 0.0):
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
                current_ev_elapsed_hours=current_ev_elapsed_hours,
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
        dt_current = self.dispatch_interval_hours if dt_hours is None else float(dt_hours)
        (case, horizon, pd_iterations, pd_residual, provisional_soh,
         demand_forecast, residuals) = (
            self._solve_price_demand_loop(
                pv, wind, ev, non_ev, current_ev, dt_current,
                bundle.current_ev_elapsed_hours, previous_soc,
            )
        )
        # The converged first-slot demand is executed through the DC safety layer.
        flexible_kw = sum(
            horizon["response"][user.name][0] / dt_current for user in case.users
        )
        current_rigid_kw = case.base_load[0] - non_ev[0]
        requested_kw = current_rigid_kw + flexible_kw
        network = DCMicrogridPowerFlow(self.dc_config)
        flow = balance_dc_step(
            network,
            self.soc,
            pv_kw=pv[0],
            wind_kw=wind[0],
            ev_request_kw=requested_kw,
            non_ev_kw=non_ev[0],
            p_schedule_kw=horizon["p_dis"][0] - horizon["p_ch"][0],
            dt_hours=dt_current,
            ev_service_limit_kw=self.ev_service_limit_kw,
            ev_must_serve_kw=current_rigid_kw,
        )
        served = flow["ev_served_kw"]
        unserved_rigid = flow["unserved_rigid_kw"]
        rigid_ev_unserved = flow.get("ev_unserved_rigid_kw",
                                      min(unserved_rigid, current_rigid_kw))
        total_shed = flow["ev_shed_kw"] + rigid_ev_unserved
        non_ev_unserved = flow.get("non_ev_unserved_kw",
                                   max(0.0, unserved_rigid - rigid_ev_unserved))
        # 积压需求两本账：本轮实际补足的部分要扣减，当前小时新缺口要累加。
        served_flexible_kw = sum(
            horizon["response"][user.name][0] / dt_current for user in case.users
        )
        backlog_planned_kwh = float(
            horizon["response"].get("rolling_ev_backlog", [0.0])[0]
        )
        current_served_kw = max(0.0, served - min(served_flexible_kw, served))
        current_shortfall_kwh = max(
            0.0, current_rigid_kw - current_served_kw
        ) * dt_current
        backlog_served_kwh = min(
            backlog_planned_kwh,
            max(0.0, served - current_rigid_kw) * dt_current,
        )
        raw_backlog = max(
            0.0, self.remaining_energy_kwh - backlog_served_kwh
            + current_shortfall_kwh,
        )
        overflow = max(0.0, raw_backlog - self.backlog_limit_kwh)
        self.overflow_unserved_kwh += overflow
        self.remaining_energy_kwh = min(raw_backlog, self.backlog_limit_kwh)
        total_requested_kw = requested_kw
        # 刚性缺额虽仍计入 requested，但并未真正供电，必须从 served 扣除。
        total_served_kw = max(0.0, served)
        self.last_price = float(horizon["prices"][0])
        degradation = evaluate_soh([previous_soc, flow["soc"]], [flow["p_bess_kw"]],
                                   c_new=self.battery_state.group_manager.eb_kwh * 1000.0,
                                   interval_hours=dt_current)
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
            horizon_result={**horizon,
                            "price_demand_iterations": pd_iterations,
                            "price_demand_residual": pd_residual,
                            "provisional_soh": provisional_soh,
                            "demand_forecast_converged": demand_forecast,
                            "price_forecast_trace": list(self.price_forecast_trace)},
            soh=self.soh,
            soh_delta=degradation["delta_soh_total"],
            degradation_cost=degradation["cost_total"],
            pv_curtailed_kw=flow["pv_curtailed_kw"],
            wind_curtailed_kw=flow["wind_curtailed_kw"],
            overflow_unserved_kwh=overflow,
            price_demand_iterations=pd_iterations,
            price_demand_residual=pd_residual,
            unserved_rigid_kw=non_ev_unserved,
            price_residual=residuals["price"],
            demand_residual=residuals["demand"],
            soh_residual=residuals["soh"],
            price_demand_converged=residuals["converged"],
            forecast_price_residual=residuals["forecast_price"],
            price_forecast_relaxation=residuals["final_relaxation"],
        )
