"""
电池资产经济评价子包。

模块:
  - group: SOH 三组状态管理与组迁移逻辑
  - asset_eval: 日历老化 + 循环老化成本计算
  - economic_eval: 全系统经济评价（日净利润）
"""

from .group import BatteryGroupManager
from .asset_eval import (
    evaluate_battery_aging,
    update_group_soh_after_day,
    k_cal_piecewise,
    k_cal_continuous,
    residual_value_ratio,
    C_NEW_DEFAULT,
    GAMMA_DEFAULT,
    ALPHA_DEFAULT,
)
from .economic_eval import (
    evaluate_daily_economics,
    format_economic_summary,
    DailyEconomicResult,
    PRICE_EV,
    PRICE_BUY,
    PRICE_SELL,
    PRICE_CURT,
)

__all__ = [
    "BatteryGroupManager",
    "evaluate_battery_aging",
    "update_group_soh_after_day",
    "k_cal_piecewise",
    "k_cal_continuous",
    "residual_value_ratio",
    "C_NEW_DEFAULT",
    "GAMMA_DEFAULT",
    "ALPHA_DEFAULT",
    "evaluate_daily_economics",
    "format_economic_summary",
    "DailyEconomicResult",
    "PRICE_EV",
    "PRICE_BUY",
    "PRICE_SELL",
    "PRICE_CURT",
]
