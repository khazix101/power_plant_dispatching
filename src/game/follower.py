"""
跟随者 (EV 用户) 模型 —— 价格弹性需求下的最优充电响应。

下层问题 (每个用户独立):
    max  Σ_t (v_i − p_t) · e_{i,t}
    s.t. 0 ≤ e_{i,t} ≤ e_i^max,   t ∈ T_i     (单时段功率上限)
         Σ_t e_{i,t} ≤ E_i^max                 (当日最大期望充电量)

含义: 当 p_t < v_i 时用户愿意在该时段充电; 当 p_t ≥ v_i 时该时段不充。
该模型用于:
  1. 独立校验双层 MILP 解出的均衡响应是否为最优反应 (比较目标值);
  2. 固定电价基线下计算用户响应。
"""

import pyomo.environ as pyo

from src.game.case import GameCase
from src.game.solvers import solve


def solve_follower_response(case: GameCase, prices, prefer=None):
    """求解所有 EV 用户在给定电价下的最优充电响应。

    Args:
        case: 博弈算例。
        prices: 长度 T 的电价列表 (元/kWh)。
        prefer: 指定求解器。

    Returns:
        dict: {user_name: [e_0, ..., e_{T-1}]}，单位 kWh。
    """
    T = case.T
    assert len(prices) == T, "电价向量长度须等于 T"

    m = pyo.ConcreteModel("follower")
    m.T = pyo.RangeSet(0, T - 1)
    m.I = pyo.RangeSet(0, case.I - 1)

    def _cap(i, t):
        u = case.users[i]
        return u.max_power_kw if t in u.available else 0.0

    m.e = pyo.Var(
        m.I, m.T, domain=pyo.NonNegativeReals,
        bounds=lambda m, i, t: (0.0, _cap(i, t)),
    )
    m.energy_cap = pyo.Constraint(
        m.I,
        rule=lambda m, i: sum(m.e[i, t] for t in m.T) <= case.users[i].energy_max_kwh,
    )
    # 用户效用 = Σ (v_i − p_t) e_{i,t}
    m.obj = pyo.Objective(
        expr=sum((case.users[i].value_per_kwh - prices[t]) * m.e[i, t]
                 for i in m.I for t in m.T),
        sense=pyo.maximize,
    )

    term, _ = solve(m, prefer=prefer)
    if term != pyo.TerminationCondition.optimal:
        raise RuntimeError(f"跟随者 LP 求解失败: {term}")

    return {
        case.users[i].name: [pyo.value(m.e[i, t]) for t in range(T)]
        for i in range(case.I)
    }


def follower_utility(case: GameCase, prices, response):
    """给定响应下的用户总效用 (元)，用于校验最优性。"""
    total = 0.0
    for u in case.users:
        for t, e in enumerate(response[u.name]):
            total += (u.value_per_kwh - prices[t]) * e
    return total


def follower_cost(case: GameCase, prices, response):
    """给定响应下的用户总充电成本 (元)。"""
    total = 0.0
    for u in case.users:
        for t, e in enumerate(response[u.name]):
            total += prices[t] * e
    return total
