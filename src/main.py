"""
充电站微电网数字孪生仿真主入口。

协调完整的数字孪生工作流：
    1. 从 data/ 加载预测数据 (A/B/C.csv) + 天气 (D.csv) + 节假日 (E.csv)
    2. 编译 OpenDSS 微电网模型
    3. 构建场景调度器 (ScenarioDispatcher)，按场景匹配策略流水线
    4. 运行 24 小时时序仿真，每小时通过调度器决策储能功率
    5. 电池资产经济评价：日历老化 + 循环老化 → 日电池使用成本
    6. 全系统经济评价：收入/成本/日净利润
    7. 将结果保存到 output/

用法：
    python src/main.py [--data data/] [--model model/master.dss]
                       [--output output/] [--strategy simple_balancing]
                       [--init-soc 50] [--date YYYY-MM-DD] [--diagram]
                       [--ndays N] [--init-soh 100]
"""

import argparse
import logging
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

# 确保 src/ 在 Python 路径中，以便内部导入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.loader import load_data, load_weather, load_holidays, get_simulation_date
from src.model.opendss import MicrogridModel
from src.output.writer import save_results, save_economic_results
from src.strategy import ScenarioDispatcher, DispatchContext, build_scenarios
from src.strategy.mpc import EconomicMPCStrategy
from src.battery.group import BatteryGroupManager
from src.battery.asset_eval import (
    evaluate_battery_aging,
    update_group_soh_after_day,
    C_NEW_DEFAULT,
    GAMMA_DEFAULT,
    ALPHA_DEFAULT,
)
from src.battery.economic_eval import (
    evaluate_daily_economics,
    format_economic_summary,
    PRICE_EV,
    PRICE_BUY,
    PRICE_SELL,
    PRICE_CURT,
)

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
                        help="包含 A/B/C/D/E.csv 数据文件的目录")
    parser.add_argument("--model", default=None,
                        help="OpenDSS 主文件路径")
    parser.add_argument("--output", default=None,
                        help="仿真结果输出目录")
    parser.add_argument("--strategy", default="simple_balancing",
                        help="储能调度策略名称。可用: simple_balancing / peak_shaving / "
                             "valley_filling / renewable_absorb / summer_peak / holiday / "
                             "mpc_grid13_sb (需加载预测数据)")
    parser.add_argument("--init-soc", type=float, default=50.0,
                        help="初始储能 SoC 百分比 (默认: 50)")
    parser.add_argument("--init-soh", type=float, default=100.0,
                        help="初始储能 SOH 百分比 (默认: 100)")
    parser.add_argument("--date", default=None,
                        help="仿真日期 YYYY-MM-DD (可选，默认从数据文件 valid_time 推导)")
    parser.add_argument("--diagram", action="store_true",
                        help="生成架构拓扑示意图")
    parser.add_argument("--ndays", type=int, default=1,
                        help="多日仿真天数 (默认: 1)，使用同日数据重复仿真")
    parser.add_argument("--no-economic", action="store_true",
                        help="禁用电池资产经济评价")
    return parser.parse_args()


