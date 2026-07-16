"""
Data loader for microgrid prediction CSV files.

Reads and validates A.csv (wind), B.csv (PV), C.csv (load) from the data
directory, each containing 24 hourly predictions with columns:
    valid_time, power_kW
"""

import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

REQUIRED_HOURS = 24
MAX_LOAD_KW = 14400.0  # 120 chargers x 120 kW


def load_data(data_dir="data"):
    """
    Load and validate all prediction data files.

    Args:
        data_dir: Path to the data directory

    Returns:
        tuple: (wind_df, pv_df, load_df) as pandas DataFrames
    """
    data_path = Path(data_dir)

    wind_df = _read_csv(data_path / "A.csv", "wind")
    pv_df = _read_csv(data_path / "B.csv", "PV")
    load_df = _read_csv(data_path / "C.csv", "load")

    _validate("wind", wind_df)
    _validate("PV", pv_df)
    _validate("load", load_df)

    max_load = load_df["power_kW"].max()
    if max_load > MAX_LOAD_KW:
        raise ValueError(
            f"Load exceeds maximum total charger capacity: "
            f"{max_load:.1f} kW > {MAX_LOAD_KW:.1f} kW"
        )

    logger.info(
        f"Data loaded: wind={len(wind_df)}h, PV={len(pv_df)}h, "
        f"load={len(load_df)}h"
    )
    return wind_df, pv_df, load_df


def _read_csv(filepath, label):
    if not filepath.exists():
        raise FileNotFoundError(
            f"{label} data file not found: {filepath}"
        )
    df = pd.read_csv(filepath)
    if "valid_time" in df.columns:
        df["valid_time"] = pd.to_datetime(df["valid_time"])
    df["power_kW"] = pd.to_numeric(df["power_kW"], errors="coerce")
    if df["power_kW"].isna().any():
        raise ValueError(f"{label} data contains invalid power_kW values")
    return df


def _validate(label, df):
    if len(df) != REQUIRED_HOURS:
        raise ValueError(
            f"{label} data has {len(df)} entries, expected {REQUIRED_HOURS}"
        )
    if (df["power_kW"] < 0).any():
        raise ValueError(f"{label} data contains negative power values")
