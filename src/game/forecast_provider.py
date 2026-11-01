"""ForecastBundle providers used by the DC rolling controller.

The provider is deliberately small: model training stays in ``prediction/``;
this module owns the runtime contract and converts a daily CSV or a callable
model into the arrays consumed by the scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

import pandas as pd

from src.data.loader import load_data
from src.game.forecast import ForecastBundle


class ForecastProvider(Protocol):
    def bundle(self, timestamp: datetime, soc: float, soh: float,
               current_ev_load_kw: float = 0.0) -> ForecastBundle:
        """Return one rolling forecast window."""


def _window(values: List[float], start: int, horizon: int) -> List[float]:
    if not values:
        return [0.0] * horizon
    return [float(values[(start + i) % len(values)]) for i in range(horizon)]


# 400 kW 光伏与 100 kW 风电的典型日出力（阶段性假设，待预测模块替换）
_TYPICAL_PV_400 = [0, 0, 0, 0, 0, 10, 60, 150, 250, 330, 380, 400,
                   390, 350, 290, 210, 120, 45, 10, 0, 0, 0, 0, 0]
_TYPICAL_WIND_100 = [60, 62, 65, 63, 58, 52, 45, 38, 32, 28, 25, 25,
                     28, 32, 38, 45, 52, 58, 65, 68, 66, 64, 62, 61]


class CalibratedForecastProvider:
    """用真实交易标定的基础 EV 负荷驱动滚动调度。

    EV 负荷取 ``Dataset/processed/ev_calibration`` 中指定站点、指定日期的
    ``normal_energy_kwh``（单位 kWh/h，数值上等于该小时平均功率 kW）。
    PV/Wind 暂用 400/100 kW 典型曲线，属阶段性假设，须在论文中标注；
    接入预测模块后应替换为本模块的 ``pv``/``wind`` 输入。
    """

    def __init__(self, site="Expressway Service District C", date=None,
                 horizon_hours=24, non_ev_load_kw=60.0,
                 calibration_dir=None, root=None,
                 pv=None, wind=None, non_ev_load_kw_series=None):
        root = Path(root) if root else Path(__file__).resolve().parents[2]
        calib = Path(calibration_dir) if calibration_dir else (
            root / "Dataset" / "processed" / "ev_calibration"
        )
        frame = pd.read_csv(calib / "expressway_hourly_ev_load.csv")
        frame = frame[frame["location"] == site]
        if frame.empty:
            raise ValueError(f"标定数据中没有站点: {site}")
        if date is None:
            daily = frame.groupby("date")["normal_energy_kwh"].sum()
            date = daily.idxmax()
        day = frame[frame["date"].astype(str) == str(date)]
        values = [0.0] * 24
        for row in day.itertuples(index=False):
            values[int(row.hour)] = float(row.normal_energy_kwh)
        self.site = site
        self.date = str(date)
        self.ev_load = values
        self.horizon_hours = horizon_hours
        self.non_ev = (list(non_ev_load_kw_series) if non_ev_load_kw_series
                       else [float(non_ev_load_kw)] * 24)
        self.pv = list(pv) if pv is not None else list(_TYPICAL_PV_400)
        self.wind = list(wind) if wind is not None else list(_TYPICAL_WIND_100)
        self.start = datetime.fromisoformat(str(date))

    def bundle(self, timestamp: datetime, soc: float, soh: float,
               current_ev_load_kw: float = 0.0) -> ForecastBundle:
        offset = int((timestamp - self.start).total_seconds() // 3600) % 24
        return ForecastBundle(
            timestamp=timestamp,
            horizon_hours=self.horizon_hours,
            pv_forecast_kw=_window(self.pv, offset, self.horizon_hours),
            wind_forecast_kw=_window(self.wind, offset, self.horizon_hours),
            base_ev_load_forecast_kw=_window(self.ev_load, offset, self.horizon_hours),
            non_ev_load_forecast_kw=_window(self.non_ev, offset, self.horizon_hours),
            current_ev_load_kw=current_ev_load_kw,
            soc_current=soc,
            soh_current=soh,
            history={"source": ["calibrated_ev", self.site, self.date]},
        )


class DailyCsvForecastProvider:
    """Build rolling windows from the validated A/B/C daily CSV files.

    ``C.csv`` is treated as the base EV demand until a separate non-EV series
    is supplied. This explicit assumption prevents silently mixing total load
    and EV load in the DC controller.
    """

    def __init__(self, data_dir: str | Path = "data", horizon_hours: int = 24,
                 non_ev_load_kw: Optional[List[float]] = None):
        self.data_dir = Path(data_dir)
        self.horizon_hours = horizon_hours
        wind_df, pv_df, load_df = load_data(self.data_dir)
        self.wind = wind_df["power_kW"].astype(float).tolist()
        self.pv = pv_df["power_kW"].astype(float).tolist()
        self.ev_load = load_df["power_kW"].astype(float).tolist()
        self.non_ev = non_ev_load_kw or [0.0] * len(self.ev_load)
        if len(self.non_ev) != len(self.ev_load):
            raise ValueError("non_ev_load_kw 必须与 C.csv 长度一致")
        if "valid_time" in load_df.columns and len(load_df):
            self.start = pd.to_datetime(load_df["valid_time"].iloc[0]).to_pydatetime()
        else:
            self.start = datetime(2024, 1, 1)

    def bundle(self, timestamp: datetime, soc: float, soh: float,
               current_ev_load_kw: float = 0.0) -> ForecastBundle:
        offset = int((timestamp - self.start).total_seconds() // 3600) % len(self.ev_load)
        return ForecastBundle(
            timestamp=timestamp,
            horizon_hours=self.horizon_hours,
            pv_forecast_kw=_window(self.pv, offset, self.horizon_hours),
            wind_forecast_kw=_window(self.wind, offset, self.horizon_hours),
            base_ev_load_forecast_kw=_window(self.ev_load, offset, self.horizon_hours),
            non_ev_load_forecast_kw=_window(self.non_ev, offset, self.horizon_hours),
            current_ev_load_kw=current_ev_load_kw,
            soc_current=soc,
            soh_current=soh,
            history={"source": [str(self.data_dir)]},
        )


class CallableForecastProvider:
    """Adapt a trained prediction function to the runtime contract.

    The callable receives ``(timestamp, horizon_hours)`` and returns a mapping
    with ``pv``, ``wind``, ``ev_load`` and optional ``non_ev_load`` arrays.
    This is the integration point for LightGBM or deep-learning inference.
    """

    def __init__(self, predictor: Callable[[datetime, int], Dict[str, List[float]]],
                 horizon_hours: int = 24):
        self.predictor = predictor
        self.horizon_hours = horizon_hours

    def bundle(self, timestamp: datetime, soc: float, soh: float,
               current_ev_load_kw: float = 0.0) -> ForecastBundle:
        prediction = self.predictor(timestamp, self.horizon_hours)
        required = {"pv", "wind", "ev_load"}
        missing = required - prediction.keys()
        if missing:
            raise ValueError(f"预测结果缺少字段: {sorted(missing)}")
        return ForecastBundle(
            timestamp=timestamp,
            horizon_hours=self.horizon_hours,
            pv_forecast_kw=prediction["pv"],
            wind_forecast_kw=prediction["wind"],
            base_ev_load_forecast_kw=prediction["ev_load"],
            non_ev_load_forecast_kw=prediction.get("non_ev_load", [0.0] * self.horizon_hours),
            current_ev_load_kw=current_ev_load_kw,
            soc_current=soc,
            soh_current=soh,
        )
