"""
结构化安全层 —— 将任意（可能不可行的）调度动作投影到离网可行集。

可行集 𝒢 由离网物理约束定义 (凸集):
    功率平衡:  pv_t + wind_t + Pdis_t + shed_t
               = base_t + Σ_i ev_{i,t} + Pch_t + curt_t
    SOC 动态:  soc_{t+1} = soc_t + (η_ch Pch_t − Pdis_t/η_dis) Δt / cap
    SOC 界:    soc_min ≤ soc_t ≤ soc_max,  soc_T ≥ soc_end_min
    功率界:    0 ≤ Pch_t, Pdis_t ≤ P_max
    EV 界:     0 ≤ ev_{i,t} ≤ ev_req_{i,t}   (安全层可削减 EV 充电)
    松弛量:    shed_t, curt_t ≥ 0

投影算子:
    Π_𝒢(a) = argmin_{z∈𝒢}  ‖z − a‖₁ + 惩罚项(shed, curt, EV 削减)
其中 a 为请求动作。该投影保证输出严格可行 (命题 3)。
"""

import logging

import pyomo.environ as pyo

from src.game.case import GameCase
from src.game.solvers import solve

logger = logging.getLogger(__name__)

W_SHED = 100.0   # 失负荷惩罚权重
W_CURT = 1.0     # 弃电惩罚权重
W_EV = 50.0      # 削减 EV 充电的惩罚权重
W_THRU = 1e-3    # 同时充放电的微惩罚, 保证解的唯一性倾向


def project_action(case: GameCase, pch_req, pdis_req, ev_req, prefer=None):
    """将请求动作投影到离网可行集。

    Args:
        case: 博弈算例。
        pch_req: 长度 T 的储能充电请求 (kW)。
        pdis_req: 长度 T 的储能放电请求 (kW)。
        ev_req: dict {user_name: [e_t]} 请求的 EV 充电量 (kWh)。
        prefer: 求解器。

    Returns:
        dict: 可行解 {p_ch, p_dis, shed, curt, ev, soc, feasible, dev_l1}。
    """
    T = case.T
    I = case.I
    bat = case.battery
    dt = 1.0

    def ev_req_of(i, t):
        return ev_req[case.users[i].name][t]

    m = pyo.ConcreteModel("safety_projection")
    m.T = pyo.RangeSet(0, T - 1)
    m.I = pyo.RangeSet(0, I - 1)

    m.Pch = pyo.Var(m.T, domain=pyo.NonNegativeReals, bounds=(0, bat.power_kw))
    m.Pdis = pyo.Var(m.T, domain=pyo.NonNegativeReals, bounds=(0, bat.power_kw))
    m.shed = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.curt = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.ev = pyo.Var(m.I, m.T, domain=pyo.NonNegativeReals)
    m.soc = pyo.Var(pyo.RangeSet(0, T), domain=pyo.NonNegativeReals)
    m.dch = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.ddis = pyo.Var(m.T, domain=pyo.NonNegativeReals)

    m.dev_ch1 = pyo.Constraint(
        m.T, rule=lambda m, t: m.dch[t] >= m.Pch[t] - pch_req[t])
    m.dev_ch2 = pyo.Constraint(
        m.T, rule=lambda m, t: m.dch[t] >= pch_req[t] - m.Pch[t])
    m.dev_dis1 = pyo.Constraint(
        m.T, rule=lambda m, t: m.ddis[t] >= m.Pdis[t] - pdis_req[t])
    m.dev_dis2 = pyo.Constraint(
        m.T, rule=lambda m, t: m.ddis[t] >= pdis_req[t] - m.Pdis[t])

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

    m.balance = pyo.Constraint(
        m.T,
        rule=lambda m, t: (
            case.pv[t] + case.wind[t] + m.Pdis[t] + m.shed[t]
            == case.base_load[t] + sum(m.ev[i, t] for i in m.I)
            + m.Pch[t] + m.curt[t]
        ),
    )
    m.ev_ub = pyo.Constraint(
        m.I, m.T, rule=lambda m, i, t: m.ev[i, t] <= ev_req_of(i, t)
    )

    ev_curtail = sum(ev_req_of(i, t) - m.ev[i, t] for i in m.I for t in m.T)
    m.obj = pyo.Objective(
        expr=sum(m.dch[t] + m.ddis[t] for t in m.T)
        + W_THRU * sum(m.Pch[t] + m.Pdis[t] for t in m.T)
        + W_SHED * sum(m.shed[t] for t in m.T)
        + W_CURT * sum(m.curt[t] for t in m.T)
        + W_EV * ev_curtail,
        sense=pyo.minimize,
    )

    term, _ = solve(m, prefer=prefer)
    if term != pyo.TerminationCondition.optimal:
        raise RuntimeError(f"安全投影求解失败: {term}")

    result = {
        "p_ch": [pyo.value(m.Pch[t]) for t in range(T)],
        "p_dis": [pyo.value(m.Pdis[t]) for t in range(T)],
        "shed": [pyo.value(m.shed[t]) for t in range(T)],
        "curt": [pyo.value(m.curt[t]) for t in range(T)],
        "ev": {case.users[i].name: [pyo.value(m.ev[i, t]) for t in range(T)]
               for i in range(I)},
        "soc": [pyo.value(m.soc[t]) for t in range(T + 1)],
        "dev_l1": sum(pyo.value(m.dch[t]) + pyo.value(m.ddis[t]) for t in range(T)),
    }
    result["feasible"] = check_feasible(case, result)
    return result


def check_feasible(case: GameCase, sol, tol=1e-6):
    """校验解是否满足离网可行集的全部约束。"""
    T = case.T
    bat = case.battery
    ok = True

    for t in range(T):
        ev_t = sum(sol["ev"][u.name][t] for u in case.users)
        lhs = case.pv[t] + case.wind[t] + sol["p_dis"][t] + sol["shed"][t]
        rhs = case.base_load[t] + ev_t + sol["p_ch"][t] + sol["curt"][t]
        if abs(lhs - rhs) > 1e-4:
            ok = False
        if sol["p_ch"][t] > bat.power_kw + 1e-6 or sol["p_dis"][t] > bat.power_kw + 1e-6:
            ok = False
        if sol["shed"][t] < -1e-9 or sol["curt"][t] < -1e-9:
            ok = False

    for t in range(T + 1):
        if sol["soc"][t] < bat.soc_min - 1e-6 or sol["soc"][t] > bat.soc_max + 1e-6:
            ok = False
    if sol["soc"][T] < bat.soc_end_min - 1e-6:
        ok = False

    # SOC 动态一致性
    for t in range(T):
        pred = sol["soc"][t] + (bat.charge_eff * sol["p_ch"][t]
                                - sol["p_dis"][t] / bat.discharge_eff) / bat.capacity_kwh
        if abs(pred - sol["soc"][t + 1]) > 1e-6:
            ok = False
    return ok


class SafeController:
    """安全控制器: 包装任意策略, 保证输出动作严格可行。

    用法:
        ctrl = SafeController(case)
        safe = ctrl.act(pch_req, pdis_req, ev_req)
    """

    def __init__(self, case: GameCase, prefer=None):
        self.case = case
        self.prefer = prefer

    def act(self, pch_req, pdis_req, ev_req):
        """投影任意请求动作到可行集。"""
        return project_action(self.case, pch_req, pdis_req, ev_req,
                              prefer=self.prefer)
