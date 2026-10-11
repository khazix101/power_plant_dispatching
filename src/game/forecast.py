"""滚动调度预测接口。

ForecastBundle 是预测模块与调度模块之间的唯一数据契约：
当前状态 + 历史摘要 + 未来 24 小时供能预测 + 基础充电负荷预测。

这里的基础充电负荷不包含价格响应。价格响应由跟随者模型在滚动
主从博弈中重新计算，避免预测模型与博弈模型重复计算响应。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


def _as_float_list(values, name: str, horizon: int) -> List[float]:
    result = [float(v) for v in values]
    if len(result) != horizon:
        raise ValueError(f"{name} 长度为 {len(result)}，期望 {horizon}")
    if any(v < 0 for v in result):
        raise ValueError(f"{name} 不允许包含负值")
    return result


@dataclass
class ForecastBundle:
    """一次滚动调度所需的状态和预测数据。"""

    timestamp: datetime
    horizon_hours: int
    pv_forecast_kw: List[float]
    wind_forecast_kw: List[float]
    base_ev_load_forecast_kw: List[float]
    non_ev_load_forecast_kw: List[float] = field(default_factory=list)
    current_ev_load_kw: float = 0.0
    base_ev_load_forecast_kvar: Optional[List[float]] = None
    non_ev_load_forecast_kvar: Optional[List[float]] = None

    soc_current: float = 0.5
    soh_current: float = 1.0
    reserve_kwh: float = 0.0
    history: Dict[str, List[float]] = field(default_factory=dict)

    pv_lower_kw: Optional[List[float]] = None
    pv_upper_kw: Optional[List[float]] = None
    wind_lower_kw: Optional[List[float]] = None
    wind_upper_kw: Optional[List[float]] = None
    load_lower_kw: Optional[List[float]] = None
    load_upper_kw: Optional[List[float]] = None

    def __post_init__(self):
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours 必须为正数")
        h = self.horizon_hours
        self.pv_forecast_kw = _as_float_list(self.pv_forecast_kw, "pv_forecast_kw", h)
        self.wind_forecast_kw = _as_float_list(self.wind_forecast_kw, "wind_forecast_kw", h)
        self.base_ev_load_forecast_kw = _as_float_list(
            self.base_ev_load_forecast_kw, "base_ev_load_forecast_kw", h
        )
        if not self.non_ev_load_forecast_kw:
            self.non_ev_load_forecast_kw = [0.0] * h
        self.non_ev_load_forecast_kw = _as_float_list(
            self.non_ev_load_forecast_kw, "non_ev_load_forecast_kw", h
        )
        if self.base_ev_load_forecast_kvar is None:
            self.base_ev_load_forecast_kvar = [0.0] * h
        self.base_ev_load_forecast_kvar = _as_float_list(
            self.base_ev_load_forecast_kvar, "base_ev_load_forecast_kvar", h
        )
        if self.non_ev_load_forecast_kvar is None:
            self.non_ev_load_forecast_kvar = [0.0] * h
        self.non_ev_load_forecast_kvar = _as_float_list(
            self.non_ev_load_forecast_kvar, "non_ev_load_forecast_kvar", h
        )
        if not 0.0 <= self.soc_current <= 1.0:
            raise ValueError("soc_current 必须在 [0, 1] 内")
        if not 0.7 <= self.soh_current <= 1.0:
            raise ValueError("soh_current 必须在 [0.7, 1.0] 内")
        if self.reserve_kwh < 0:
            raise ValueError("reserve_kwh 不允许为负")
        if self.current_ev_load_kw < 0:
            raise ValueError("current_ev_load_kw 不允许为负")

    @property
    def total_base_load_kw(self) -> List[float]:
        """基础负荷 = 非 EV 负荷 + 基础 EV 负荷。"""
        return [
            non_ev + ev
            for non_ev, ev in zip(
                self.non_ev_load_forecast_kw, self.base_ev_load_forecast_kw
            )
        ]
