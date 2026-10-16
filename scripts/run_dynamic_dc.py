"""运行直流微网动态 SOC 滚动主从博弈示例。"""

from datetime import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.game.dynamic_dc import DynamicDCSFSEController


def main():
    controller = DynamicDCSFSEController(horizon_hours=6, max_sweeps=1)
    controller.reset(soc=0.5)
    for step in range(6):
        decision = controller.step(
            datetime(2024, 1, 1, step),
            pv_forecast_kw=[200, 300, 400, 350, 150, 0],
            wind_forecast_kw=[50, 50, 50, 50, 50, 50],
            ev_base_forecast_kw=[180, 220, 200, 160, 120, 80],
            non_ev_forecast_kw=[50, 50, 50, 50, 50, 50],
        )
        print(
            f"{decision.timestamp.isoformat()} "
            f"price={decision.price:.2f} "
            f"P_BESS={decision.storage_kw:.2f}kW "
            f"EV={decision.ev_served_kw:.2f}/{decision.ev_requested_kw:.2f}kW "
            f"shed={decision.ev_shed_kw:.2f}kW SOC={decision.soc:.3f}"
        )


if __name__ == "__main__":
    main()
