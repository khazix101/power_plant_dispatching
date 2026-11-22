"""
Stage-3 组件二: 场景定价环境 (单步上下文老虎机)。

每个 episode 采样一个随机运行场景 (风光/负荷/用户参数扰动), 智能体
输出一组电价, 环境用精确跟随者响应 + 领导者最优储能调度 + 安全投影
计算净利润作为奖励, 并返回场景特征向量。

安全层保证: 无论智能体输出何种电价, 最终调度均严格可行。
"""

import copy
import logging
from dataclasses import dataclass

import numpy as np

from src.game.baseline import solve_leader_fixed_profile
from src.game.case import GameCase
from src.game.follower import solve_follower_response
from src.game.safety import project_action

logger = logging.getLogger(__name__)


class Scenario:
    """一个运行场景: 扰动后的算例 + 特征向量。"""

    def __init__(self, case: GameCase, features: np.ndarray):
        self.case = case
        self.features = features


@dataclass
class RewardBreakdown:
    """Executed-action reward ledger, with all monetary fields in yuan."""

    profit: float
    revenue_yuan: float
    degradation_cost_yuan: float
    shed_cost_yuan: float
    curtailment_cost_yuan: float
    ev_unserved_cost_yuan: float
    price_ramp_cost_yuan: float
    congestion_cost_yuan: float
    ev_requested_kwh: float
    ev_served_kwh: float
    ev_unserved_kwh: float
    feasible: bool
    response: dict
    safe_action: dict


class PricingEnv:
    """定价环境。"""

    # 特征归一化尺度
    _SCALE_PV = 1000.0
    _SCALE_LOAD = 500.0

    def __init__(self, case: GameCase, prefer=None, use_surrogate=None):
        self.base = case
        self.prefer = prefer
        self.use_surrogate = use_surrogate
        self.n_features = 3 * case.T + 3 * case.I

    def sample_scenario(self, rng: np.random.Generator,
                        extreme: bool = False) -> Scenario:
        c = copy.deepcopy(self.base)
        if extreme:
            # 极端天气: 新能源骤降 + 负荷抬升
            s_re = rng.uniform(0.25, 0.5)
            s_load = rng.uniform(1.15, 1.35)
        else:
            s_re = rng.uniform(0.8, 1.2)
            s_load = rng.uniform(0.9, 1.15)
        c.pv = [v * s_re * rng.uniform(0.9, 1.1) for v in self.base.pv]
        c.wind = [v * s_re * rng.uniform(0.9, 1.1) for v in self.base.wind]
        c.base_load = [v * s_load for v in self.base.base_load]
        for u, u0 in zip(c.users, self.base.users):
            u.value_per_kwh = max(0.1, u0.value_per_kwh + rng.uniform(-0.15, 0.15))
        feat = self._features(c)
        return Scenario(c, feat)

    def _features(self, c: GameCase) -> np.ndarray:
        parts = [
            np.array(c.pv) / self._SCALE_PV,
            np.array(c.wind) / self._SCALE_PV,
            np.array(c.base_load) / self._SCALE_LOAD,
            np.array([u.value_per_kwh for u in c.users]),
            np.array([u.energy_max_kwh for u in c.users]) / 200.0,
            np.array([u.max_power_kw for u in c.users]) / 100.0,
        ]
        return np.concatenate(parts).astype(np.float32)

    def evaluate_action(self, prices, scenario: Scenario) -> RewardBreakdown:
        """Evaluate one price vector against the executed safe action.

        流程: 跟随者响应 -> 领导者最优储能 -> 安全投影 -> 净利润。
        """
        c = scenario.case
        prices = [float(np.clip(p, min(c.price_levels), max(c.price_levels)))
                  for p in prices]
        response = solve_follower_response(c, prices, prefer=self.prefer)
        ev_profile = [sum(response[u.name][t] for u in c.users) for t in range(c.T)]
        requested_revenue = sum(prices[t] * ev_profile[t] for t in range(c.T))
        sched = solve_leader_fixed_profile(c, ev_profile, requested_revenue,
                                           prefer=self.prefer, prices=prices)

        # 安全投影 (保证可行)
        safe = project_action(c, sched["p_ch"], sched["p_dis"], response,
                              prefer=self.prefer)
        dt = c.interval_hours
        served_by_interval = [
            sum(safe["ev"][u.name][t] for u in c.users)
            for t in range(c.T)
        ]
        requested_by_interval = ev_profile
        revenue = sum(prices[t] * served_by_interval[t] for t in range(c.T))
        requested_kwh = sum(requested_by_interval)
        served_kwh = sum(served_by_interval)
        unserved_kwh = sum(
            max(0.0, requested_by_interval[t] - served_by_interval[t])
            for t in range(c.T)
        )
        degradation_cost = c.effective_deg_cost_per_kwh * sum(
            (safe["p_ch"][t] + safe["p_dis"][t]) * dt[t]
            for t in range(c.T)
        )
        shed_cost = c.penalty_shed * sum(
            safe["shed"][t] * dt[t] for t in range(c.T)
        )
        curtailment_cost = c.penalty_curt * sum(
            safe["curt"][t] * dt[t] for t in range(c.T)
        )
        ev_unserved_cost = c.penalty_shed * unserved_kwh
        ramp_cost = c.price_ramp_penalty * sum(
            (prices[t] - prices[t - 1]) ** 2 for t in range(1, c.T)
        )
        congestion_cost = 0.0
        if c.ev_congestion_penalty > 0 and c.ev_service_limit_kw:
            limit = float(c.ev_service_limit_kw)
            congestion_cost = c.ev_congestion_penalty * sum(
                (max(0.0, served_by_interval[t] / dt[t]) / limit) ** 2 * dt[t]
                for t in range(c.T)
            )
        profit = (revenue - degradation_cost - shed_cost - curtailment_cost
                  - ev_unserved_cost - ramp_cost - congestion_cost)
        return RewardBreakdown(
            profit=float(profit),
            revenue_yuan=float(revenue),
            degradation_cost_yuan=float(degradation_cost),
            shed_cost_yuan=float(shed_cost),
            curtailment_cost_yuan=float(curtailment_cost),
            ev_unserved_cost_yuan=float(ev_unserved_cost),
            price_ramp_cost_yuan=float(ramp_cost),
            congestion_cost_yuan=float(congestion_cost),
            ev_requested_kwh=float(requested_kwh),
            ev_served_kwh=float(served_kwh),
            ev_unserved_kwh=float(unserved_kwh),
            feasible=bool(safe["feasible"]),
            response=response,
            safe_action=safe,
        )

    def reward(self, prices, scenario: Scenario):
        """Backward-compatible ``(profit, feasible, response)`` wrapper."""
        result = self.evaluate_action(prices, scenario)
        return result.profit, result.feasible, result.response
