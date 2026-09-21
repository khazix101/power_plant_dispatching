"""
安全可行主从博弈均衡 (SFSE) —— 下层 KKT + 大 M 线性化 → 单层 MILP。

数学结构
--------
领导者 (微网运营商):
    max  Σ_t Σ_i p_t e_{i,t}
         − c_deg Σ_t (P_ch,t + P_dis,t) Δt
         − α Σ_t shed_t Δt − β Σ_t curt_t Δt
    s.t. 离网功率平衡、SOC 动态、充放电互斥、终端 SOC、电价档位选择

跟随者 (EV 用户 i, 价格弹性):
    max  Σ_t (v_i − p_t) e_{i,t}
    s.t. Σ_t e_{i,t} ≤ E_i^max,  0 ≤ e_{i,t} ≤ e_i^max

求解: 用跟随者 KKT 条件替换下层 → 含互补约束的单层问题,
      再用 0-1 变量 + 大 M 线性化; 目标中的 p_t·e_{i,t} 双线性项
      通过电价离散化 (档位 z_{t,k}) 精确线性化为 Σ_k P_k e_{i,t,k}。
      最终得到可被 Gurobi / HiGHS 求解的 MILP。

注: 该模型对应"乐观型 (optimistic) 双层优化"——当跟随者最优反应
    不唯一时, 允许领导者选择对其最有利的最优反应。这符合运营商可
    通过充电桩分配引导用户的实际情形, 也是双层优化的标准处理。
"""

import logging

import pyomo.environ as pyo

from src.game.case import GameCase
from src.game.solvers import solve

logger = logging.getLogger(__name__)

# 互补条件大 M 上界
BIG_M = 10.0
# 能量上限互补需用 ≥ 最大需求电量的 M, 否则会误限未饱和用户
BIG_M_ENERGY = 1e4


