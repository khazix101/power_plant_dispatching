"""
微电网预测 CSV 数据加载器。

从 data 目录读取并校验 A.csv (风电), B.csv (光伏), C.csv (负荷) 文件，
每个文件包含 24 小时预测数据，字段为：
    valid_time, power_kW
"""

import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

REQUIRED_HOURS = 24
MAX_LOAD_KW = 14400.0  # 120台充电桩 x 120 kW


def load_data(data_dir="data"):
    """
    加载并校验所有预测数据文件。

    Args:
        data_dir: 数据目录路径

    Returns:
        tuple: (wind_df, pv_df, load_df) 熊猫 DataFrame 三元组
    """
    data_path = Path(data_dir)

    wind_df = _read_csv(data_path / "A.csv", "风电")
    pv_df = _read_csv(data_path / "B.csv", "光伏")
    load_df = _read_csv(data_path / "C.csv", "负荷")

    _validate("风电", wind_df)
    _validate("光伏", pv_df)
    _validate("负荷", load_df)

    max_load = load_df["power_kW"].max()
    if max_load > MAX_LOAD_KW:
        raise ValueError(
            f"负荷超过充电桩最大总容量: "
            f"{max_load:.1f} kW > {MAX_LOAD_KW:.1f} kW"
        )

    logger.info(
        f"数据已加载: 风电={len(wind_df)}h, 光伏={len(pv_df)}h, "
        f"负荷={len(load_df)}h"
    )
    return wind_df, pv_df, load_df


def _read_csv(filepath, label):
    if not filepath.exists():
        raise FileNotFoundError(
            f"{label} 数据文件未找到: {filepath}"
        )
    df = pd.read_csv(filepath)
    if "valid_time" in df.columns:
        df["valid_time"] = pd.to_datetime(df["valid_time"])
    df["power_kW"] = pd.to_numeric(df["power_kW"], errors="coerce")
    if df["power_kW"].isna().any():
        raise ValueError(f"{label} 数据包含无效的 power_kW 值")
    return df


def _validate(label, df):
    if len(df) != REQUIRED_HOURS:
        raise ValueError(
            f"{label} 数据有 {len(df)} 条记录, 期望 {REQUIRED_HOURS} 条"
        )
    if (df["power_kW"] < 0).any():
        raise ValueError(f"{label} 数据包含负功率值")
