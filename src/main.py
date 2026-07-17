"""
充电站微电网数字孪生仿真主入口。

协调完整的数字孪生工作流：
    1. 从 data/ 加载预测数据 (A.csv, B.csv, C.csv)
    2. 编译 OpenDSS 微电网模型
    3. 运行 24 小时时序仿真，含储能调度
    4. 将结果保存到 output/

用法：
    python src/main.py [--data data/] [--model model/master.dss]
                       [--output output/] [--strategy simple_balancing]
                       [--init-soc 50] [--diagram]
"""

import argparse
import logging
import sys
from pathlib import Path

# 确保 src/ 在 Python 路径中，以便内部导入
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

# 微电网额定参数
PV_RATED_KW = 3000.0
WIND_RATED_KW = 2000.0
LOAD_MAX_KW = 14400.0   # 120台充电桩 x 120 kW
STORAGE_KW = 3750.0     # 储能变流器额定功率
STORAGE_KWH = 7500.0    # 储能容量
BUS_KV = 0.4
FREQ_HZ = 50


def parse_args():
    parser = argparse.ArgumentParser(
        description="充电站微电网数字孪生仿真"
    )
    parser.add_argument("--data", default=None,
                        help="包含 A/B/C.csv 预测文件的目录")
    parser.add_argument("--model", default=None,
                        help="OpenDSS 主文件路径")
    parser.add_argument("--output", default=None,
                        help="仿真结果输出目录")
    parser.add_argument("--strategy", default="simple_balancing",
                        help="储能调度策略名称")
    parser.add_argument("--init-soc", type=float, default=50.0,
                        help="初始储能 SoC 百分比 (默认: 50)")
    parser.add_argument("--diagram", action="store_true",
                        help="生成架构拓扑示意图")
    return parser.parse_args()


def main():
    args = parse_args()

    data_dir = args.data or str(_PROJECT_ROOT / "data")
    model_file = args.model or str(_PROJECT_ROOT / "model" / "master.dss")
    output_dir = args.output or str(_PROJECT_ROOT / "output")

    logger.info("=" * 60)
    logger.info("充电站微电网数字孪生仿真")
    logger.info("=" * 60)

    logger.info("步骤 1: 加载预测数据...")
    wind_df, pv_df, load_df = load_data(data_dir)

    logger.info("步骤 2: 构建 OpenDSS 模型...")
    model = MicrogridModel(base_kv=BUS_KV, base_freq=FREQ_HZ)
    model.compile(model_file)

    logger.info("步骤 3: 初始化储能策略: %s", args.strategy)
    strategy = get_strategy(args.strategy)
    init_soc = max(10.0, min(90.0, args.init_soc))
    storage_soc = init_soc

    logger.info("步骤 4: 运行 24 小时时序仿真...")
    results = []

    for i in range(24):
        hour = i
        pv_kw = float(pv_df["power_kW"].iloc[i])
        wind_kw = float(wind_df["power_kW"].iloc[i])
        load_kw = float(load_df["power_kW"].iloc[i])

        if load_kw > LOAD_MAX_KW:
            logger.warning(
                "第 %d 小时: 负荷 %.1f kW 超过最大值 %.1f kW, 已限幅",
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
            "第 %2d 小时 | 光伏=%6.1f 风电=%6.1f 负荷=%6.1f | "
            "储能=%7.1f SoC=%5.1f%% | 母线电压=%5.3f pu | 电源=%6.2f kW",
            hour, pv_kw, wind_kw, load_kw,
            storage_kw, storage_soc, bus_voltage, source_kw,
        )

    logger.info("步骤 5: 保存结果...")
    save_results(results, output_dir, init_soc)

    logger.info("步骤 6: 仿真汇总...")
    total_gen = sum(r["pv_kW"] + r["wind_kW"] for r in results)
    total_load = sum(r["load_kW"] for r in results)
    total_losses = sum(r["losses_kW"] for r in results)
    total_source = sum(abs(r["source_kW"]) for r in results)
    logger.info("  总发电量 (kWh):  %.1f", total_gen)
    logger.info("  总负荷 (kWh):    %.1f", total_load)
    logger.info("  总损耗 (kWh):    %.3f", total_losses)
    logger.info("  总电源交换 (kWh): %.3f", total_source)
    logger.info("  初始 SoC:        %.1f %%", init_soc)
    logger.info("  最终 SoC:        %.1f %%", storage_soc)

    if args.diagram:
        logger.info("步骤 7: 生成架构拓扑示意图...")
        from src.diagram import generate_diagram
        generate_diagram(output_dir)

    logger.info("=" * 60)
    logger.info("仿真完成。结果已保存至: %s", output_dir)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
