"""
微电网预测 CSV 数据加载器。

从 data 目录读取并校验以下文件：
  - A.csv (风电)    列: valid_time, power_kW
  - B.csv (光伏)    列: valid_time, power_kW
  - C.csv (负荷)    列: valid_time, power_kW
  - D.csv (天气)    列: valid_time, temperature_c, weather_type
  - E.csv (节假日)  列: date

每个功率文件包含 24 小时预测数据。
"""

import pandas as pd
from pathlib import Path
from datetime import date, datetime
from typing import Optional, Set
import logging

logger = logging.getLogger(__name__)

REQUIRED_HOURS = 24
MAX_LOAD_KW = 14400.0  # 120台充电桩 x 120 kW
VALID_WEATHER_TYPES = {"sunny", "cloudy", "overcast", "rainy", "storm"}


def load_data(data_dir="data"):
    """
    加载并校验所有预测数据文件 (A/B/C.csv)。

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


def load_weather(data_dir="data"):
    """
    从 D.csv 加载 24 小时天气数据。

    列: valid_time, temperature_c, weather_type

    weather_type 有效值: sunny / cloudy / overcast / rainy / storm

    Args:
        data_dir: 数据目录路径

    Returns:
        list[dict]: 24 小时天气记录列表，索引为小时 (0-23)
            每项格式: {"temperature_c": float, "weather_type": str}
    """
    data_path = Path(data_dir)
    filepath = data_path / "D.csv"

    if not filepath.exists():
        logger.warning("天气数据 D.csv 未找到，使用默认值 (sunny, 25°C)")
        return [{"temperature_c": 25.0, "weather_type": "sunny"}
                for _ in range(24)]

    df = pd.read_csv(filepath)

    if len(df) != REQUIRED_HOURS:
        raise ValueError(
            f"天气数据有 {len(df)} 条记录，期望 {REQUIRED_HOURS} 条"
        )

    if "valid_time" in df.columns:
        df["valid_time"] = pd.to_datetime(df["valid_time"])

    df["temperature_c"] = pd.to_numeric(df["temperature_c"], errors="coerce")
    if df["temperature_c"].isna().any():
        raise ValueError("D.csv 包含无效的 temperature_c 值")

    invalid_types = set(df["weather_type"].unique()) - VALID_WEATHER_TYPES
    if invalid_types:
        raise ValueError(
            f"D.csv 包含无效的 weather_type: {invalid_types}。"
            f"有效值: {VALID_WEATHER_TYPES}"
        )

    weather_data = []
    for _, row in df.iterrows():
        weather_data.append({
            "temperature_c": float(row["temperature_c"]),
            "weather_type": str(row["weather_type"]),
        })

    logger.info("天气数据已加载 (%dh), 类型分布: %s",
                len(weather_data),
                pd.Series([w["weather_type"] for w in weather_data]).value_counts().to_dict())
    return weather_data


def load_holidays(data_dir="data") -> Set[date]:
    """
    从 E.csv 加载节假日日历。

    列: date (YYYY-MM-DD 格式)

    Args:
        data_dir: 数据目录路径

    Returns:
        set[date]: 节假日日期集合
    """
    data_path = Path(data_dir)
    filepath = data_path / "E.csv"

    if not filepath.exists():
        logger.warning("节假日数据 E.csv 未找到，视为无节假日")
        return set()

    df = pd.read_csv(filepath)
    holidays = set()

    for _, row in df.iterrows():
        d = pd.to_datetime(row["date"]).date()
        holidays.add(d)

    logger.info("节假日数据已加载: %d 天", len(holidays))
    return holidays


def get_simulation_date(load_df, cli_date: Optional[str] = None) -> date:
    """
    获取仿真日期。

    优先级: CLI --date 参数 > A/B/C.csv 的 valid_time 首行。

    Args:
        load_df: 负荷数据 DataFrame (含 valid_time 列)
        cli_date: CLI 指定的日期字符串 (YYYY-MM-DD)，可选

    Returns:
        datetime.date: 仿真日期
    """
    if cli_date:
        return datetime.strptime(cli_date, "%Y-%m-%d").date()

    if "valid_time" in load_df.columns and len(load_df) > 0:
        return pd.to_datetime(load_df["valid_time"].iloc[0]).date()

    return date.today()


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
