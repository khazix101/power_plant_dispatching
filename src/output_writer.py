"""
Output writer for microgrid simulation results.

Writes time-series simulation results to CSV files in the output directory,
including power balance, bus voltages, storage state, and summary statistics.
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
    Save simulation results to CSV files.

    Args:
        results: List of dicts, one per time step, with keys matching
                 OUTPUT_COLUMNS
        output_dir: Path to output directory
        init_soc: Initial SOC before first time step (optional)

    Generates:
        output/power_balance.csv  - full time-series
        output/storage_state.csv  - storage SOC and power
        output/summary.csv        - aggregated statistics
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(results)

    _save_power_balance(df, output_path)
    _save_storage_state(df, output_path)
    _save_summary(df, output_path, init_soc)

    logger.info(f"Results saved to: {output_path}")


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
        {"metric": "Total PV generation (kWh)", "value": f"{total_pv:.2f}"},
        {"metric": "Total Wind generation (kWh)", "value": f"{total_wind:.2f}"},
        {"metric": "Total Load consumption (kWh)", "value": f"{total_load:.2f}"},
        {"metric": "Total Line losses (kWh)", "value": f"{total_losses:.2f}"},
        {"metric": "Initial storage SOC (%)", "value": f"{init_soc:.2f}"},
        {"metric": "Final storage SOC (%)", "value": f"{soc_final:.2f}"},
        {"metric": "SOC change (%)", "value": f"{soc_final - init_soc:.2f}"},
        {"metric": "Load cap violations (>14400 kW)", "value": str(int(
            (df["load_kW"] > 14400).sum() if "load_kW" in df.columns else 0
        ))},
    ])

    summary.to_csv(output_path / "summary.csv", index=False)
