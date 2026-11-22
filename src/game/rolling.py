"""滚动预测驱动的安全可行主从博弈控制器。

每次调用 ``step`` 都重新构造未来窗口的博弈问题，只执行当前时刻
的价格、EV 负荷和储能动作。下一次调用用新的 ForecastBundle 重新求解。

该模块故意不依赖具体预测模型，便于对接其他成员负责的预测接口。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from src.game.case import BatteryParams, EVUser, GameCase
from src.game.equilibrium import solve_equilibrium
from src.game.forecast import ForecastBundle
from src.game.bri import solve_equilibrium_bri
from src.game.safety import project_action
from src.game.follower import solve_follower_response
from src.game.baseline import solve_leader_fixed_profile
from src.game.dc_microgrid import DCMicrogridPowerFlow, DCSOCState, balance_dc_step
from src.battery.state import BatteryState
from src.battery.degradation import marginal_cycle_cost_per_kwh
from src.game.state_transition import transition_battery_state


@dataclass
class RollingDecision:
    """滚动控制器一次决策的结果。"""

    timestamp: object
    price: float
    storage_kw: float
    ev_load_kw: float
    total_load_kw: float
    feasible: bool
    method: str
    horizon_result: Dict
    soh_current: float = 1.0
    usable_capacity_kwh: float = 0.0


class RollingSFSEController:
    """预测驱动的滚动 SFSE 控制器。

    ``base_ev_load_forecast_kw`` 表示未来基础充电需求总量。控制器将
    每个预测时段转成一个可移峰的用户群：总期望充电量保持不变，
    价格决定这些需求在窗口内的分配。这样基础预测与价格响应不会重复。
    """

    def __init__(
        self,
        battery: Optional[BatteryParams] = None,
        price_levels: Optional[List[float]] = None,
        response_value_per_kwh: float = 1.55,
        response_power_factor: float = 1.0,
        reserve_penalty: float = 0.2,
        flexibility: float = 0.2,
        method: str = "bri",
        max_sweeps: int = 3,
        battery_state: Optional[BatteryState] = None,
    ):
        self.battery = battery or BatteryParams()
        self.price_levels = price_levels or [1.5, 1.7, 1.9, 2.1, 2.3, 2.5]
        self.response_value_per_kwh = response_value_per_kwh
        self.response_power_factor = response_power_factor
        self.reserve_penalty = reserve_penalty
        if not 0.0 <= flexibility < 1.0:
            raise ValueError("flexibility 必须在 [0, 1) 内")
        self.flexibility = flexibility
        self.method = method
        self.max_sweeps = max_sweeps
        self.battery_state = battery_state

    def _build_users(self, bundle: ForecastBundle) -> List[EVUser]:
        """把基础预测需求转为可移峰的用户群。"""
        h = bundle.horizon_hours
        users = []
        # 当前已实现负荷不再进入价格响应，避免控制器把已发生的充电推迟到未来。
        forecast_load = list(bundle.base_ev_load_forecast_kw)
        forecast_load[0] = max(0.0, forecast_load[0] - bundle.current_ev_load_kw)
        for t, load_kw in enumerate(forecast_load):
            energy = max(0.0, load_kw * self.response_power_factor)
            if energy <= 1e-8:
                continue
            users.append(
                EVUser(
                    name=f"forecast_{t:02d}",
                    value_per_kwh=self.response_value_per_kwh,
                    energy_max_kwh=energy,
                    energy_min_kwh=energy * (1.0 - self.flexibility),
                    available=tuple(range(h)),
                    max_power_kw=max(energy, 1.0),
                )
            )
        return users

    def _build_case(self, bundle: ForecastBundle) -> GameCase:
        dynamic_capacity = None
        if self.battery_state is not None:
            dynamic_capacity = self.battery_state.usable_capacity_kwh
            dynamic_deg_cost = marginal_cycle_cost_per_kwh(self.battery_state)
        else:
            dynamic_capacity = self.battery.capacity_kwh * bundle.soh_current
            dynamic_deg_cost = self.battery.deg_cost_per_kwh
        battery = BatteryParams(
            capacity_kwh=dynamic_capacity,
            power_kw=self.battery.power_kw,
            charge_eff=self.battery.charge_eff,
            discharge_eff=self.battery.discharge_eff,
            soc0=bundle.soc_current,
            soc_min=self.battery.soc_min,
            soc_max=self.battery.soc_max,
            soc_end_min=max(
                self.battery.soc_end_min,
                bundle.reserve_kwh / max(dynamic_capacity, 1e-9),
            ),
            deg_cost_per_kwh=dynamic_deg_cost,
        )
        return GameCase(
            T=bundle.horizon_hours,
            pv=bundle.pv_forecast_kw,
            wind=bundle.wind_forecast_kw,
            base_load=[
                bundle.non_ev_load_forecast_kw[0] + bundle.current_ev_load_kw,
                *bundle.non_ev_load_forecast_kw[1:],
            ],
            users=self._build_users(bundle),
            battery=battery,
            price_levels=list(self.price_levels),
            penalty_shed=100.0,
            penalty_curt=0.2,
            battery_state=self.battery_state,
        )

    def step(self, bundle: ForecastBundle) -> RollingDecision:
        """滚动求解并返回当前时刻应执行的决策。"""
        case = self._build_case(bundle)
        if not case.users:
            raise ValueError("预测窗口内没有基础充电需求，无法构造用户跟随者")

        if self.method == "milp":
            result = solve_equilibrium(case)
        elif self.method == "bri":
            result = solve_equilibrium_bri(
                case,
                init_prices=[min(self.price_levels)] * case.T,
                max_sweeps=self.max_sweeps,
            )
        else:
            raise ValueError(f"不支持的滚动求解方法: {self.method}")

        safe = project_action(
            case,
            result["p_ch"],
            result["p_dis"],
            result["response"],
        )
        return self._decision_from_result(bundle, case, result, safe,
                                          method=self.method)

    def step_with_prices(self, bundle: ForecastBundle, price_plan) -> RollingDecision:
        """Execute a legal horizon price plan through the shared safe pipeline.

        The plan has one discrete configured price per forecast interval. Only
        the first interval is returned for execution, matching ``step``.
        """
        case = self._build_case(bundle)
        if not case.users:
            raise ValueError("预测窗口内没有基础充电需求，无法构造用户跟随者")
        prices = self._validate_price_plan(price_plan, case.T)
        response = solve_follower_response(case, prices)
        ev_profile = [
            sum(response[user.name][t] for user in case.users)
            for t in range(case.T)
        ]
        revenue = sum(prices[t] * ev_profile[t] for t in range(case.T))
        schedule = solve_leader_fixed_profile(case, ev_profile, revenue,
                                               prices=prices)
        result = {
            **schedule,
            "prices": prices,
            "response": response,
            "method": "policy_price_plan",
        }
        safe = project_action(
            case,
            result["p_ch"],
            result["p_dis"],
            result["response"],
        )
        return self._decision_from_result(bundle, case, result, safe,
                                          method="policy_price_plan")

    def _validate_price_plan(self, price_plan, horizon):
        try:
            prices = [float(price) for price in price_plan]
        except (TypeError, ValueError) as exc:
            raise ValueError("price_plan 必须是价格档位序列") from exc
        if len(prices) != horizon:
            raise ValueError(f"price_plan 长度必须等于预测时域 T={horizon}")
        levels = [float(level) for level in self.price_levels]
        if any(not any(abs(price - level) <= 1e-9 for level in levels)
               for price in prices):
            raise ValueError("price_plan 中的价格必须属于配置的 price_levels")
        return prices

    def _decision_from_result(self, bundle, case, result, safe, method):
        flexible_current_ev_kwh = sum(
            safe["ev"][user.name][0]
            for user in case.users
        )
        flexible_current_ev_kw = (
            flexible_current_ev_kwh / case.interval_hours[0]
        )
        current_ev = bundle.current_ev_load_kw + flexible_current_ev_kw
        current_total = bundle.non_ev_load_forecast_kw[0] + current_ev
        current_storage = safe["p_dis"][0] - safe["p_ch"][0]
        price = result["prices"][0]
        return RollingDecision(
            timestamp=bundle.timestamp,
            price=price,
            storage_kw=current_storage,
            ev_load_kw=current_ev,
            total_load_kw=current_total,
            feasible=safe["feasible"],
            method=method,
            horizon_result={**result, "safe": safe},
            soh_current=(self.battery_state.soh_system
                         if self.battery_state is not None else bundle.soh_current),
            usable_capacity_kwh=case.effective_capacity_kwh,
        )

    def advance_battery_day(self, storage_power_kw, soc_pct):
        """Apply a completed daily trajectory to the attached SOH state."""
        if self.battery_state is None:
            raise RuntimeError("advance_battery_day 需要在控制器中传入 battery_state")
        self.battery_state, result = transition_battery_state(
            self.battery_state, storage_power_kw, soc_pct)
        return result

    def step_dc(self, bundle: ForecastBundle, power_flow=None, soc_state=None,
                dt_hours: float = 1.0):
        """Execute the rolling game decision on the DC network.

        This keeps the game solver and physical DC execution as separate
        stages while exposing one connected call for experiments.
        """
        decision = self.step(bundle)
        network = power_flow or DCMicrogridPowerFlow()
        soc = soc_state or DCSOCState(soc=bundle.soc_current)
        flow = balance_dc_step(
            network,
            soc,
            pv_kw=bundle.pv_forecast_kw[0],
            wind_kw=bundle.wind_forecast_kw[0],
            ev_request_kw=decision.ev_load_kw,
            non_ev_kw=bundle.non_ev_load_forecast_kw[0],
            p_schedule_kw=decision.storage_kw,
            dt_hours=dt_hours,
        )
        return decision, flow
