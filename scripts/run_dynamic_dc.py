"""运行直流微网动态 SOC 滚动主从博弈示例。

时间步长 1 h，滚动窗口 6 h，逐小时前移。
负荷量级采用真实高速服务区观测尺度（小时峰值约 226 kW），
EV 上限由 8 桩 x 60 kW = 480 kW 约束。
"""

from datetime import datetime, timedelta
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.game.forecast import ForecastBundle
from src.game.dynamic_dc import DynamicDCSFSEController


# 观测尺度小时负荷（峰值约 226 kW，C 站水平）
EV_24H = [118, 92, 88, 71, 69, 69, 75, 90, 110, 129, 155, 194,
          229, 221, 212, 217, 224, 217, 204, 193, 174, 166, 145, 136]
# 典型光伏日出力（400 kW 装机）
PV_24H = [0, 0, 0, 0, 0, 10, 60, 150, 250, 330, 380, 400,
          390, 350, 290, 210, 120, 45, 10, 0, 0, 0, 0, 0]
# 典型风电出力（100 kW 装机）
WIND_24H = [60, 62, 65, 63, 58, 52, 45, 38, 32, 28, 25, 25,
            28, 32, 38, 45, 52, 58, 65, 68, 66, 64, 62, 61]
NON_EV_24H = [60.0] * 24


def _window(series, start, length):
    chunk = list(series[start:start + length])
    return chunk + [0.0] * (length - len(chunk))


def main():
    controller = DynamicDCSFSEController(horizon_hours=6, max_sweeps=1)
    controller.reset(soc=0.5)
    start = datetime(2024, 1, 1)
    for hour in range(24):
        window = 6
        bundle = ForecastBundle(
            timestamp=start + timedelta(hours=hour),
            horizon_hours=window,
            pv_forecast_kw=_window(PV_24H, hour, window),
            wind_forecast_kw=_window(WIND_24H, hour, window),
            base_ev_load_forecast_kw=_window(EV_24H, hour, window),
            non_ev_load_forecast_kw=_window(NON_EV_24H, hour, window),
            current_ev_load_kw=EV_24H[hour],
            soc_current=controller.soc.soc,
            soh_current=controller.soh,
        )
        decision = controller.step(bundle)
        print(
            f"{decision.timestamp:%H:%M} price={decision.price:.2f} "
            f"P_BESS={decision.storage_kw:7.2f}kW "
            f"EV={decision.ev_served_kw:6.1f}/{decision.ev_requested_kw:6.1f}kW "
            f"shed={decision.ev_shed_kw:5.1f}kW SOC={decision.soc:.3f} "
            f"loss={decision.power_flow['losses_kw']:.2f}kW"
        )


if __name__ == "__main__":
    main()