def solve_equilibrium(case: GameCase, prefer=None, tee=False, fixed_prices=None,
                      time_limit=None):
    """求解安全可行主从博弈均衡。

    Args:
        case: 博弈算例。
        prefer: 指定求解器 ('gurobi' / 'appsi_highs' / ...)。
        tee: 是否打印求解器日志。
        fixed_prices: 若给定, 固定电价为该向量 (用于评估给定电价的均衡响应)。

    Returns:
        dict: 均衡解, 含 profit/prices/response/soc/bess/shed/curt 等。
    """
    T, I = case.T, case.I
    bat = case.battery
    dt = 1.0

    def cap(i, t):
        u = case.users[i]
        return u.max_power_kw if t in u.available else 0.0

    m = pyo.ConcreteModel("sfse_equilibrium")
    m.T = pyo.RangeSet(0, T - 1)
    m.I = pyo.RangeSet(0, I - 1)
    m.K = pyo.RangeSet(0, len(case.price_levels) - 1)

    # ── 领导者变量 ──────────────────────────────────────
    m.z = pyo.Var(m.T, m.K, domain=pyo.Binary)
    if fixed_prices is not None:
        for t in range(T):
            for k in range(len(case.price_levels)):
                if abs(case.price_levels[k] - fixed_prices[t]) < 1e-9:
                    m.z[t, k].fix(1)
                else:
                    m.z[t, k].fix(0)
    m.Pch = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.Pdis = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.uch = pyo.Var(m.T, domain=pyo.Binary)
    m.shed = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.curt = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.soc = pyo.Var(pyo.RangeSet(0, T), domain=pyo.NonNegativeReals)

    # ── 跟随者变量 ──────────────────────────────────────
    m.e = pyo.Var(m.I, m.T, domain=pyo.NonNegativeReals)
    m.eik = pyo.Var(m.I, m.T, m.K, domain=pyo.NonNegativeReals)
    m.lam = pyo.Var(m.I, domain=pyo.NonNegativeReals)   # 总量约束对偶 γ_i
    m.mu = pyo.Var(m.I, m.T, domain=pyo.NonNegativeReals)   # 下界对偶
    m.nu = pyo.Var(m.I, m.T, domain=pyo.NonNegativeReals)   # 上界对偶
    m.a = pyo.Var(m.I, m.T, domain=pyo.Binary)              # e=0 指示
    m.b = pyo.Var(m.I, m.T, domain=pyo.Binary)              # e=e_max 指示
    m.c = pyo.Var(m.I, domain=pyo.Binary)                   # 总量饱和指示

    # ── 领导者约束 ──────────────────────────────────────
    m.price_select = pyo.Constraint(
        m.T, rule=lambda m, t: sum(m.z[t, k] for k in m.K) == 1
    )
    m.soc_init = pyo.Constraint(expr=m.soc[0] == bat.soc0)
    m.soc_dyn = pyo.Constraint(
        m.T,
        rule=lambda m, t: m.soc[t + 1]
        == m.soc[t] + (bat.charge_eff * m.Pch[t]
                       - m.Pdis[t] / bat.discharge_eff) * dt / bat.capacity_kwh,
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
            == case.base_load[t] + sum(m.e[i, t] for i in m.I)
            + m.Pch[t] + m.curt[t]
        ),
    )

    # ── 跟随者约束 (KKT) ────────────────────────────────
    # 单时段功率上限 (不可用时段 cap=0)
    m.e_ub = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: m.e[i, t] <= cap(i, t)
    )
    # 总量上限 Σ e ≤ E_max
    m.energy_cap = pyo.Constraint(
        m.I,
        rule=lambda m, i: sum(m.e[i, t] for t in m.T) <= case.users[i].energy_max_kwh,
    )
    # 平稳性: (p_t − v_i) − μ_{i,t} + ν_{i,t} + γ_i = 0
    def _stationarity(m, i, t):
        p_t = sum(case.price_levels[k] * m.z[t, k] for k in m.K)
        return p_t - case.users[i].value_per_kwh - m.mu[i, t] + m.nu[i, t] + m.lam[i] == 0
    m.stationarity = pyo.Constraint(m.I, m.T, rule=_stationarity)

    # 互补松弛 (大 M)
    m.comp_lo_1 = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: m.e[i, t] <= cap(i, t) * (1 - m.a[i, t])
    )
    m.comp_lo_2 = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: m.mu[i, t] <= BIG_M * m.a[i, t]
    )
    m.comp_hi_1 = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: m.e[i, t] >= cap(i, t) * m.b[i, t]
    )
    m.comp_hi_2 = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: m.nu[i, t] <= BIG_M * m.b[i, t]
    )
    m.comp_ab = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: m.a[i, t] + m.b[i, t] <= 1
    )
    # 总量饱和互补: γ_i (E_max − Σe) = 0
    m.comp_g1 = pyo.Constraint(
        m.I,
        rule=lambda m, i: sum(m.e[i, t] for t in m.T)
        >= case.users[i].energy_max_kwh - BIG_M_ENERGY * (1 - m.c[i]),
    )
    m.comp_g2 = pyo.Constraint(
        m.I, rule=lambda m, i: m.lam[i] <= BIG_M * m.c[i]
    )

    # ── 目标双线性项线性化 ──────────────────────────────
    def _eik_lb(m, i, t, k):
        return m.eik[i, t, k] >= m.e[i, t] - cap(i, t) * (1 - m.z[t, k])
    def _eik_ub_z(m, i, t, k):
        return m.eik[i, t, k] <= cap(i, t) * m.z[t, k]
    m.eik_lb = pyo.Constraint(m.I, m.T, m.K, rule=_eik_lb)
    m.eik_ub_z = pyo.Constraint(m.I, m.T, m.K, rule=_eik_ub_z)
    m.eik_ub_e = pyo.Constraint(
        m.I, m.T, m.K, rule=lambda m, i, t, k: m.eik[i, t, k] <= m.e[i, t]
    )
    m.eik_sum = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: sum(m.eik[i, t, k] for k in m.K) == m.e[i, t]
    )

    # ── 目标函数 ────────────────────────────────────────
    revenue = sum(case.price_levels[k] * m.eik[i, t, k]
                  for i in m.I for t in m.T for k in m.K)
    deg = bat.deg_cost_per_kwh * sum((m.Pch[t] + m.Pdis[t]) * dt for t in m.T)
    shed_cost = case.penalty_shed * sum(m.shed[t] * dt for t in m.T)
    curt_cost = case.penalty_curt * sum(m.curt[t] * dt for t in m.T)
    m.obj = pyo.Objective(expr=revenue - deg - shed_cost - curt_cost,
                          sense=pyo.maximize)

    term, solver_name = solve(m, prefer=prefer, tee=tee, time_limit=time_limit)
    if term != pyo.TerminationCondition.optimal:
        raise RuntimeError(f"均衡 MILP 求解未达最优: {term}")

    prices = [
        sum(case.price_levels[k] * pyo.value(m.z[t, k])
            for k in range(len(case.price_levels)))
        for t in range(T)
    ]
    response = {
        case.users[i].name: [pyo.value(m.e[i, t]) for t in range(T)]
        for i in range(I)
    }
    result = {
        "termination": str(term),
        "solver": solver_name,
        "profit": pyo.value(m.obj),
        "prices": prices,
        "response": response,
        "soc": [pyo.value(m.soc[t]) for t in range(T + 1)],
        "p_ch": [pyo.value(m.Pch[t]) for t in range(T)],
        "p_dis": [pyo.value(m.Pdis[t]) for t in range(T)],
        "shed": [pyo.value(m.shed[t]) for t in range(T)],
        "curt": [pyo.value(m.curt[t]) for t in range(T)],
    }
    logger.info("均衡求解完成: profit=%.2f, solver=%s", result["profit"], solver_name)
    return result