def main():
    args = parse_args()

    data_dir = args.data or str(_PROJECT_ROOT / "data")
    model_file = args.model or str(_PROJECT_ROOT / "model" / "master.dss")
    output_dir = args.output or str(_PROJECT_ROOT / "output")

    logger.info("=" * 60)
    logger.info("充电站微电网数字孪生仿真")
    logger.info("=" * 60)

    # ---------------------------------------------------------------
    #  步骤 1: 加载数据
    # ---------------------------------------------------------------
    logger.info("步骤 1: 加载预测数据...")
    wind_df, pv_df, load_df = load_data(data_dir)
    sim_date = get_simulation_date(load_df, args.date)
    logger.info("  仿真日期: %s", sim_date.isoformat())

    logger.info("  加载天气数据 (D.csv)...")
    weather_data = load_weather(data_dir)

    logger.info("  加载节假日数据 (E.csv)...")
    holidays = load_holidays(data_dir)

    # ---------------------------------------------------------------
    #  步骤 2: 构建 OpenDSS 模型
    # ---------------------------------------------------------------
    logger.info("步骤 2: 构建 OpenDSS 模型...")
    model = MicrogridModel(base_kv=BUS_KV, base_freq=FREQ_HZ)
    model.compile(model_file)

    # ---------------------------------------------------------------
    #  步骤 3: 初始化电池组管理器 (MPC 策略构造前需要 SOH 引用)
    # ---------------------------------------------------------------
    init_soc = max(10.0, min(90.0, args.init_soc))
    init_soh_pct = max(70.0, min(100.0, args.init_soh))
    init_soh = init_soh_pct / 100.0

    group_mgr = BatteryGroupManager(
        total_batteries=1,
        eb_kwh=STORAGE_KWH,
        init_soh=[init_soh],
    )
    logger.info("  电池组状态: SOH=%.2f%%, 初始组=%s",
                init_soh_pct,
                group_mgr.groups[group_mgr.get_active_group_idx()].name)

    # ---------------------------------------------------------------
    #  步骤 4: 构建场景调度器或固定策略
    # ---------------------------------------------------------------
    force_strategy = args.strategy if args.strategy != "simple_balancing" else None

    if force_strategy and force_strategy.startswith("mpc"):
        if force_strategy != "mpc_grid13_sb":
            logger.warning("未知 MPC 策略 '%s'，回退到 mpc_grid13_sb", force_strategy)
            force_strategy = "mpc_grid13_sb"
        logger.info("步骤 4: 使用 MPC 策略: %s (场景调度已禁用)", force_strategy)
        strategy = EconomicMPCStrategy(
            forecast_pv=[float(v) for v in pv_df["power_kW"]],
            forecast_wind=[float(v) for v in wind_df["power_kW"]],
            forecast_load=[float(v) for v in load_df["power_kW"]],
            group_mgr=group_mgr,
            eb_kwh=STORAGE_KWH,
            c_new=C_NEW_DEFAULT,
            gamma=GAMMA_DEFAULT,
            alpha=ALPHA_DEFAULT,
        )
        dispatcher = None
    elif force_strategy:
        from src.strategy.storage_strategy import get_strategy
        logger.info("步骤 4: 使用固定策略: %s (场景调度已禁用)", force_strategy)
        strategy = get_strategy(force_strategy)
        dispatcher = None
    else:
        logger.info("步骤 4: 构建场景调度器...")
        scenarios = build_scenarios()
        dispatcher = ScenarioDispatcher(scenarios=scenarios)
        logger.info("  已加载 %d 个场景", len(scenarios))

    ndays = max(1, args.ndays)
    cumulative_profit = 0.0
    all_daily_results = []
    all_economic_results = []

    for day_idx in range(ndays):
        if ndays > 1:
            logger.info("-" * 40)
            logger.info("第 %d / %d 日仿真", day_idx + 1, ndays)

        current_date = sim_date + timedelta(days=day_idx) if isinstance(sim_date, date) else sim_date
        storage_soc = init_soc
        is_holiday = current_date in holidays

        # ---------------------------------------------------------------
        #  步骤 5: 运行 24 小时时序仿真
        # ---------------------------------------------------------------
        logger.info("步骤 5: 运行 24 小时时序仿真...")
        results = []

        active_g_idx = group_mgr.get_active_group_idx()
        if active_g_idx is None:
            logger.warning("无可用电池组，跳过仿真")
            break

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

            if dispatcher is not None:
                ctx = DispatchContext(
                    hour=hour,
                    date=current_date if isinstance(current_date, date)
                         else datetime.strptime(str(current_date), "%Y-%m-%d").date(),
                    pv_kw=pv_kw,
                    wind_kw=wind_kw,
                    load_kw=load_kw,
                    soc_pct=storage_soc,
                    temperature_c=weather_data[i]["temperature_c"],
                    weather_type=weather_data[i]["weather_type"],
                    is_holiday=is_holiday,
                    rated_capacity_kwh=STORAGE_KWH,
                    rated_power_kw=STORAGE_KW,
                )
                storage_kw, storage_soc = dispatcher.dispatch(ctx)
            else:
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

            # 记录当前激活组的 SOC
            active_g_idx = group_mgr.get_active_group_idx()
            if active_g_idx is not None:
                group_mgr.record_soc(active_g_idx, storage_soc)

            logger.info(
                "第 %2d 小时 | 光伏=%6.1f 风电=%6.1f 负荷=%6.1f | "
                "储能=%7.1f SoC=%5.1f%% | 母线电压=%5.3f pu | 电源=%6.2f kW",
                hour, pv_kw, wind_kw, load_kw,
                storage_kw, storage_soc, bus_voltage, source_kw,
            )

        # ---------------------------------------------------------------
        #  步骤 6: 电池资产经济评价
        # ---------------------------------------------------------------
        if not args.no_economic:
            logger.info("步骤 6: 电池资产经济评价...")

            hourly_storage_kw = [r["storage_kW"] for r in results]
            hourly_soc_pct = [r["soc_pct"] for r in results]

            aging_results, battery_cost = evaluate_battery_aging(
                group_mgr=group_mgr,
                hourly_storage_kw=hourly_storage_kw,
                hourly_soc_pct=hourly_soc_pct,
                eb_kwh=STORAGE_KWH,
                c_new=C_NEW_DEFAULT,
                gamma=GAMMA_DEFAULT,
                alpha=ALPHA_DEFAULT,
            )

            update_group_soh_after_day(group_mgr, aging_results)

            migration_events = group_mgr.check_migration(
                c_new=C_NEW_DEFAULT,
                c_salvage_ratio=0.05,
            )
            for evt in migration_events:
                if evt["type"] == "retirement":
                    cumulative_profit -= evt["cost_yuan"]

            eco_result = evaluate_daily_economics(
                hourly_results=results,
                group_mgr=group_mgr,
                aging_results=aging_results,
                battery_cost_yuan=battery_cost,
                cumulative_profit=cumulative_profit,
                cumulative_replace=group_mgr.total_replace_cost,
                sim_date=str(current_date),
            )
            cumulative_profit = eco_result.cumulative_profit_yuan

            logger.info(format_economic_summary(eco_result))
            all_economic_results.append(eco_result)
        else:
            aging_results = []
            battery_cost = 0.0
            eco_result = None

        group_mgr.clear_daily_records()
        all_daily_results.append(results)

        logger.info(
            "第 %d 日完成 | SoC 终值=%.1f%% | SOH=%.2f%%",
            day_idx + 1, storage_soc,
            group_mgr.soh_system * 100,
        )

    # ---------------------------------------------------------------
    #  步骤 7: 保存结果
    # ---------------------------------------------------------------
    logger.info("步骤 7: 保存结果...")
    save_results(all_daily_results[-1], output_dir, init_soc)

    if not args.no_economic and all_economic_results:
        save_economic_results(all_economic_results, group_mgr, output_dir)

    # ---------------------------------------------------------------
    #  步骤 8: 仿真汇总
    # ---------------------------------------------------------------
    logger.info("步骤 8: 仿真汇总...")
    final_results = all_daily_results[-1]
    total_gen = sum(r["pv_kW"] + r["wind_kW"] for r in final_results)
    total_load = sum(r["load_kW"] for r in final_results)
    total_losses = sum(r["losses_kW"] for r in final_results)
    total_source = sum(abs(r["source_kW"]) for r in final_results)
    logger.info("  总发电量 (kWh):  %.1f", total_gen)
    logger.info("  总负荷 (kWh):    %.1f", total_load)
    logger.info("  总损耗 (kWh):    %.3f", total_losses)
    logger.info("  总电源交换 (kWh): %.3f", total_source)
    logger.info("  初始 SoC:        %.1f %%", init_soc)
    logger.info("  最终 SoC:        %.1f %%", storage_soc)
    logger.info("  当前 SOH:        %.2f %%", group_mgr.soh_system * 100)

    sync_summary_group_info(group_mgr, output_dir)

    if ndays > 1:
        logger.info("  累计净利润:      %,.2f 元", cumulative_profit)
        logger.info("  累计置换支出:    %,.2f 元", group_mgr.total_replace_cost)
        logger.info("  退役触发次数:    %d", group_mgr.retirement_count)

    if args.diagram:
        logger.info("步骤 9: 生成架构拓扑示意图...")
        from src.utils.diagram import generate_diagram
        generate_diagram(output_dir)

    logger.info("=" * 60)
    logger.info("仿真完成。结果已保存至: %s", output_dir)
    logger.info("=" * 60)


def sync_summary_group_info(group_mgr, output_dir: str):
    """将电池组 SOH 信息同步到 summary.csv。"""

    import pandas as pd

    summary_path = Path(output_dir) / "summary.csv"
    if not summary_path.exists():
        return

    try:
        df = pd.read_csv(summary_path)
        group_rows = pd.DataFrame([
            {"metric": "全系统 SOH (%)", "value": f"{group_mgr.soh_system * 100:.2f}"},
            {"metric": "可用储能容量 (kWh)", "value": f"{group_mgr.usable_capacity_kwh:.2f}"},
            {"metric": "退役次数", "value": str(group_mgr.retirement_count)},
        ])
        for g in group_mgr.groups:
            if g.n_batteries > 0:
                group_rows = pd.concat([group_rows, pd.DataFrame([
                    {"metric": f"组 {g.name} SOH (%)", "value": f"{g.soh_avg * 100:.2f}"},
                    {"metric": f"组 {g.name} 电池数", "value": str(g.n_batteries)},
                ])], ignore_index=True)

        df = pd.concat([df, group_rows], ignore_index=True)
        df.to_csv(summary_path, index=False)
    except Exception as exc:
        logger.warning("同步 summary 时出错: %s", exc)


if __name__ == "__main__":
    main()
