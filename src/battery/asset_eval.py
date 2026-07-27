"""
电池资产经济评价模块 —— 日历老化 + 循环老化成本计算。

按照 soc_health_v3.pdf 的建模思路，逐组计算：
  1. 日历老化：SOC 依赖的年老化率折算为日衰减
  2. 循环老化：能量吞吐量 × DOD 非线性加权
  3. 两组老化合并为日电池使用成本

残值函数: f_resid(SOH) = (SOH - 0.70) / 0.30  (SOH∈[0.70, 1.00])
导数: ∂f_resid/∂SOH = 1/0.30 ≈ 3.333 (为恒定值)
"""

import logging
import math
from typing import List, Optional, Tuple
from dataclasses import dataclass

from src.battery.group import BatteryGroupManager, NUM_GROUPS, SOH_RETIRE, SOH_NEW

logger = logging.getLogger(__name__)

# ── 默认经济与技术参数 ────────────────────────────────────
C_NEW_PER_KWH = 1000.0        # 新电池单位成本 (元/kWh)
C_NEW_DEFAULT = 7_500_000.0   # 新电池总成本 (元, 7500 kWh × 1000 元/kWh)
C_SALVAGE_RATIO = 0.05        # 退役残值回收率

GAMMA_DEFAULT = 1.5e-8        # 循环退化系数 (1/kWh)，需通过实测标定
ALPHA_DEFAULT = 1.5           # DOD 非线性指数

CHARGE_EFF = 0.95             # 充电效率
DISCHARGE_EFF = 0.95          # 放电效率

# SOC 区间 → 年老化率分段表 (soc_health_v3 表2)
# 格式: (SOC下限, SOC上限, 年老化率)
K_CAL_TABLE = [
    (0.10, 0.50, 0.010),
    (0.50, 0.80, 0.015),
    (0.80, 0.90, 0.025),
    (0.90, 1.00, 0.040),
]


def k_cal_piecewise(soc_pct: float) -> float:
    """SOC 依赖的年日历老化率（分段查表）。

    Args:
        soc_pct: SOC 百分比 (0~100)

    Returns:
        float: 年老化率 (1/年)，如 0.01 表示 1%/年
    """
    s = soc_pct / 100.0
    if s < 0.10:
        s = 0.10
    if s > 1.00:
        s = 1.00

    for lo, hi, rate in K_CAL_TABLE:
        if lo <= s <= hi:
            return rate

    return K_CAL_TABLE[-1][2]


def k_cal_continuous(soc_pct: float) -> float:
    """SOC 依赖的年日历老化率（连续近似，eq.10）。

    k_cal(s) = 0.01 + 0.005·max(0,(s-50)/40) + 0.015·max(0,(s-80)/20)^2
    """
    s = max(0.0, min(100.0, soc_pct))
    base = 0.010
    mid = 0.005 * max(0.0, (s - 50.0) / 40.0)
    high = 0.015 * (max(0.0, (s - 80.0) / 20.0)) ** 2
    return base + mid + high


def residual_value_ratio(soh: float) -> float:
    """残值率函数 f_resid(SOH)。

    f_resid = (SOH - 0.70) / 0.30, clamped to [0, 1]

    Args:
        soh: SOH (0~1)

    Returns:
        float: 残值率 (0~1)
    """
    if soh < SOH_RETIRE:
        return 0.0
    ratio = (soh - SOH_RETIRE) / (SOH_NEW - SOH_RETIRE)
    return max(0.0, min(1.0, ratio))


RESID_VALUE_DERIV = 1.0 / (SOH_NEW - SOH_RETIRE)


def residual_value_deriv(soh: float) -> float:
    """残值函数导数 ∂f_resid/∂SOH。

    在分段线性模型中，SOH ∈ [0.70, 1.00] 区间内为常数 = 1/0.30。
    SOH < 0.70 时残值为 0，导数也为 0。
    """
    if soh < SOH_RETIRE:
        return 0.0
    return RESID_VALUE_DERIV


