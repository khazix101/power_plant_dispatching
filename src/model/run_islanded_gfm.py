"""运行单站物理离网 GFM QSTS 诊断。

示例:
    python -m src.model.run_islanded_gfm --soc 50 --ev-load 100

PCS kVA 必须由设备铭牌给出。当前线路尚无热额定电流时，程序会报告
实测 Iline，并将热限值状态标为未验证。
"""

import argparse
import json
from pathlib import Path

from src.model.opendss import (
    IslandedGFMConfig,
    IslandedGFMQSTS,
    MicrogridModel,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--soc", type=float, default=50.0, help="初始 SOC 百分比")
    parser.add_argument("--pv", type=float, default=200.0, help="光伏可用功率 kW")
    parser.add_argument("--wind", type=float, default=50.0, help="风电可用功率 kW")
    parser.add_argument("--ev-load", type=float, default=180.0, help="EV 负荷 kW")
    parser.add_argument("--ev-load-kvar", type=float, required=True,
                        help="EV 预测负荷无功 kvar，必须显式提供")
    parser.add_argument("--non-ev-load", type=float, default=0.0,
                        help="非EV负荷 kW")
    parser.add_argument("--non-ev-kvar", type=float, default=0.0,
                        help="非EV负荷 kvar")
    parser.add_argument("--pcs-kva", type=float, required=True, help="PCS 铭牌 kVA")
    parser.add_argument("--pcs-q-kvar", type=float, required=True,
                        help="PCS 铭牌最大无功 kvar")
    parser.add_argument("--coupling", choices=["R_gt_X", "X_gt_R"], required=True,
                        help="按馈线等值参数选择下垂耦合类型")
    parser.add_argument("--p-schedule", type=float, default=0.0,
                        help="BESS 计划有功注入 kW，正值放电")
    parser.add_argument("--q-schedule", type=float, default=0.0,
                        help="BESS 计划无功注入 kvar，正值向网络注入")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    dss_path = root / "model" / "islanded_gfm.dss"
    model = MicrogridModel(base_kv=0.4, base_freq=50.0)
    model.compile(dss_path, islanded=True, vtheta_bus="Storage_Bus")

    config = IslandedGFMConfig(
        pcs_kva=args.pcs_kva,
        pcs_q_kvar=args.pcs_q_kvar,
        coupling=args.coupling,
    )
    controller = IslandedGFMQSTS(model, config)
    controller.reset(soc_pct=args.soc)
    result = controller.solve_step(
        pv_kw=args.pv,
        wind_kw=args.wind,
        ev_base_kw=args.ev_load,
        p_bess_schedule_kw=args.p_schedule,
        q_bess_schedule_kvar=args.q_schedule,
        ev_load_kvar=args.ev_load_kvar,
        non_ev_kw=args.non_ev_load,
        non_ev_kvar=args.non_ev_kvar,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
