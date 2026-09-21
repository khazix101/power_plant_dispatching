"""
Stage-1 入口 —— 小算例安全可行主从博弈均衡求解与验证。

运行:
    python -m src.game.run_small_case

输出:
    - 控制台对比表 (SFSE 均衡 vs 固定电价基线)
    - output/sfse_stage1_small_case.json

验证内容:
    1. 均衡 MILP 是否求得最优解;
    2. 均衡电价下的跟随者响应是否与 MILP 内层解一致 (KKT 正确性);
    3. 均衡相对固定电价基线的收益增益。
"""

import json
import logging
import os

from src.game.baseline import run_fixed_price_baseline
from src.game.case import build_small_case, summarize_case
from src.game.equilibrium import solve_equilibrium
from src.game.follower import solve_follower_response, follower_utility

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")
logger = logging.getLogger("sfse.stage1")
logger.setLevel(logging.INFO)


def _fmt_row(label, values, width=8):
    cells = " ".join(f"{v:>{width}.2f}" for v in values)
    return f"  {label:<16}{cells}"


def main():
    case = build_small_case()
    print(summarize_case(case))

    # ── 1. 求 SFSE 均衡 ────────────────────────────────
    print("\n[1] 求解安全可行主从博弈均衡 (KKT + 大M → MILP) ...")
    eq = solve_equilibrium(case)

    # ── 2. 校验跟随者最优性 (比较效用, 而非具体分配) ────
    print("[2] 校验均衡电价下的跟随者最优性 ...")
    resp_check = solve_follower_response(case, eq["prices"])
    u_eq = follower_utility(case, eq["prices"], eq["response"])
    u_opt = follower_utility(case, eq["prices"], resp_check)
    util_gap = u_opt - u_eq
    print(f"    均衡响应效用   = {u_eq:.4f} 元")
    print(f"    独立最优效用   = {u_opt:.4f} 元")
    print(f"    最优性间隙     = {util_gap:.3e} 元  "
          f"({'通过' if util_gap < 1e-4 else '不通过'})")

    # ── 3. 固定电价基线 ────────────────────────────────
    print("[3] 求解固定电价基线 ...")
    base = run_fixed_price_baseline(case, fixed_price=1.2)

    # ── 4. 结果对比 ────────────────────────────────────
    ev_eq = [sum(eq["response"][u.name][t] for u in case.users) for t in range(case.T)]
    ev_base = [sum(base["response"][u.name][t] for u in case.users) for t in range(case.T)]

    print("\n================ 结果对比 ================")
    print(f"  求解器           : {eq['solver']}")
    print(f"  均衡状态         : {eq['termination']}")
    print(f"  SFSE 净利润      : {eq['profit']:.2f} 元")
    print(f"  固定电价净利润   : {base['profit']:.2f} 元")
    delta = eq["profit"] - base["profit"]
    pct = 100.0 * delta / abs(base["profit"]) if base["profit"] else float("nan")
    print(f"  博弈增益         : {delta:+.2f} 元 ({pct:+.2f}%)")
    print(f"  均衡失负荷总量   : {sum(eq['shed']):.2f} kWh")
    print(f"  基线失负荷总量   : {sum(base['shed']):.2f} kWh")

    print("\n  ── 电价 (元/kWh) ──")
    print(_fmt_row("时段", list(range(case.T)), width=6))
    print(_fmt_row("SFSE", eq["prices"]))
    print(_fmt_row("固定基线", base["prices"]))
    print("\n  ── EV 总充电负荷 (kWh) ──")
    print(_fmt_row("SFSE", ev_eq))
    print(_fmt_row("固定基线", ev_base))
    print("\n  ── 储能功率 (kW, 正=放电) ──")
    print(_fmt_row("SFSE", [eq["p_dis"][t] - eq["p_ch"][t] for t in range(case.T)]))
    print("\n  ── SOC (%) ──")
    print(_fmt_row("SFSE", [100 * s for s in eq["soc"]]))
    print("=" * 42)

    # ── 5. 保存结果 ────────────────────────────────────
    out_dir = os.path.join(os.getcwd(), "output")
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "case": {
            "T": case.T,
            "pv": case.pv,
            "wind": case.wind,
            "base_load": case.base_load,
            "price_levels": case.price_levels,
            "users": [
                {"name": u.name, "value_per_kwh": u.value_per_kwh,
                 "energy_max_kwh": u.energy_max_kwh,
                 "avail": list(u.available), "pmax": u.max_power_kw}
                for u in case.users
            ],
        },
        "equilibrium": eq,
        "baseline_fixed_price": base,
        "validation": {"follower_optimality_gap_yuan": util_gap},
        "summary": {
            "profit_gain_yuan": delta,
            "profit_gain_pct": pct,
            "shed_eq_kwh": sum(eq["shed"]),
            "shed_base_kwh": sum(base["shed"]),
        },
    }
    out_path = os.path.join(out_dir, "sfse_stage1_small_case.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
