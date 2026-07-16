"""
Main simulation entry point for the charging station microgrid digital twin.

Orchestrates the full digital twin workflow:
    1. Load prediction data (A.csv, B.csv, C.csv) from data/
    2. Compile OpenDSS microgrid model
    3. Run 24-hour time-series simulation with storage dispatch
    4. Save results to output/

Usage:
    python src/main.py [--data data/] [--model model/master.dss]
                       [--output output/] [--strategy simple_balancing]
                       [--init-soc 50] [--diagram]
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure src/ is on the Python path for internal imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data_loader import load_data
from src.storage_strategy import get_strategy
from src.opendss_model import MicrogridModel
from src.output_writer import save_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

# Microgrid rated parameters
PV_RATED_KW = 3000.0
WIND_RATED_KW = 2000.0
LOAD_MAX_KW = 14400.0   # 120 x 120 kW
STORAGE_KW = 3750.0     # PCS rated power
STORAGE_KWH = 7500.0    # Energy capacity
BUS_KV = 0.4
FREQ_HZ = 50


def parse_args():
    parser = argparse.ArgumentParser(
        description="Charging Station Microgrid Digital Twin Simulation"
    )
    parser.add_argument("--data", default=None,
                        help="Directory containing A/B/C.csv prediction files")
    parser.add_argument("--model", default=None,
                        help="Path to OpenDSS master file")
    parser.add_argument("--output", default=None,
                        help="Directory for simulation results")
    parser.add_argument("--strategy", default="simple_balancing",
                        help="Storage dispatch strategy name")
    parser.add_argument("--init-soc", type=float, default=50.0,
                        help="Initial storage SOC percentage (default: 50)")
    parser.add_argument("--diagram", action="store_true",
                        help="Generate architecture topology diagram")
    return parser.parse_args()


def main():
    args = parse_args()

    data_dir = args.data or str(_PROJECT_ROOT / "data")
    model_file = args.model or str(_PROJECT_ROOT / "model" / "master.dss")
    output_dir = args.output or str(_PROJECT_ROOT / "output")

    logger.info("=" * 60)
    logger.info("Charging Station Microgrid Digital Twin Simulation")
    logger.info("=" * 60)

    logger.info("Step 1: Loading prediction data...")
    wind_df, pv_df, load_df = load_data(data_dir)

    logger.info("Step 2: Building OpenDSS model...")
    model = MicrogridModel(base_kv=BUS_KV, base_freq=FREQ_HZ)
    model.compile(model_file)

    logger.info("Step 3: Initializing storage strategy: %s", args.strategy)
    strategy = get_strategy(args.strategy)
    init_soc = max(10.0, min(90.0, args.init_soc))
    storage_soc = init_soc

    logger.info("Step 4: Running 24-hour time-series simulation...")
    results = []

    for i in range(24):
        hour = i
        pv_kw = float(pv_df["power_kW"].iloc[i])
        wind_kw = float(wind_df["power_kW"].iloc[i])
        load_kw = float(load_df["power_kW"].iloc[i])

        if load_kw > LOAD_MAX_KW:
            logger.warning(
                "Hour %d: load %.1f kW exceeds max %.1f kW, clamping",
                hour, load_kw, LOAD_MAX_KW
            )
            load_kw = LOAD_MAX_KW

        storage_kw, storage_soc = strategy.compute(
            pv_kw=pv_kw,
            wind_kw=wind_kw,
            load_kw=load_kw,
            soc_pct=storage_soc,
            rated_capacity_kwh=STORAGE_KWH,
            rated_power_kw=STORAGE_KW,
            time_hour=hour,
        )

        model.set_power(pv_kw, wind_kw, load_kw, storage_kw)
        model.solve()

        bus_voltage = model.get_main_bus_voltage()
        source_kw = model.get_source_power_kw()
        losses_kw = model.get_line_losses_kw()
        net_balance = pv_kw + wind_kw - load_kw + storage_kw - source_kw - losses_kw

        results.append({
            "hour": hour,
            "pv_kW": pv_kw,
            "wind_kW": wind_kw,
            "load_kW": load_kw,
            "storage_kW": storage_kw,
            "source_kW": source_kw,
            "losses_kW": losses_kw,
            "net_balance_kW": net_balance,
            "bus_voltage_pu": bus_voltage,
            "soc_pct": storage_soc,
        })

        logger.info(
            "Hour %2d | PV=%6.1f Wind=%6.1f Load=%6.1f | "
            "Stor=%7.1f SOC=%5.1f%% | Vbus=%5.3f pu | Src=%6.2f kW",
            hour, pv_kw, wind_kw, load_kw,
            storage_kw, storage_soc, bus_voltage, source_kw,
        )

    logger.info("Step 5: Saving results...")
    save_results(results, output_dir, init_soc)

    logger.info("Step 6: Simulation summary...")
    total_gen = sum(r["pv_kW"] + r["wind_kW"] for r in results)
    total_load = sum(r["load_kW"] for r in results)
    total_losses = sum(r["losses_kW"] for r in results)
    total_source = sum(abs(r["source_kW"]) for r in results)
    logger.info("  Total generation (kWh):  %.1f", total_gen)
    logger.info("  Total load (kWh):        %.1f", total_load)
    logger.info("  Total losses (kWh):      %.3f", total_losses)
    logger.info("  Total source exchange (kWh): %.3f", total_source)
    logger.info("  Initial SOC:             %.1f %%", init_soc)
    logger.info("  Final SOC:               %.1f %%", storage_soc)

    if args.diagram:
        logger.info("Step 7: Generating architecture diagram...")
        from src.diagram import generate_diagram
        generate_diagram(output_dir)

    logger.info("=" * 60)
    logger.info("Simulation complete. Results saved to: %s", output_dir)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
