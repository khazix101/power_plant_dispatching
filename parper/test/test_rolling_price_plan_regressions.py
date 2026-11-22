"""Regression tests for policy price plans through rolling execution."""

from datetime import datetime
from unittest.mock import patch

from src.game.forecast import ForecastBundle
from src.game.rolling import RollingSFSEController


def _bundle():
    return ForecastBundle(
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


def test_price_plan_path_matches_existing_solver_execution_for_same_plan():
    bundle = _bundle()
    controller = RollingSFSEController(method="bri", max_sweeps=0)
    price_plan = [1.5, 1.7, 1.9]
    planned = controller.step_with_prices(bundle, price_plan)
    with patch("src.game.rolling.solve_equilibrium_bri",
               return_value=planned.horizon_result):
        solved = controller.step(bundle)

    assert planned.price == solved.price
    assert planned.storage_kw == solved.storage_kw
    assert planned.ev_load_kw == solved.ev_load_kw
    assert planned.total_load_kw == solved.total_load_kw
    assert planned.feasible == solved.feasible
    assert planned.horizon_result["safe"] == solved.horizon_result["safe"]
    assert planned.method == "policy_price_plan"


def test_price_plan_path_converts_half_hour_first_slot_energy_to_kw():
    bundle = _bundle()
    controller = RollingSFSEController(method="bri", max_sweeps=0)
    build_case = controller._build_case

    def build_half_hour_case(input_bundle):
        case = build_case(input_bundle)
        case.interval_hours = [0.5, 1.0, 1.0]
        return case

    controller._build_case = build_half_hour_case
    decision = controller.step_with_prices(bundle, [1.5, 1.7, 1.9])
    safe = decision.horizon_result["safe"]
    flexible_kwh = sum(values[0] for values in safe["ev"].values())
    assert abs(decision.ev_load_kw - (10.0 + flexible_kwh / 0.5)) <= 1e-6
    assert abs(decision.total_load_kw - (80.0 + decision.ev_load_kw)) <= 1e-6


def test_price_plan_rejects_wrong_horizon_and_non_grid_values():
    controller = RollingSFSEController(method="bri", max_sweeps=0)
    try:
        controller.step_with_prices(_bundle(), [1.5, 1.7])
    except ValueError as exc:
        assert "长度" in str(exc)
    else:
        raise AssertionError("price plan with wrong horizon must be rejected")

    try:
        controller.step_with_prices(_bundle(), [1.5, 1.71, 1.9])
    except ValueError as exc:
        assert "price_levels" in str(exc)
    else:
        raise AssertionError("price outside the discrete grid must be rejected")
