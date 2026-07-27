"""
全系统经济评价模块 —— 日净利润计算。

按照 soc_health_v3.pdf eq.26 的定义：
    Π_day = R_EV + R_sell − C_buy − C_battery − C_curt

整合购电/售电/EV充电收入、电池日使用成本（由 battery_asset_eval 给出），
输出单一经济指标——日净利润（元/日）。
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from src.battery.asset_eval import AgingResult

logger = logging.getLogger(__name__)

# ── 默认经济参数 (soc_health_v3 表5) ──────────────────────
PRICE_EV = 1.5          # EV 充电服务单价 (元/kWh)
PRICE_BUY = 0.8         # 外网购电电价 (元/kWh)
PRICE_SELL = 0.4        # 余电上网电价 (元/kWh)
PRICE_CURT = 0.2        # 弃电惩罚系数 (元/kWh)


@dataclass
class DailyEconomicResult:
    """单日全系统经济评价结果。"""
    date: str

    # 收入项
    revenue_ev_yuan: float = 0.0
    revenue_sell_yuan: float = 0.0

    # 成本项
    cost_buy_yuan: float = 0.0
    cost_battery_yuan: float = 0.0
    cost_curt_yuan: float = 0.0

    # 净利润
    profit_daily_yuan: float = 0.0

    # 物理指标
    total_load_kwh: float = 0.0
    total_generation_kwh: float = 0.0
    total_source_buy_kwh: float = 0.0
    total_source_sell_kwh: float = 0.0
    energy_throughput_kwh: float = 0.0
    soh_sys_pct: float = 100.0
    usable_capacity_kwh: float = 0.0
    is_retired: int = 0

    # 分组明细
    group_details: dict = field(default_factory=dict)

    # 累积累计
    cumulative_profit_yuan: float = 0.0
    cumulative_replace_cost_yuan: float = 0.0

    # 老化明细
    aging_details: List[AgingResult] = field(default_factory=list)


def evaluate_daily_economics(
    hourly_results: List[dict],
    group_mgr,
    aging_results: List[AgingResult],
    battery_cost_yuan: float,
    cumulative_profit: float = 0.0,
    cumulative_replace: float = 0.0,
    sim_date: str = "",
    lambda_ev: float = PRICE_EV,
    lambda_buy: float = PRICE_BUY,
    lambda_sell: float = PRICE_SELL,
    w_curt: float = PRICE_CURT,
    curtailment_kwh: float = 0.0,
) -> DailyEconomicResult:
    """计算单日全系统经济评价。

    Args:
        hourly_results: 24 小时仿真结果，每项含 hour/pv_kW/wind_kW/load_kW/
                       storage_kW/source_kW/soc_pct 等字段
        group_mgr: BatteryGroupManager 实例
        aging_results: 各组老化计算结果
        battery_cost_yuan: 日电池使用总成本
        cumulative_profit: 累计净利润（含当日之前）
        cumulative_replace: 累计置换支出
        sim_date: 仿真日期字符串
        lambda_ev: EV 充电单价 (元/kWh)
        lambda_buy: 购电电价 (元/kWh)
        lambda_sell: 售电电价 (元/kWh)
        w_curt: 弃电惩罚 (元/kWh)
        curtailment_kwh: 当日弃电量 (kWh)，默认 0

    Returns:
        DailyEconomicResult 对象
    """
    result = DailyEconomicResult(date=sim_date)

    # ── 汇总 ────────────────────────────────────────────
    total_load = 0.0
    total_gen = 0.0
    total_source_buy = 0.0
    total_source_sell = 0.0

    for rec in hourly_results:
        load = rec.get("load_kW", 0.0)
        pv = rec.get("pv_kW", 0.0)
        wind = rec.get("wind_kW", 0.0)
        source = rec.get("source_kW", 0.0)

        total_load += load
        total_gen += pv + wind

        if source > 0:
            total_source_buy += source
        elif source < 0:
            total_source_sell += abs(source)

    result.total_load_kwh = total_load
    result.total_generation_kwh = total_gen
    result.total_source_buy_kwh = total_source_buy
    result.total_source_sell_kwh = total_source_sell

    # ── 收入项 ──────────────────────────────────────────
    result.revenue_ev_yuan = lambda_ev * total_load
    result.revenue_sell_yuan = lambda_sell * total_source_sell

    # ── 成本项 ──────────────────────────────────────────
    result.cost_buy_yuan = lambda_buy * total_source_buy
    result.cost_battery_yuan = battery_cost_yuan
    result.cost_curt_yuan = w_curt * curtailment_kwh

    # ── 日净利润 ────────────────────────────────────────
    result.profit_daily_yuan = (
        result.revenue_ev_yuan
        + result.revenue_sell_yuan
        - result.cost_buy_yuan
        - result.cost_battery_yuan
        - result.cost_curt_yuan
    )

    # ── 累计 ────────────────────────────────────────────
    result.cumulative_profit_yuan = cumulative_profit + result.profit_daily_yuan
    result.cumulative_replace_cost_yuan = cumulative_replace

    # ── 物理指标 ────────────────────────────────────────
    result.energy_throughput_kwh = sum(
        abs(rec.get("storage_kW", 0.0)) for rec in hourly_results
    )
    result.soh_sys_pct = round(group_mgr.soh_system * 100, 4)
    result.usable_capacity_kwh = round(group_mgr.usable_capacity_kwh, 2)
    result.is_retired = 1 if group_mgr.retirement_count > 0 else 0

    # ── 分组明细 ────────────────────────────────────────
    groups_detail = {}
    for ar in aging_results:
        g_name = ar.group_name
        groups_detail[f"n_{g_name.lower()}"] = ar.n_batteries
        groups_detail[f"soh_{g_name.lower()}_pct"] = round(ar.soh_avg * 100, 4)
        groups_detail[f"soc_avg_{g_name.lower()}_pct"] = round(ar.soc_avg_daily, 2)
        groups_detail[f"cost_cal_{g_name.lower()}_yuan"] = ar.cost_cal_yuan
        groups_detail[f"cost_cyc_{g_name.lower()}_yuan"] = ar.cost_cyc_yuan
    result.group_details = groups_detail

    result.aging_details = aging_results

    return result


def format_economic_summary(result: DailyEconomicResult) -> str:
    """格式化经济评价摘要文本。"""
    lines = [
        "=" * 50,
        f"  日期: {result.date or 'N/A'}",
        f"  日净利润: {result.profit_daily_yuan:,.2f} 元",
        f"  累计净利润: {result.cumulative_profit_yuan:,.2f} 元",
        "-" * 50,
        f"  EV 充电收入: {result.revenue_ev_yuan:,.2f} 元",
        f"  余电上网收入: {result.revenue_sell_yuan:,.2f} 元",
        f"  外网购电成本: {result.cost_buy_yuan:,.2f} 元",
        f"  电池日使用成本: {result.cost_battery_yuan:,.2f} 元",
        f"  弃电惩罚成本: {result.cost_curt_yuan:,.2f} 元",
        "-" * 50,
        f"  全系统SOH: {result.soh_sys_pct:.2f}%",
        f"  可用容量: {result.usable_capacity_kwh:,.0f} kWh",
        f"  日吞吐量: {result.energy_throughput_kwh:,.0f} kWh",
        f"  总负荷: {result.total_load_kwh:,.0f} kWh",
        f"  总发电: {result.total_generation_kwh:,.0f} kWh",
        "=" * 50,
    ]
    return "\n".join(lines)
