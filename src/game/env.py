"""
Stage-3 组件二: 场景定价环境 (单步上下文老虎机)。

每个 episode 采样一个随机运行场景 (风光/负荷/用户参数扰动), 智能体
输出一组电价, 环境用精确跟随者响应 + 领导者最优储能调度 + 安全投影
计算净利润作为奖励, 并返回场景特征向量。

安全层保证: 无论智能体输出何种电价, 最终调度均严格可行。
"""

import copy
import logging

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

    def reward(self, prices, scenario: Scenario):
        """计算 (净利润, 是否可行, 响应)。

        流程: 跟随者响应 -> 领导者最优储能 -> 安全投影 -> 净利润。
        """
        c = scenario.case
        prices = [float(np.clip(p, min(c.price_levels), max(c.price_levels)))
                  for p in prices]
        response = solve_follower_response(c, prices, prefer=self.prefer)
        ev_profile = [sum(response[u.name][t] for u in c.users) for t in range(c.T)]
        revenue = sum(prices[t] * ev_profile[t] for t in range(c.T))
        sched = solve_leader_fixed_profile(c, ev_profile, revenue, prefer=self.prefer)

        # 安全投影 (保证可行)
        safe = project_action(c, sched["p_ch"], sched["p_dis"], response,
                              prefer=self.prefer)
        deg = c.battery.deg_cost_per_kwh * sum(
            safe["p_ch"][t] + safe["p_dis"][t] for t in range(c.T))
        profit = (revenue - deg
                  - c.penalty_shed * sum(safe["shed"])
                  - c.penalty_curt * sum(safe["curt"]))
        return profit, safe["feasible"], response
