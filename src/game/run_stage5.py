"""
Stage-5 入口 —— 全尺寸安全 RL + 不确定性/极端天气鲁棒性 + 总结。

运行:
    python -m src.game.run_stage5

流程:
  1. 生成随机场景, 用多起点 BRI 求示范均衡 (learning-to-optimize 标签);
  2. 行为克隆预训练 + A2C 微调全尺寸安全 RL 策略;
  3. 测试场景: RL vs BRI 的收益/速度/可靠性;
  4. 不确定性场景 (风光/负荷/用户扰动) 与极端天气的鲁棒性对比;
  5. 输出 output/sfse_stage5_report.json
"""

import json
import logging
import os
import time

import numpy as np

from src.game.baseline import run_fixed_price_baseline
from src.game.bri import solve_equilibrium_bri
from src.game.case import build_full_case, summarize_case
from src.game.env import PricingEnv
from src.game.rl import PolicyNet, pretrain_policy, train_policy

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("sfse.stage5")
logger.setLevel(logging.INFO)


def bri_solve(case, init=1.5, max_sweeps=3):
    return solve_equilibrium_bri(case, init_prices=[init] * case.T,
                                 max_sweeps=max_sweeps)


def eval_fixed_price(case, price=1.2):
    r = run_fixed_price_baseline(case, fixed_price=price)
    return {"profit": r["profit"], "shed": sum(r["shed"]), "curt": sum(r["curt"])}


def main():
    case = build_full_case()
    print(summarize_case(case))
    env = PricingEnv(case)
    rng = np.random.default_rng(2024)

    # ── 1. 生成示范数据 (BRI) ──────────────────────────
    print("\n[1] 生成 BRI 示范均衡数据 ...")
    n_train = 20
    train_scens = [env.sample_scenario(rng) for _ in range(n_train)]
    X, Y = [], []
    bri_train_prof, bri_train_rel = [], []
    t0 = time.perf_counter()
    for i, s in enumerate(train_scens):
        r = bri_solve(s.case, max_sweeps=3)
        X.append(s.features)
        Y.append(r["prices"])
        bri_train_prof.append(r["profit"])
        bri_train_rel.append(sum(r["shed"]) < 1.0)
    t_label = time.perf_counter() - t0
    print(f"    {n_train} 条示范, 耗时 {t_label:.1f}s")

    # ── 2. 训练全尺寸安全 RL ───────────────────────────
    print("[2] 预训练 + A2C 微调 ...")
    policy = PolicyNet(env.n_features, case.T,
                       min(case.price_levels), max(case.price_levels),
                       hidden=256, seed=0)
    pretrain_policy(policy, X, Y, epochs=600)
    t0 = time.perf_counter()
    policy, hist = train_policy(env, episodes=800, seed=0, verbose=True,
                                policy=policy)
    t_rl_train = time.perf_counter() - t0
    print(f"    RL 微调完成, 耗时 {t_rl_train:.1f}s")

    # ── 3. 测试场景: RL vs BRI ─────────────────────────
    print("[3] 测试场景 RL vs BRI ...")
    n_test = 4
    test_scens = [env.sample_scenario(rng) for _ in range(n_test)]
    rl_profits, bri_profits = [], []
    t_rl_total = 0.0
    t_bri_total = 0.0
    for s in test_scens:
        import torch
        feat = torch.tensor(s.features, dtype=torch.float32)
        t0 = time.perf_counter()
        prices = policy.act_deterministic(feat)
        t_rl_total += time.perf_counter() - t0
        rl_p, _, _ = env.reward(prices, s)
        rl_profits.append(rl_p)

        t0 = time.perf_counter()
        b = bri_solve(s.case)
        t_bri_total += time.perf_counter() - t0
        bri_profits.append(b["profit"])
    rl_mean = float(np.mean(rl_profits))
    bri_mean = float(np.mean(bri_profits))
    gap_pct = 100 * (rl_mean - bri_mean) / abs(bri_mean)
    t_rl_ms = t_rl_total / n_test * 1000
    t_bri_ms = t_bri_total / n_test * 1000
    print(f"    RL  平均净利润={rl_mean:.1f}, 决策 {t_rl_ms:.2f} ms/场景")
    print(f"    BRI 平均净利润={bri_mean:.1f}, 求解 {t_bri_ms:.0f} ms/场景")
    print(f"    间隙={gap_pct:+.2f}%,  决策加速={t_bri_ms/max(t_rl_ms,1e-6):.0f}x")

    # ── 4. 不确定性鲁棒性 ──────────────────────────────
    print("[4] 不确定性场景鲁棒性 (SFSE-BRI vs 固定电价) ...")
    bri_prof, bri_rel = bri_train_prof, bri_train_rel
    fp_prof, fp_rel = [], []
    for s in train_scens:
        f = eval_fixed_price(s.case)
        fp_prof.append(f["profit"])
        fp_rel.append(f["shed"] < 1.0)
    print(f"    SFSE-BRI: 均值={np.mean(bri_prof):.1f}, "
          f"最差={np.min(bri_prof):.1f}, 可靠率={100*np.mean(bri_rel):.0f}%")
    print(f"    固定电价: 均值={np.mean(fp_prof):.1f}, "
          f"最差={np.min(fp_prof):.1f}, 可靠率={100*np.mean(fp_rel):.0f}%")

    # ── 5. 极端天气鲁棒性 ──────────────────────────────
    print("[5] 极端天气 (新能源骤降) 鲁棒性 ...")
    ext_scens = [env.sample_scenario(rng, extreme=True) for _ in range(2)]
    ext_bri, ext_fp = [], []
    for s in ext_scens:
        b = bri_solve(s.case)
        ext_bri.append({"profit": b["profit"], "shed": sum(b["shed"])})
        f = eval_fixed_price(s.case)
        ext_fp.append(f)
    print(f"    极端场景 SFSE-BRI 失负荷: "
          f"{[round(x['shed']) for x in ext_bri]} kWh")
    print(f"    极端场景 固定电价 失负荷: "
          f"{[round(x['shed']) for x in ext_fp]} kWh")

    # ── 6. 保存 ────────────────────────────────────────
    out_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "rl_full_scale": {
            "label_time_s": t_label, "train_time_s": t_rl_train,
            "rl_mean_profit": rl_mean, "bri_mean_profit": bri_mean,
            "gap_pct": gap_pct, "decision_ms": t_rl_ms,
            "bri_solve_ms": t_bri_ms,
            "decision_speedup": t_bri_ms / max(t_rl_ms, 1e-6),
        },
        "uncertainty": {
            "bri_mean": float(np.mean(bri_prof)),
            "bri_worst": float(np.min(bri_prof)),
            "bri_reliability": float(np.mean(bri_rel)),
            "fixed_mean": float(np.mean(fp_prof)),
            "fixed_worst": float(np.min(fp_prof)),
            "fixed_reliability": float(np.mean(fp_rel)),
        },
        "extreme_weather": {"bri": ext_bri, "fixed": ext_fp},
    }
    out_path = os.path.join(out_dir, "sfse_stage5_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
