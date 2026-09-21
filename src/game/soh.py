"""
Stage-4 组件: SOH 退化评估 (复用现有电池老化模型)。

对给定方法的 SOC / 储能功率轨迹, 按 soc_health_v3 的日历老化 + 循环
老化公式计算当日 SOH 衰减与等效经济成本, 作为"定价—调度—寿命"耦合
的评价指标。

公式 (与 src/battery/asset_eval.py 一致):
    日历: ΔSOH_cal = k_cal(SOC_avg)/365
    循环: ΔSOH_cyc = γ · E_throughput · DOD^(α−1)
    成本: C = n · C_new · (ΔSOH_cal + ΔSOH_cyc) · ∂f_resid/∂SOH
"""

from src.battery.asset_eval import (
    k_cal_continuous, k_cal_piecewise,
    GAMMA_DEFAULT, ALPHA_DEFAULT, C_NEW_DEFAULT, RESID_VALUE_DERIV,
)


def evaluate_soh(soc_traj, power_traj, c_new=C_NEW_DEFAULT,
                 gamma=GAMMA_DEFAULT, alpha=ALPHA_DEFAULT,
                 use_continuous=True):
    """评估一条 SOC/功率轨迹的 SOH 衰减与经济成本。

    Args:
        soc_traj: SOC 轨迹 (分数 0~1), 长度 T+1。
        power_traj: 储能功率轨迹 (kW, 正=放电), 长度 T。
        c_new: 电池总购置成本 (元)。
        gamma: 循环退化系数。
        alpha: DOD 非线性指数。
        use_continuous: 用连续 k_cal 近似还是分段查表。

    Returns:
        dict: delta_soh_cal / delta_soh_cyc / delta_soh_total /
              cost_cal / cost_cyc / cost_total / throughput / dod / soc_avg。
    """
    soc_pct = [max(0.0, min(100.0, s * 100.0)) for s in soc_traj]
    soc_avg = sum(soc_pct) / max(len(soc_pct), 1)
    k_cal = k_cal_continuous(soc_avg) if use_continuous else k_cal_piecewise(soc_avg)
    delta_cal = k_cal / 365.0

    throughput = sum(abs(p) for p in power_traj)
    dod = max(0.01, min(1.0, (max(soc_pct) - min(soc_pct)) / 100.0))
    delta_cyc = gamma * throughput * (dod ** (alpha - 1))

    deriv = RESID_VALUE_DERIV
    cost_cal = c_new * delta_cal * deriv
    cost_cyc = c_new * delta_cyc * deriv

    return {
        "delta_soh_cal": delta_cal,
        "delta_soh_cyc": delta_cyc,
        "delta_soh_total": delta_cal + delta_cyc,
        "cost_cal": cost_cal,
        "cost_cyc": cost_cyc,
        "cost_total": cost_cal + cost_cyc,
        "throughput_kwh": throughput,
        "dod": dod,
        "soc_avg_pct": soc_avg,
    }
