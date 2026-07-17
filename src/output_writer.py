"""
微电网仿真结果输出写入器。

将时序仿真结果写入 output 目录的 CSV 文件，
包括功率平衡、母线电压、储能状态和汇总统计。
"""

import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "hour", "pv_kW", "wind_kW", "load_kW",
    "storage_kW", "source_kW", "losses_kW",
    "net_balance_kW", "bus_voltage_pu", "soc_pct",
]


def save_results(results, output_dir="output", init_soc=None):
    """
    将仿真结果保存到 CSV 文件。

    Args:
        results: 字典列表，每个时间步长一个字典，键与 OUTPUT_COLUMNS 对应
        output_dir: 输出目录路径
        init_soc: 首个时间步长前的初始 SoC (可选)

    生成文件:
        output/power_balance.csv  - 完整时序数据
        output/storage_state.csv  - 储能 SoC 及功率
        output/summary.csv        - 汇总统计
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(results)

    _save_power_balance(df, output_path)
    _save_storage_state(df, output_path)
    _save_summary(df, output_path, init_soc)

    logger.info(f"结果已保存至: {output_path}")


def _save_power_balance(df, output_path):
    cols = ["hour", "pv_kW", "wind_kW", "load_kW",
            "storage_kW", "source_kW", "losses_kW",
            "net_balance_kW", "bus_voltage_pu"]
    available = [c for c in cols if c in df.columns]
    df[available].to_csv(output_path / "power_balance.csv", index=False)


def _save_storage_state(df, output_path):
    cols = ["hour", "soc_pct", "storage_kW"]
    available = [c for c in cols if c in df.columns]
    df[available].to_csv(output_path / "storage_state.csv", index=False)


def _save_summary(df, output_path, init_soc=None):
    total_pv = df["pv_kW"].sum() if "pv_kW" in df.columns else 0
    total_wind = df["wind_kW"].sum() if "wind_kW" in df.columns else 0
    total_load = df["load_kW"].sum() if "load_kW" in df.columns else 0
    total_losses = df["losses_kW"].sum() if "losses_kW" in df.columns else 0
    soc_final = df["soc_pct"].iloc[-1] if "soc_pct" in df.columns and len(df) > 0 else 0

    if init_soc is None:
        init_soc = df["soc_pct"].iloc[0] if "soc_pct" in df.columns and len(df) > 0 else 0

    summary = pd.DataFrame([
        {"metric": "光伏总发电量 (kWh)", "value": f"{total_pv:.2f}"},
        {"metric": "风电总发电量 (kWh)", "value": f"{total_wind:.2f}"},
        {"metric": "总负荷消耗 (kWh)", "value": f"{total_load:.2f}"},
        {"metric": "线路总损耗 (kWh)", "value": f"{total_losses:.2f}"},
        {"metric": "储能初始 SoC (%)", "value": f"{init_soc:.2f}"},
        {"metric": "储能最终 SoC (%)", "value": f"{soc_final:.2f}"},
        {"metric": "SoC 变化量 (%)", "value": f"{soc_final - init_soc:.2f}"},
        {"metric": "负荷超限次数 (>14400 kW)", "value": str(int(
            (df["load_kW"] > 14400).sum() if "load_kW" in df.columns else 0
        ))},
    ])

    summary.to_csv(output_path / "summary.csv", index=False)
