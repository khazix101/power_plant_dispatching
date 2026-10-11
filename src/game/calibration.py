"""读取真实 EV 标定产物并转换为滚动调度输入。

该模块只读取 ``Dataset/processed/ev_calibration``，不读取原始大 CSV。
基础负荷和用户群参数来自真实交易；价格响应系数仍需注明其观测关联属性。
"""

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


def calibration_dir(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[2]
    return root / "Dataset" / "processed" / "ev_calibration"


def load_hourly_base_ev_load(
    location: str,
    date: str,
    root: Optional[Path] = None,
) -> List[float]:
    """读取某服务区某日的 24 小时基础 EV 负荷 (kW 平均值)。"""
    path = calibration_dir(root) / "expressway_hourly_ev_load.csv"
    frame = pd.read_csv(path)
    subset = frame[(frame["location"] == location) & (frame["date"] == date)]
    values = [0.0] * 24
    for row in subset.itertuples(index=False):
        hour = int(row.hour)
        if 0 <= hour < 24:
            values[hour] += float(row.normal_energy_kwh)
    return values


def load_user_group_parameters(root: Optional[Path] = None) -> pd.DataFrame:
    """读取真实交易标定的用户群参数。"""
    path = calibration_dir(root) / "ev_user_group_parameters.csv"
    return pd.read_csv(path)


def load_calibration_summary(root: Optional[Path] = None) -> Dict:
    """读取样本数、异常率和价格响应观测结果。"""
    import json

    path = calibration_dir(root) / "calibration_summary.json"
    with path.open(encoding="utf-8") as file:
        return json.load(file)
