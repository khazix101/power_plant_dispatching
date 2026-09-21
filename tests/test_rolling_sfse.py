from datetime import datetime

from src.game.forecast import ForecastBundle
from src.game.rolling import RollingSFSEController


def make_bundle(current_ev_load_kw=0.0):
    return ForecastBundle(
        timestamp=datetime(2024, 1, 1),
        horizon_hours=6,
        pv_forecast_kw=[0, 50, 300, 500, 200, 0],
        wind_forecast_kw=[150, 120, 80, 60, 100, 180],
        base_ev_load_forecast_kw=[30, 40, 60, 80, 50, 20],
        non_ev_load_forecast_kw=[80, 80, 90, 100, 120, 100],
        current_ev_load_kw=current_ev_load_kw,
        soc_current=0.5,
        soh_current=1.0,
        reserve_kwh=100.0,
    )


def test_forecast_bundle_validates_horizon():
    bundle = make_bundle()
    assert bundle.horizon_hours == 6
    assert len(bundle.total_base_load_kw) == 6


def test_rolling_controller_returns_safe_current_action():
    decision = RollingSFSEController(
        method="bri", max_sweeps=2, flexibility=0.2
    ).step(make_bundle(current_ev_load_kw=30.0))
    assert decision.feasible is True
    assert decision.ev_load_kw >= 30.0
    assert decision.total_load_kw >= decision.ev_load_kw
    assert decision.method == "bri"
