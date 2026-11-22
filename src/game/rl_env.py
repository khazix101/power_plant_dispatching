"""Independent sequential MDP adapter for the rolling SFSE controller.

The controller returns a safe projected plan, not a physical DC execution.
This adapter therefore reports a first-slot planned reward and labels it as
such; an external executor remains responsible for actual metering and reward.
"""

from dataclasses import dataclass
import math
from typing import Optional, Sequence

import numpy as np

from src.game.forecast import ForecastBundle
from src.game.rolling import RollingSFSEController


@dataclass(frozen=True)
class RollingMDPState:
    """Observable state at one decision boundary."""

    forecast_window: ForecastBundle
    soc: float
    soh: float
    usable_capacity_kwh: float
    reserve_kwh: float
    backlog_kwh: float
    previous_price: Optional[float]
    time_index: int


class RollingSFSEMDPEnv:
    """Sequential adapter; it does not forecast or execute a DC power flow."""

    def __init__(self, controller: RollingSFSEController, seed: Optional[int] = None):
        self.controller = controller
        self._rng = np.random.default_rng(seed)
        self._state: Optional[RollingMDPState] = None
        self._terminated = False

    @property
    def state(self) -> RollingMDPState:
        if self._state is None:
            raise RuntimeError("reset() must be called before accessing state")
        return self._state

    def reset(
        self,
        bundle_or_trace,
        *,
        seed: Optional[int] = None,
        start_index: Optional[int] = None,
        backlog_kwh: Optional[float] = None,
        previous_price: Optional[float] = None,
        time_index: int = 0,
    ) -> RollingMDPState:
        """Reset from one bundle or choose one bundle from an explicit trace.

        Trace selection is seeded and does not create or advance forecasts.
        """
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        if isinstance(bundle_or_trace, ForecastBundle):
            if start_index not in (None, 0):
                raise ValueError("单个 ForecastBundle 的 start_index 只能为 0")
            bundle = bundle_or_trace
        else:
            if isinstance(bundle_or_trace, (str, bytes)):
                raise TypeError("trace 必须是 ForecastBundle 序列")
            try:
                trace = list(bundle_or_trace)
            except TypeError as exc:
                raise TypeError("reset 需要 ForecastBundle 或其序列") from exc
            if not trace or any(not isinstance(item, ForecastBundle) for item in trace):
                raise ValueError("trace 必须是非空 ForecastBundle 序列")
            if start_index is None:
                selected = int(self._rng.integers(0, len(trace)))
            else:
                if isinstance(start_index, bool) or not isinstance(start_index, int):
                    raise TypeError("start_index 必须为整数")
                if not 0 <= start_index < len(trace):
                    raise ValueError("start_index 超出 trace 范围")
                selected = start_index
            bundle = trace[selected]

        if isinstance(time_index, bool) or not isinstance(time_index, int) or time_index < 0:
            raise ValueError("time_index 必须为非负整数")
        backlog = self._bundle_backlog(bundle, backlog_kwh, default=0.0)
        previous = self._validate_previous_price(previous_price)
        self._state = self._make_state(bundle, backlog, previous, time_index)
        self._terminated = False
        return self._state

    def step(self, price_plan, next_bundle=None, terminated=False):
        """Apply one discrete horizon plan and return ``(state, reward, done, info)``."""
        if self._state is None:
            raise RuntimeError("reset() must be called before step()")
        if self._terminated:
            raise RuntimeError("episode is terminated; call reset() before step()")
        if not isinstance(terminated, bool):
            raise TypeError("terminated 必须为 bool")
        if not terminated and not isinstance(next_bundle, ForecastBundle):
            raise ValueError("非终止转移必须显式提供 next_bundle")
        if next_bundle is not None and not isinstance(next_bundle, ForecastBundle):
            raise TypeError("next_bundle 必须为 ForecastBundle")

        plan = self._validate_plan(price_plan, self._state.forecast_window.horizon_hours)
        decision = self.controller.step_with_prices(self._state.forecast_window, plan)
        reward, ledger = self._first_slot_planned_reward(
            self._state.forecast_window, plan[0], decision
        )

        if next_bundle is not None:
            backlog = self._bundle_backlog(
                next_bundle, None, default=self._state.backlog_kwh
            )
            self._state = self._make_state(
                next_bundle, backlog, plan[0], self._state.time_index + 1
            )
        self._terminated = terminated
        info = {
            "decision": decision,
            "reward_source": "planned_safe_projection",
            "is_actual_execution_reward": False,
            "planned_reward_breakdown": ledger,
        }
        return self._state, reward, terminated, info

    def _validate_plan(self, price_plan, horizon):
        if isinstance(price_plan, (str, bytes)):
            raise TypeError("price_plan 必须是一维价格序列")
        try:
            raw = list(price_plan)
        except TypeError as exc:
            raise TypeError("price_plan 必须是一维价格序列") from exc
        if len(raw) != horizon:
            raise ValueError(f"price_plan 长度必须等于预测时域 T={horizon}")
        levels = [float(level) for level in self.controller.price_levels]
        result = []
        for value in raw:
            if isinstance(value, (bool, np.bool_)):
                raise TypeError("price_plan 价格必须是离散数值档位")
            try:
                price = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("price_plan 必须包含离散价格值") from exc
            if not math.isfinite(price):
                raise ValueError("price_plan 价格必须为有限值")
            if not any(abs(price - level) <= 1e-9 for level in levels):
                raise ValueError("price_plan 中的价格必须属于配置的 price_levels")
            result.append(price)
        return result

    def _make_state(self, bundle, backlog, previous_price, time_index):
        battery_state = self.controller.battery_state
        if battery_state is None:
            soh = float(bundle.soh_current)
            capacity = float(self.controller.battery.capacity_kwh) * soh
        else:
            soh = float(battery_state.soh_system)
            capacity = float(battery_state.usable_capacity_kwh)
        return RollingMDPState(
            forecast_window=bundle,
            soc=float(bundle.soc_current),
            soh=soh,
            usable_capacity_kwh=capacity,
            reserve_kwh=float(bundle.reserve_kwh),
            backlog_kwh=float(backlog),
            previous_price=previous_price,
            time_index=time_index,
        )

    @staticmethod
    def _bundle_backlog(bundle, explicit, default):
        value = explicit
        if value is None:
            for key in ("ev_backlog_kwh", "backlog_kwh"):
                if key in bundle.history:
                    history_value = bundle.history[key]
                    if isinstance(history_value, (list, tuple, np.ndarray)):
                        value = history_value[0] if history_value else default
                    else:
                        value = history_value
                    break
        if value is None:
            value = default
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("backlog_kwh 必须为有限非负数") from exc
        if not math.isfinite(result) or result < 0:
            raise ValueError("backlog_kwh 必须为有限非负数")
        return result

    def _validate_previous_price(self, price):
        if price is None:
            return None
        levels = [float(level) for level in self.controller.price_levels]
        if isinstance(price, (bool, np.bool_)):
            raise TypeError("previous_price 必须属于配置的 price_levels")
        try:
            value = float(price)
        except (TypeError, ValueError) as exc:
            raise ValueError("previous_price 必须属于配置的 price_levels") from exc
        if not math.isfinite(value) or not any(abs(value - x) <= 1e-9 for x in levels):
            raise ValueError("previous_price 必须属于配置的 price_levels")
        return value

    def _first_slot_planned_reward(self, bundle, price, decision):
        """Build a first-slot proxy from the controller's safe planned action."""
        case = self.controller._build_case(bundle)
        result = decision.horizon_result
        safe = result["safe"]
        dt = float(case.interval_hours[0])
        requested_kwh = sum(float(profile[0]) for profile in result["response"].values())
        served_kwh = sum(float(profile[0]) for profile in safe["ev"].values())
        unserved_kwh = max(0.0, requested_kwh - served_kwh)
        revenue = price * served_kwh
        degradation = case.effective_deg_cost_per_kwh * (
            float(safe["p_ch"][0]) + float(safe["p_dis"][0])
        ) * dt
        shed = case.penalty_shed * float(safe["shed"][0]) * dt
        curt = case.penalty_curt * float(safe["curt"][0]) * dt
        ev_unserved = case.penalty_shed * unserved_kwh
        previous = self._state.previous_price
        ramp = (case.price_ramp_penalty * (price - previous) ** 2
                if previous is not None else 0.0)
        congestion = 0.0
        if case.ev_congestion_penalty > 0 and case.ev_service_limit_kw:
            served_kw = served_kwh / dt
            congestion = case.ev_congestion_penalty * (
                max(0.0, served_kw) / float(case.ev_service_limit_kw)
            ) ** 2 * dt
        parts = {
            "revenue_yuan": float(revenue),
            "degradation_cost_yuan": float(degradation),
            "shed_cost_yuan": float(shed),
            "curtailment_cost_yuan": float(curt),
            "ev_unserved_cost_yuan": float(ev_unserved),
            "price_ramp_cost_yuan": float(ramp),
            "congestion_cost_yuan": float(congestion),
            "ev_requested_kwh": float(requested_kwh),
            "ev_served_kwh": float(served_kwh),
            "ev_unserved_kwh": float(unserved_kwh),
            "feasible": bool(safe["feasible"]),
            "reward_source": "planned_safe_projection",
            "is_actual_execution_reward": False,
        }
        reward = (revenue - degradation - shed - curt - ev_unserved - ramp - congestion)
        return float(reward), parts
