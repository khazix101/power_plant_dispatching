"""
Stage-2 入口 —— BRI 求解器 + 安全层 + 理论命题验证。

运行:
    python -m src.game.run_stage2

输出:
    - 控制台: MILP vs BRI 对比、安全层验证、命题验证
    - output/sfse_stage2_report.json
"""

import json
import logging
import os
import time

from src.game.bri import solve_bri_multistart
from src.game.case import build_small_case, summarize_case
from src.game.equilibrium import solve_equilibrium
from src.game.theory import (
    verify_existence, verify_safety, verify_bri_convergence,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("sfse.stage2")
logger.setLevel(logging.INFO)


def main():
    case = build_small_case()
    print(summarize_case(case))

    # ── 1. MILP 真解 (基准) ────────────────────────────
    print("\n[1] MILP 均衡真解 ...")
    t0 = time.perf_counter()
    eq = solve_equilibrium(case)
    t_milp = time.perf_counter() - t0
    print(f"    净利润 = {eq['profit']:.2f} 元,  耗时 = {t_milp*1000:.1f} ms")

    # ── 2. BRI 最佳反应迭代 ────────────────────────────
    print("[2] BRI 多起点最佳反应迭代 ...")
    t0 = time.perf_counter()
    bri = solve_bri_multistart(case, n_restarts=8, seed=0)
    t_bri = time.perf_counter() - t0
    gap = bri["profit"] - eq["profit"]
    gap_pct = 100 * gap / abs(eq["profit"]) if eq["profit"] else float("nan")
    print(f"    净利润 = {bri['profit']:.2f} 元,  起点数 = {bri['n_restarts']},  "
          f"总扫描 = {bri['total_sweeps']},  耗时 = {t_bri*1000:.1f} ms")
    print(f"    相对 MILP 间隙 = {gap:+.4f} 元 ({gap_pct:+.4f}%)")

    # ── 3. 安全层验证 ──────────────────────────────────
    print("[3] 安全层验证 (命题 3) ...")
    safety = verify_safety(case, n=40)
    print(f"    随机请求可行率 = {100*safety['feasibility_rate']:.0f}%  "
          f"({safety['feasible']}/{safety['n']})")
    print(f"    幂等偏差 = {safety['idempotence_dev_l1']:.3e}  "
          f"({'幂等' if safety['idempotent'] else '非幂等'})")

    # ── 4. 理论命题验证 ────────────────────────────────
    print("[4] 命题验证 ...")
    exist = verify_existence(case, n=12)
    print(f"    命题1 存在性: {exist['solved']}/{exist['n']} "
          f"({100*exist['existence_rate']:.0f}%)")
    conv = verify_bri_convergence(case, n_inits=3, n_restarts=8)
    print(f"    命题4 收敛性: 不动点 {conv['fixed_points']}/{conv['n_inits']} "
          f"({100*conv['fixed_point_rate']:.0f}%),  最优收益极差 = "
          f"{conv['profit_spread']:.3e}  "
          f"({'一致' if conv['consistent'] else '有波动'})")

    # ── 5. 保存 ────────────────────────────────────────
    out_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "milp": {"profit": eq["profit"], "prices": eq["prices"],
                 "time_ms": t_milp * 1000, "response": eq["response"]},
        "bri": {"profit": bri["profit"], "prices": bri["prices"],
                "n_restarts": bri["n_restarts"], "time_ms": t_bri * 1000,
                "gap_vs_milp": gap, "gap_pct": gap_pct},
        "safety": safety,
        "proposition_existence": exist,
        "proposition_convergence": conv,
    }
    out_path = os.path.join(out_dir, "sfse_stage2_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
