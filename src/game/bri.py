"""
最佳反应迭代 (Best-Response Iteration, BRI) 求解器。

由于领导者必须"预判"跟随者响应, 直接的朴素迭代并不收敛。本模块采用
**电价坐标上升 (coordinate ascent)** 的实用型最佳反应迭代:

    初始化电价 p^0
    repeat:
        for 每个时段 t:
            对每个电价档位 k, 用跟随者预言机计算响应 e(p),
            领导者在该响应下优化储能/可靠性, 得到净利润;
            取使净利润最大的档位更新 p_t
        若一轮无改进则停止

每次评估都调用精确的跟随者 LP (最优反应), 因此得到的解一定是
博弈的一个均衡点 (双方均无单边改进动机), 但可能只是局部最优。
其价值在于: 单轮只需 T×K 次小规模求解, 规模扩展性优于单体 MILP,
可作为 Stage-3 学习型加速的对照与热启动。
"""

import logging

from src.game.baseline import solve_leader_fixed_profile
from src.game.case import GameCase
from src.game.follower import solve_follower_response

logger = logging.getLogger(__name__)


def evaluate_price_vector(case: GameCase, prices, prefer=None):
    """给定电价, 返回 (净利润, 跟随者响应, 领导者调度)。

    领导者在该电价诱导的 EV 负荷下做最优储能/可靠性调度。
    """
    response = solve_follower_response(case, prices, prefer=prefer)
    ev_profile = [sum(response[u.name][t] for u in case.users) for t in range(case.T)]
    revenue = sum(prices[t] * ev_profile[t] for t in range(case.T))
    sched = solve_leader_fixed_profile(case, ev_profile, revenue, prefer=prefer)
    return sched["profit"], response, sched


def solve_equilibrium_bri(case: GameCase, init_prices=None,
                          max_sweeps=30, tol=1e-6, prefer=None,
                          verbose=False):
    """坐标上升式最佳反应迭代求均衡。

    Args:
        case: 博弈算例。
        init_prices: 初始电价向量, 默认全为最低档。
        max_sweeps: 最大扫描轮数。
        tol: 改进判定阈值。
        prefer: 求解器。
        verbose: 是否打印每轮收益。

    Returns:
        dict: 与 solve_equilibrium 相同结构, 附加 sweeps 字段。
    """
    T = case.T
    levels = case.price_levels
    p = list(init_prices) if init_prices else [min(levels)] * T

    profit, response, sched = evaluate_price_vector(case, p, prefer=prefer)
    history = [profit]

    for sweep in range(max_sweeps):
        improved = False
        for t in range(T):
            best_k = p[t]
            best_profit = profit
            best_pack = (response, sched)
            for lv in levels:
                if abs(lv - p[t]) < 1e-12:
                    continue
                cand = list(p)
                cand[t] = lv
                cp, cresp, csched = evaluate_price_vector(case, cand, prefer=prefer)
                if cp > best_profit + tol:
                    best_profit = cp
                    best_k = lv
                    best_pack = (cresp, csched)
            if best_k != p[t]:
                p[t] = best_k
                profit = best_profit
                response, sched = best_pack
                improved = True
        history.append(profit)
        if verbose:
            logger.info("BRI sweep %d: profit=%.4f, prices=%s", sweep, profit, p)
        if not improved:
            break

    result = dict(sched)
    result.update({
        "profit": profit,
        "prices": p,
        "response": response,
        "sweeps": len(history) - 1,
        "history": history,
        "method": "BRI",
    })
    logger.info("BRI 收敛: profit=%.2f, sweeps=%d", profit, result["sweeps"])
    return result


def solve_bri_multistart(case: GameCase, n_restarts=8, seed=0,
                         prefer=None, verbose=False):
    """多起点 BRI: 从多个初始电价出发, 取收益最高者, 缓解局部最优。"""
    import random
    rng = random.Random(seed)
    levels = case.price_levels
    best = None
    total_sweeps = 0
    for r in range(n_restarts):
        if r == 0:
            init = [min(levels)] * case.T
        elif r == 1:
            init = [max(levels)] * case.T
        else:
            init = [rng.choice(levels) for _ in range(case.T)]
        res = solve_equilibrium_bri(case, init_prices=init, prefer=prefer,
                                    verbose=verbose)
        total_sweeps += res["sweeps"]
        if best is None or res["profit"] > best["profit"]:
            best = res
    best["method"] = "BRI-multistart"
    best["n_restarts"] = n_restarts
    best["total_sweeps"] = total_sweeps
    logger.info("BRI-multistart 最优: profit=%.2f", best["profit"])
    return best

