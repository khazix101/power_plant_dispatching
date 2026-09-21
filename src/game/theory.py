"""
Stage-2 理论命题的数值验证。

对应开题文档的四条命题:
  命题 1 (存在性)  : 随机扰动场景下, 安全可行均衡是否总存在 (MILP 有最优解)。
  命题 3 (安全性)  : 结构化投影算子输出严格可行, 且对可行点是幂等的。
  命题 4 (收敛性)  : BRI 从不同初值出发是否收敛到一致的均衡收益。
(命题 2 唯一性依赖严格凹性假设, 留待全尺寸模型讨论。)
"""

import copy
import logging
import random

from src.game.bri import solve_bri_multistart
from src.game.case import GameCase
from src.game.equilibrium import solve_equilibrium
from src.game.safety import project_action, check_feasible

logger = logging.getLogger(__name__)


def randomize_case(case: GameCase, rng: random.Random) -> GameCase:
    """生成扰动场景: 缩放新能源/负荷, 微调用户保留效用。"""
    c = copy.deepcopy(case)
    s_re = rng.uniform(0.7, 1.3)
    s_load = rng.uniform(0.8, 1.2)
    c.pv = [v * s_re for v in case.pv]
    c.wind = [v * s_re for v in case.wind]
    c.base_load = [v * s_load for v in case.base_load]
    for u in c.users:
        u.value_per_kwh = max(0.1, u.value_per_kwh + rng.uniform(-0.1, 0.1))
    return c


def verify_existence(case: GameCase, n=12, seed=0, prefer=None):
    """命题 1: 随机场景下均衡存在率。"""
    rng = random.Random(seed)
    ok = 0
    profits = []
    for _ in range(n):
        c = randomize_case(case, rng)
        try:
            res = solve_equilibrium(c, prefer=prefer)
            ok += 1
            profits.append(res["profit"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("场景不可解: %s", exc)
    rate = ok / n if n else 0.0
    logger.info("命题1 存在性: %d/%d (%.0f%%)", ok, n, 100 * rate)
    return {"n": n, "solved": ok, "existence_rate": rate,
            "profit_min": min(profits) if profits else None,
            "profit_max": max(profits) if profits else None}


def verify_safety(case: GameCase, n=40, seed=1, prefer=None):
    """命题 3: 随机请求动作经投影后的可行率 + 幂等性。"""
    rng = random.Random(seed)
    T, I = case.T, case.I
    feasible_cnt = 0
    for _ in range(n):
        pch = [rng.uniform(0, 400) for _ in range(T)]
        pdis = [rng.uniform(0, 400) for _ in range(T)]
        ev = {u.name: [rng.uniform(0, u.max_power_kw * 2) for _ in range(T)]
              for u in case.users}
        sol = project_action(case, pch, pdis, ev, prefer=prefer)
        if sol["feasible"]:
            feasible_cnt += 1
    rate = feasible_cnt / n if n else 0.0

    # 幂等性: 对均衡解本身投影, 偏差应约为 0
    eq = solve_equilibrium(case, prefer=prefer)
    sol = project_action(case, eq["p_ch"], eq["p_dis"], eq["response"], prefer=prefer)
    idem_dev = sol["dev_l1"]

    logger.info("命题3 安全性: 可行率 %.0f%%, 幂等偏差 %.3e", 100 * rate, idem_dev)
    return {"n": n, "feasible": feasible_cnt, "feasibility_rate": rate,
            "idempotence_dev_l1": idem_dev, "idempotent": idem_dev < 1e-4}


def _is_fixed_point(case: GameCase, prices, prefer=None):
    """检查给定电价是否已是 BRI 不动点 (无单坐标改进)。"""
    from src.game.bri import evaluate_price_vector
    cur, _, _ = evaluate_price_vector(case, prices, prefer=prefer)
    for t in range(case.T):
        for lv in case.price_levels:
            if abs(lv - prices[t]) < 1e-12:
                continue
            cand = list(prices)
            cand[t] = lv
            p, _, _ = evaluate_price_vector(case, cand, prefer=prefer)
            if p > cur + 1e-6:
                return False
    return True


def verify_bri_convergence(case: GameCase, n_inits=3, n_restarts=8, seed=2,
                           prefer=None):
    """命题 4: BRI 收敛性。

    验证两点:
      (a) BRI 从任意初值出发都终止于不动点 (局部均衡);
      (b) 多起点 BRI 的最优收益在不同随机种子下稳定 (近全局)。
    """
    from src.game.bri import solve_equilibrium_bri
    fixed_cnt = 0
    for s in range(n_inits):
        rng = random.Random(seed + 100 + s)
        init = [rng.choice(case.price_levels) for _ in range(case.T)]
        res = solve_equilibrium_bri(case, init_prices=init, prefer=prefer)
        if _is_fixed_point(case, res["prices"], prefer=prefer):
            fixed_cnt += 1

    best_profits = []
    for s in range(n_inits):
        res = solve_bri_multistart(case, n_restarts=n_restarts,
                                   seed=seed + s, prefer=prefer)
        best_profits.append(res["profit"])
    spread = max(best_profits) - min(best_profits)
    logger.info("命题4 收敛性: 不动点 %d/%d, 最优收益极差 %.3f",
                fixed_cnt, n_inits, spread)
    return {"n_inits": n_inits, "fixed_points": fixed_cnt,
            "fixed_point_rate": fixed_cnt / n_inits,
            "best_profits": best_profits, "profit_spread": spread,
            "consistent": spread < 1e-3}
