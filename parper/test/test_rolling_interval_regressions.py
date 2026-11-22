"""Regression tests for rolling decision power/energy unit conversion."""

from datetime import datetime
from unittest.mock import patch

from src.game.forecast import ForecastBundle
from src.game.rolling import RollingSFSEController


def test_rolling_decision_converts_half_hour_ev_energy_to_kw():
    bundle = ForecastBundle(
        timestamp=datetime(2024, 1, 1),
        horizon_hours=3,
        pv_forecast_kw=[0.0, 50.0, 100.0],
        wind_forecast_kw=[150.0, 120.0, 100.0],
        base_ev_load_forecast_kw=[30.0, 40.0, 20.0],
        non_ev_load_forecast_kw=[80.0, 80.0, 80.0],
        current_ev_load_kw=10.0,
        soc_current=0.5,
        soh_current=1.0,
        reserve_kwh=100.0,
    )
    controller = RollingSFSEController(method="bri", max_sweeps=0)
    build_case = controller._build_case

    def build_half_hour_case(input_bundle):
        case = build_case(input_bundle)
        case.interval_hours = [0.5, 1.0, 1.0]
        return case

    controller._build_case = build_half_hour_case
    case = build_half_hour_case(bundle)
    response = {user.name: [0.0] * case.T for user in case.users}
    response[case.users[0].name][0] = case.users[0].energy_max_kwh
    result = {
        "prices": [1.5] * case.T,
        "p_ch": [0.0] * case.T,
        "p_dis": [0.0] * case.T,
        "response": response,
    }
    with patch("src.game.rolling.solve_equilibrium_bri", return_value=result):
        decision = controller.step(bundle)

    safe = decision.horizon_result["safe"]
    first_slot_ev_kwh = sum(values[0] for values in safe["ev"].values())
    assert first_slot_ev_kwh > 0.0
    assert abs(decision.ev_load_kw - (10.0 + first_slot_ev_kwh / 0.5)) <= 1e-6
    assert abs(decision.total_load_kw - (80.0 + decision.ev_load_kw)) <= 1e-6
