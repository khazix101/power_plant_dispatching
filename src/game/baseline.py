"""
基线方法 —— 固定电价 (无博弈) 下的调度与收益。

流程:
    1. 给定固定电价 p̄，求解跟随者最优响应 e(p̄)；
    2. 领导者在该固定负荷曲线下优化储能/失负荷/弃电 (仅成本最小化)。

该基线用于与 SFSE 均衡对比，量化"定价博弈"带来的增益。
"""

import logging

import pyomo.environ as pyo

from src.game.case import GameCase
from src.game.follower import solve_follower_response
from src.game.solvers import solve

logger = logging.getLogger(__name__)


def solve_leader_fixed_profile(case: GameCase, ev_profile, revenue_yuan,
                               prefer=None, prices=None):
    """给定 EV 充电负荷曲线，领导者优化储能与可靠性决策。

    Args:
        case: 博弈算例。
        ev_profile: 长度 T 的 EV 总充电负荷 (kWh)。
        revenue_yuan: Legacy requested-energy revenue (元), used only when prices is absent.
        prices: Interval EV tariffs (元/kWh). When supplied, the objective
            charges only EV energy physically served after EV shedding.
        prefer: 求解器。

    Returns:
        dict: profit/soc/p_ch/p_dis/shed/curt。
    """
    T = case.T
    bat = case.battery
    capacity_kwh = case.effective_capacity_kwh
    dt = case.interval_hours

    m = pyo.ConcreteModel("leader_fixed")
    m.T = pyo.RangeSet(0, T - 1)
    m.Pch = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.Pdis = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.uch = pyo.Var(m.T, domain=pyo.Binary)
    m.ev_shed = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.non_ev_shed = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.shed = pyo.Expression(m.T, rule=lambda m, t: m.ev_shed[t] + m.non_ev_shed[t])
    m.curt = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.soc = pyo.Var(pyo.RangeSet(0, T), domain=pyo.NonNegativeReals)

    m.soc_init = pyo.Constraint(expr=m.soc[0] == bat.soc0)
    m.soc_dyn = pyo.Constraint(
        m.T,
        rule=lambda m, t: m.soc[t + 1]
        == m.soc[t] + (bat.charge_eff * m.Pch[t]
                       - m.Pdis[t] / bat.discharge_eff) * dt[t] / capacity_kwh,
    )
    m.S = pyo.RangeSet(0, T)
    m.soc_lo = pyo.Constraint(m.S, rule=lambda m, t: m.soc[t] >= bat.soc_min)
    m.soc_hi = pyo.Constraint(m.S, rule=lambda m, t: m.soc[t] <= bat.soc_max)
    m.soc_end = pyo.Constraint(expr=m.soc[T] >= bat.soc_end_min)
    m.pch_lim = pyo.Constraint(
        m.T, rule=lambda m, t: m.Pch[t] <= bat.power_kw * m.uch[t]
    )
    m.pdis_lim = pyo.Constraint(
        m.T, rule=lambda m, t: m.Pdis[t] <= bat.power_kw * (1 - m.uch[t])
    )
    m.balance = pyo.Constraint(
        m.T,
        rule=lambda m, t: (
            case.pv[t] + case.wind[t] + m.Pdis[t] + m.shed[t]
            == case.base_load[t] + ev_profile[t] / dt[t] + m.Pch[t] + m.curt[t]
        ),
    )
    m.ev_shed_limit = pyo.Constraint(
        m.T, rule=lambda m, t: m.ev_shed[t] <= ev_profile[t] / dt[t]
    )
    m.non_ev_shed_limit = pyo.Constraint(
        m.T, rule=lambda m, t: m.non_ev_shed[t] <= case.base_load[t]
    )

    if prices is not None:
        if len(prices) != T:
            raise ValueError("prices length must equal T")
        revenue = sum(prices[t] * (ev_profile[t] - m.ev_shed[t] * dt[t])
                      for t in m.T)
    else:
        revenue = revenue_yuan

    deg = case.effective_deg_cost_per_kwh * sum(
        (m.Pch[t] + m.Pdis[t]) * dt[t] for t in m.T)
    # Preserve non-EV supply priority in the islanded planning layer. This
    # explicit reliability weight is an assumption, shared by all arms.
    non_ev_penalty = max(1000.0, 10.0 * case.penalty_shed)
    shed_cost = sum((case.penalty_shed * m.ev_shed[t]
                     + non_ev_penalty * m.non_ev_shed[t]) * dt[t]
                    for t in m.T)
    curt_cost = case.penalty_curt * sum(m.curt[t] * dt[t] for t in m.T)
    m.obj = pyo.Objective(expr=revenue - deg - shed_cost - curt_cost,
                          sense=pyo.maximize)

    term, solver_name = solve(m, prefer=prefer)
    if term != pyo.TerminationCondition.optimal:
        raise RuntimeError(f"基线调度求解失败: {term}")

    return {
        "termination": str(term),
        "solver": solver_name,
        "profit": pyo.value(m.obj),
        "revenue_yuan": pyo.value(revenue),
        "soc": [pyo.value(m.soc[t]) for t in range(T + 1)],
        "p_ch": [pyo.value(m.Pch[t]) for t in range(T)],
        "p_dis": [pyo.value(m.Pdis[t]) for t in range(T)],
        "shed": [pyo.value(m.shed[t]) for t in range(T)],
        "ev_shed": [pyo.value(m.ev_shed[t]) for t in range(T)],
        "non_ev_shed": [pyo.value(m.non_ev_shed[t]) for t in range(T)],
        "curt": [pyo.value(m.curt[t]) for t in range(T)],
    }


def run_fixed_price_baseline(case: GameCase, fixed_price=1.2, prefer=None):
    """固定电价基线: 用户响应 + 领导者最优调度。

    Args:
        case: 博弈算例。
        fixed_price: 固定电价 (元/kWh)。
        prefer: 求解器。

    Returns:
        dict: prices/response/profit/...
    """
    T = case.T
    prices = [fixed_price] * T
    response = solve_follower_response(case, prices, prefer=prefer)
    ev_profile = [sum(response[u.name][t] for u in case.users) for t in range(T)]
    revenue = sum(fixed_price * ev_profile[t] for t in range(T))
    result = solve_leader_fixed_profile(case, ev_profile, revenue,
                                        prefer=prefer, prices=prices)
    result.update({"prices": prices, "response": response})
    logger.info("固定电价基线完成: profit=%.2f", result["profit"])
    return result