@dataclass
class AgingResult:
    """单组单日老化计算结果。"""
    group_name: str
    n_batteries: int
    soh_avg: float
    soc_avg_daily: float
    delta_soh_cal: float
    delta_soh_cyc: float
    delta_soh_total: float
    cost_cal_yuan: float
    cost_cyc_yuan: float
    cost_total_yuan: float
    energy_throughput_kwh: float
    dod_eff: float


def evaluate_battery_aging(
    group_mgr: BatteryGroupManager,
    hourly_storage_kw: List[float],
    hourly_soc_pct: List[float],
    eb_kwh: float = 7500.0,
    c_new: float = C_NEW_DEFAULT,
    gamma: float = GAMMA_DEFAULT,
    alpha: float = ALPHA_DEFAULT,
    use_continuous_kcal: bool = False,
) -> Tuple[List[AgingResult], float]:
    """按组评估单日电池老化成本。

    基于 24 小时仿真结果，计算各组日历老化 + 循环老化引起的
    SOH 衰减及其等效经济成本。

    Args:
        group_mgr: 电池组管理器
        hourly_storage_kw: 每小时储能功率 (kW, 正=放电, 负=充电)
        hourly_soc_pct: 每小时 SOC 百分比
        eb_kwh: 单块电池标称容量 (kWh)
        c_new: 新电池购置成本 (元)
        gamma: 循环退化系数 (1/kWh)
        alpha: DOD 非线性指数
        use_continuous_kcal: True 用连续公式，False 用分段查表

    Returns:
        (各组结果列表, 日电池使用总成本)
    """
    results: List[AgingResult] = []
    total_cost = 0.0

    for g_idx in range(NUM_GROUPS):
        g = group_mgr.get_group(g_idx)
        if g.is_empty:
            continue

        n = g.n_batteries
        soh_g = g.soh_avg
        soc_hourly = g.soc_hourly if g.soc_hourly else hourly_soc_pct

        if not soc_hourly:
            continue

        soc_avg_daily = sum(soc_hourly) / len(soc_hourly)

        # ── 日历老化 ─────────────────────────────────────
        if use_continuous_kcal:
            k_cal_val = k_cal_continuous(soc_avg_daily)
        else:
            k_cal_val = k_cal_piecewise(soc_avg_daily)

        delta_soh_cal = k_cal_val / 365.0

        # ── 循环老化 ─────────────────────────────────────
        e_throughput = sum(abs(kw) for kw in hourly_storage_kw)

        soc_max = max(soc_hourly)
        soc_min = min(soc_hourly)
        dod_eff = (soc_max - soc_min) / 100.0
        dod_eff = max(0.01, min(1.0, dod_eff))

        delta_soh_cyc = gamma * e_throughput * (dod_eff ** (alpha - 1))

        # ── 总 SOH 衰减 ──────────────────────────────────
        delta_soh_total = delta_soh_cal + delta_soh_cyc

        # ── 经济成本 ─────────────────────────────────────
        deriv = residual_value_deriv(soh_g)
        cost_cal = n * c_new * delta_soh_cal * deriv
        cost_cyc = n * c_new * delta_soh_cyc * deriv
        cost_total = cost_cal + cost_cyc

        total_cost += cost_total

        results.append(AgingResult(
            group_name=g.name,
            n_batteries=n,
            soh_avg=soh_g,
            soc_avg_daily=soc_avg_daily,
            delta_soh_cal=delta_soh_cal,
            delta_soh_cyc=delta_soh_cyc,
            delta_soh_total=delta_soh_total,
            cost_cal_yuan=round(cost_cal, 4),
            cost_cyc_yuan=round(cost_cyc, 4),
            cost_total_yuan=round(cost_total, 4),
            energy_throughput_kwh=round(e_throughput, 2),
            dod_eff=round(dod_eff, 4),
        ))

    return results, round(total_cost, 4)


def update_group_soh_after_day(
    group_mgr: BatteryGroupManager,
    aging_results: List[AgingResult],
) -> None:
    """根据当日老化结果更新各组 SOH。"""
    for ar in aging_results:
        g_idx = next(
            (i for i in range(NUM_GROUPS)
             if group_mgr.get_group(i).name == ar.group_name),
            None,
        )
        if g_idx is not None:
            group_mgr.update_soh(g_idx, ar.delta_soh_total)
