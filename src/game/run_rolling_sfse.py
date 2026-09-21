"""滚动 SFSE 小算例入口。

该入口模拟连续三个滚动时刻：每次只执行当前时刻动作，下一次用
新的当前 EV 负荷和 SOC 重新构造未来窗口。预测模块可将同样的
ForecastBundle 替换为真实接口输出。
"""

from datetime import datetime, timedelta

from src.game.forecast import ForecastBundle
from src.game.rolling import RollingSFSEController


def make_bundle(step: int, soc: float, current_ev: float) -> ForecastBundle:
    return ForecastBundle(
        timestamp=datetime(2024, 1, 1) + timedelta(hours=step),
        horizon_hours=6,
        pv_forecast_kw=[0, 50, 300, 500, 200, 0],
        wind_forecast_kw=[150, 120, 80, 60, 100, 180],
        base_ev_load_forecast_kw=[30, 40, 60, 80, 50, 20],
        non_ev_load_forecast_kw=[80, 80, 90, 100, 120, 100],
        current_ev_load_kw=current_ev,
        soc_current=soc,
        soh_current=1.0,
        reserve_kwh=100.0,
        history={"previous_soc": [soc], "previous_ev_load": [current_ev]},
    )


def main():
    controller = RollingSFSEController(
        method="bri",
        max_sweeps=2,
        flexibility=0.2,
        response_value_per_kwh=2.0,
    )
    soc = 0.5
    current_ev = 30.0
    for step in range(3):
        decision = controller.step(make_bundle(step, soc, current_ev))
        safe = decision.horizon_result["safe"]
        soc = safe["soc"][1]
        current_ev = decision.ev_load_kw
        print(
            f"t={decision.timestamp.isoformat()} "
            f"price={decision.price:.2f} "
            f"storage={decision.storage_kw:.2f}kW "
            f"ev={decision.ev_load_kw:.2f}kW "
            f"soc={soc:.3f} feasible={decision.feasible}"
        )


if __name__ == "__main__":
    main()
