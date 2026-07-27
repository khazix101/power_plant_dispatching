"""
场景条件系统。

提供 DispatchContext（调度上下文）和可组合的 Condition（条件）对象，
用于场景匹配的条件求值。支持 AND/OR/NOT 组合。
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Set


@dataclass
class DispatchContext:
    """
    单小时调度上下文，包含场景条件求值所需的全部信息。

    日级场景仅需 date / month / is_holiday 字段；
    小时级场景还需 hour / load / soc 等实时字段。
    """

    hour: int
    date: date
    pv_kw: float
    wind_kw: float
    load_kw: float
    soc_pct: float
    temperature_c: float
    weather_type: str
    is_holiday: bool
    rated_capacity_kwh: float
    rated_power_kw: float
    charge_eff: float = 0.95
    discharge_eff: float = 0.95
    soc_min_hard: float = 10.0
    soc_max_hard: float = 90.0

    @property
    def net_kw(self):
        """净功率 (盈余为正, 缺额为负) = 光伏 + 风电 - 负荷"""
        return self.pv_kw + self.wind_kw - self.load_kw

    @property
    def renewable_ratio(self):
        """
        新能源渗透率 = (光伏 + 风电) / 负荷。
        负荷为 0 时返回正无穷。
        """
        if self.load_kw > 0:
            return (self.pv_kw + self.wind_kw) / self.load_kw
        return float('inf')


class Condition:
    """
    可组合的场景条件。

    通过 & (AND)、| (OR)、~ (NOT) 运算符组合多个条件。
    调用 evaluate(ctx) 返回 bool。

    用法:
        c = hour_in_range(8, 11) & load_above(10000)
        if c.evaluate(ctx):
            ...
    """

    def __init__(self, eval_fn, description=""):
        self._eval = eval_fn
        self.desc = description

    def evaluate(self, ctx):
        return self._eval(ctx)

    def __and__(self, other):
        return Condition(
            lambda ctx: self._eval(ctx) and other._eval(ctx),
            f"({self.desc} AND {other.desc})",
        )

    def __or__(self, other):
        return Condition(
            lambda ctx: self._eval(ctx) or other._eval(ctx),
            f"({self.desc} OR {other.desc})",
        )

    def __invert__(self):
        return Condition(
            lambda ctx: not self._eval(ctx),
            f"(NOT {self.desc})",
        )

    def __repr__(self):
        return f"Condition({self.desc})"


# ============================================================
#  条件工厂函数
# ============================================================

def hour_in_range(start: int, end: int) -> Condition:
    """小时落在 [start, end] 区间内（跨零时用 start > end 表示，如 23,7）。"""
    if start <= end:
        return Condition(
            lambda ctx: start <= ctx.hour <= end,
            f"hour in [{start}, {end}]",
        )
    else:
        return Condition(
            lambda ctx: ctx.hour >= start or ctx.hour <= end,
            f"hour in [{start}, {end}] (overnight)",
        )


def month_in_range(start: int, end: int) -> Condition:
    """月份落在 [start, end] 区间内（跨年用 start > end，如 11,3 表示冬11月-3月）。"""
    if start <= end:
        return Condition(
            lambda ctx: start <= ctx.date.month <= end,
            f"month in [{start}, {end}]",
        )
    else:
        return Condition(
            lambda ctx: ctx.date.month >= start or ctx.date.month <= end,
            f"month in [{start}, {end}] (cross-year)",
        )


def load_above(threshold_kw: float) -> Condition:
    """负荷超过阈值 (kW)。"""
    return Condition(
        lambda ctx: ctx.load_kw > threshold_kw,
        f"load > {threshold_kw} kW",
    )


def load_below(threshold_kw: float) -> Condition:
    """负荷低于阈值 (kW)。"""
    return Condition(
        lambda ctx: ctx.load_kw < threshold_kw,
        f"load < {threshold_kw} kW",
    )


def soc_between(lo: float, hi: float) -> Condition:
    """SoC 在 [lo, hi] 区间内（含边界）。"""
    return Condition(
        lambda ctx: lo <= ctx.soc_pct <= hi,
        f"SoC in [{lo}%, {hi}%]",
    )


def soc_below(pct: float) -> Condition:
    """SoC 低于阈值 (%)。"""
    return Condition(
        lambda ctx: ctx.soc_pct < pct,
        f"SoC < {pct}%",
    )


def soc_above(pct: float) -> Condition:
    """SoC 高于阈值 (%)。"""
    return Condition(
        lambda ctx: ctx.soc_pct > pct,
        f"SoC > {pct}%",
    )


def weather_equals(wtype: str) -> Condition:
    """天气类型等于指定值。"""
    return Condition(
        lambda ctx: ctx.weather_type == wtype,
        f"weather == '{wtype}'",
    )


def renewable_ratio_above(ratio: float) -> Condition:
    """新能源渗透率超过 ratio (如 1.2 表示 PV+Wind > Load×1.2)。"""
    return Condition(
        lambda ctx: ctx.renewable_ratio > ratio,
        f"(PV+Wind)/Load > {ratio}",
    )


def renewable_ratio_below(ratio: float) -> Condition:
    """新能源渗透率低于 ratio (如 0.3 表示 PV+Wind < Load×0.3)。"""
    return Condition(
        lambda ctx: ctx.renewable_ratio < ratio,
        f"(PV+Wind)/Load < {ratio}",
    )


def net_positive() -> Condition:
    """净功率为正 (新能源盈余)。"""
    return Condition(
        lambda ctx: ctx.net_kw > 0,
        "net_kW > 0",
    )


def is_holiday() -> Condition:
    """当前日期为节假日。"""
    return Condition(
        lambda ctx: ctx.is_holiday,
        "is holiday",
    )


def always() -> Condition:
    """始终命中，用于兜底场景。"""
    return Condition(
        lambda ctx: True,
        "always",
    )
