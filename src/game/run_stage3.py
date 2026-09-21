"""
Stage-3 入口 —— 可微最佳反应代理 + 安全强化学习加速。

运行:
    python -m src.game.run_stage3

输出:
    - 代理模型精度与推理加速
    - 安全 RL 策略 vs MILP 最优: 净利润、推理时间、可行率
    - output/sfse_stage3_report.json
"""

import json
import logging
import os
import time

import numpy as np

from src.game.case import build_small_case, summarize_case
from src.game.env import PricingEnv
from src.game.equilibrium import solve_equilibrium
from src.game.follower import solve_follower_response
from src.game.rl import train_policy, pretrain_policy, PolicyNet
from src.game.surrogate import (
    BestResponseSurrogate, build_dataset, evaluate_surrogate,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("sfse.stage3")
logger.setLevel(logging.INFO)


def main():
    case = build_small_case()
    print(summarize_case(case))

    # ── 1. 可微最佳反应代理 ────────────────────────────
    print("\n[1] 训练最佳反应代理 (p -> E) ...")
    P, Y = build_dataset(case, n=1200, seed=0)
    model = BestResponseSurrogate(case.T, seed=0).fit(P, Y, epochs=300)
    acc = evaluate_surrogate(model, case, n=200, seed=99)
    print(f"    代理精度: MAE={acc['mae_kwh']:.3f} kWh, "
          f"相对误差={100*acc['rel_error']:.2f}%")

    # 响应推理速度对比
    p0 = [1.2] * case.T
    n_rep = 200
    t0 = time.perf_counter()
    for _ in range(n_rep):
        solve_follower_response(case, p0)
    t_lp = (time.perf_counter() - t0) / n_rep * 1000
    t0 = time.perf_counter()
    for _ in range(n_rep):
        model.predict(p0)
    t_nn = (time.perf_counter() - t0) / n_rep * 1000
    print(f"    响应推理: LP={t_lp:.3f} ms,  代理={t_nn:.3f} ms,  "
          f"加速={t_lp/max(t_nn,1e-6):.0f}x")

    # ── 2. 安全强化学习定价策略 ────────────────────────
    print("\n[2] 生成 MILP 示范数据并训练安全 RL 策略 ...")
    env = PricingEnv(case)
    n_demo = 120
    rng_demo = np.random.default_rng(7)
    X, Y = [], []
    t0 = time.perf_counter()
    for _ in range(n_demo):
        scen = env.sample_scenario(rng_demo)
        eq = solve_equilibrium(scen.case)
        X.append(scen.features)
        Y.append(eq["prices"])
    t_demo = time.perf_counter() - t0
    print(f"    示范数据: {n_demo} 条, 生成耗时 {t_demo:.1f} s")

    policy = _new_policy(env)
    pretrain_policy(policy, X, Y, epochs=600)
    t0 = time.perf_counter()
    policy, hist = train_policy(env, episodes=400, seed=0, verbose=True,
                                policy=policy)
    t_train = time.perf_counter() - t0
    print(f"    RL 微调完成: {len(hist)} episodes, 耗时 {t_train:.1f} s, "
          f"末100轮平均奖励={np.mean(hist[-100:]):.2f}")

    # ── 3. 测试场景: RL vs MILP 最优 ───────────────────
    print("\n[3] 测试场景对比 (RL 策略 vs MILP 最优) ...")
    rng = np.random.default_rng(2024)
    n_test = 15
    rl_profits, milp_profits = [], []
    feas = 0
    # RL 推理时间
    t0 = time.perf_counter()
    for _ in range(n_test):
        scen = env.sample_scenario(rng)
        feat = _tensor(scen.features)
        prices = policy.act_deterministic(feat)
        profit, feasible, _ = env.reward(prices, scen)
        rl_profits.append(profit)
        feas += int(feasible)
    t_rl = (time.perf_counter() - t0) / n_test * 1000

    # MILP 最优
    rng = np.random.default_rng(2024)
    t0 = time.perf_counter()
    for _ in range(n_test):
        scen = env.sample_scenario(rng)
        eq = solve_equilibrium(scen.case)
        milp_profits.append(eq["profit"])
    t_milp = (time.perf_counter() - t0) / n_test * 1000

    rl_mean = float(np.mean(rl_profits))
    milp_mean = float(np.mean(milp_profits))
    gap = rl_mean - milp_mean
    gap_pct = 100 * gap / abs(milp_mean)

    print(f"    RL   平均净利润 = {rl_mean:.2f} 元,  推理 {t_rl:.2f} ms/场景")
    print(f"    MILP 平均净利润 = {milp_mean:.2f} 元,  求解 {t_milp:.1f} ms/场景")
    print(f"    最优性间隙 = {gap:+.2f} 元 ({gap_pct:+.2f}%)")
    print(f"    加速比 = {t_milp/max(t_rl,1e-6):.0f}x")
    print(f"    RL 可行率 = {100*feas/n_test:.0f}%")

    # ── 4. 保存 ────────────────────────────────────────
    out_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "surrogate": {"mae_kwh": acc["mae_kwh"],
                      "rel_error": acc["rel_error"],
                      "lp_ms": t_lp, "nn_ms": t_nn,
                      "speedup": t_lp / max(t_nn, 1e-6)},
        "rl": {"train_seconds": t_train, "episodes": len(hist),
               "mean_profit": rl_mean, "inference_ms": t_rl,
               "feasibility_rate": feas / n_test},
        "milp": {"mean_profit": milp_mean, "solve_ms": t_milp},
        "comparison": {"profit_gap": gap, "profit_gap_pct": gap_pct,
                       "speedup": t_milp / max(t_rl, 1e-6)},
    }
    out_path = os.path.join(out_dir, "sfse_stage3_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


def _new_policy(env):
    return PolicyNet(env.n_features, env.base.T,
                     min(env.base.price_levels), max(env.base.price_levels),
                     hidden=256, seed=0)


def _tensor(feat):
    import torch
    return torch.tensor(feat, dtype=torch.float32)


if __name__ == "__main__":
    main()
