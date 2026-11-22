"""Tracked regression tests for the dynamic DC/SOH paper path."""

from datetime import datetime

from src.game.dc_microgrid import DCNetworkConfig
from src.game.dynamic_dc import DynamicDCSFSEController
from src.game.forecast import ForecastBundle
from src.game.forecast_provider import CalibratedForecastProvider
from src.game.case import EVUser, GameCase
from src.game.follower import solve_follower_response


def _bundle(*, elapsed=0.0, measured=100.0, forecast=200.0):
    return ForecastBundle(
        timestamp=datetime(2024, 1, 1),
        horizon_hours=3,
        pv_forecast_kw=[0.0, 0.0, 0.0],
        wind_forecast_kw=[0.0, 0.0, 0.0],
        base_ev_load_forecast_kw=[forecast, 50.0, 50.0],
        non_ev_load_forecast_kw=[0.0, 0.0, 0.0],
        current_ev_load_kw=measured,
        current_ev_elapsed_hours=elapsed,
        soc_current=0.5,
        soh_current=1.0,
    )


def test_ev_limit_is_separate_from_pcs_rating():
    controller = DynamicDCSFSEController(horizon_hours=3, max_sweeps=1)
    assert controller.soc_config.power_kw == 800.0
    assert controller.ev_service_limit_kw == DCNetworkConfig().ev_rated_kw == 960.0


def test_half_hour_interval_arrival_does_not_mix_legacy_observation():
    controller = DynamicDCSFSEController(
        horizon_hours=3, max_sweeps=1, current_flexible_fraction=0.0
    )
    case = controller._build_case(
        [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [200.0, 50.0, 50.0],
        [0.0, 0.0, 0.0], current_ev=100.0,
        dt_hours=0.5,
        current_ev_elapsed_hours=0.5,
    )
    # Slot zero is its own 0.5-hour arrival interval.  ``current_ev`` is a
    # legacy observation field and cannot be added to/subtracted from the
    # price-conditioned interval rate, otherwise two price bases are mixed.
    assert case.base_load[0] == 200.0
    assert case.interval_hours[0] == 0.5


def test_same_price_scaled_half_hour_arrivals_sum_to_hourly_current_energy():
    controller = DynamicDCSFSEController(
        horizon_hours=3, max_sweeps=1, current_flexible_fraction=1.0
    )
    # A 100 kW reference rate at price scale 0.5 gives a 50 kW arrival rate
    # in each half-hour.  The current-arrival user must total 50 kWh over the
    # hour.  Future user fc_01 is deliberately excluded from this assertion:
    # it is an optional anticipatory charging decision, not current arrival.
    cases = [
        controller._build_case(
            [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [50.0, 0.0, 0.0],
            [0.0, 0.0, 0.0], current_ev=legacy_observation,
            dt_hours=0.5,
            current_ev_elapsed_hours=elapsed,
        )
        for legacy_observation, elapsed in ((0.0, 0.0), (100.0, 0.5))
    ]
    energies = [
        next(user for user in case.users if user.name == "current_ev_flexible")
        .energy_max_kwh
        for case in cases
    ]
    assert energies == [25.0, 25.0]
    assert sum(energies) == 50.0


def test_next_hour_forecast_can_use_current_dispatch_interval():
    controller = DynamicDCSFSEController(horizon_hours=3, max_sweeps=1)
    case = controller._build_case(
        [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 100.0, 0.0],
        [0.0, 0.0, 0.0], current_ev=0.0,
        current_ev_elapsed_hours=0.0,
    )
    future = next(user for user in case.users if user.name == "fc_01")
    assert 0 in future.available


def test_current_flexible_fraction_adds_price_responsive_user():
    controller = DynamicDCSFSEController(
        horizon_hours=3, max_sweeps=0, current_flexible_fraction=0.5
    )
    case = controller._build_case(
        [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [200.0, 50.0, 50.0],
        [0.0, 0.0, 0.0], current_ev=200.0,
        dt_hours=0.5,
        current_ev_elapsed_hours=0.0,
    )
    flexible = next(user for user in case.users if user.name == "current_ev_flexible")
    assert flexible.energy_max_kwh == 50.0
    assert case.base_load[0] == 100.0


def test_soh_game_switch_controls_effective_capacity():
    common = dict(horizon_hours=3, max_sweeps=0)
    aware = DynamicDCSFSEController(**common, use_soh_in_game=True)
    unaware = DynamicDCSFSEController(**common, use_soh_in_game=False)
    aware.reset(soc=0.5, soh=0.9)
    unaware.reset(soc=0.5, soh=0.9)
    inputs = ([0.0] * 3, [0.0] * 3, [100.0] * 3, [0.0] * 3)
    case_aware = aware._build_case(*inputs, soh_override=0.9)
    case_unaware = unaware._build_case(*inputs, soh_override=1.0)
    assert case_aware.effective_capacity_kwh < case_unaware.effective_capacity_kwh
    aged_case = aware._build_case(*inputs, soh_override=0.8)
    assert case_aware.battery_state is not None
    assert aged_case.battery_state is not None
    assert aged_case.effective_capacity_kwh < case_aware.effective_capacity_kwh
    assert (
        aged_case.effective_deg_cost_per_kwh
        > case_aware.effective_deg_cost_per_kwh
    )
    assert case_unaware.battery_state is None
    assert case_unaware.battery.deg_cost_per_kwh == 0.05


def test_ev_demand_elasticity_reduces_follower_energy_with_price():
    case = GameCase(
        T=1, pv=[0.0], wind=[0.0], base_load=[0.0],
        users=[EVUser("elastic", 3.0, 100.0, (0,), 100.0)],
        price_levels=[1.5, 2.5], ev_demand_elasticity=1.157,
        demand_reference_price=1.4, demand_forecast_price=1.4,
    )
    low = solve_follower_response(case, [1.5])["elastic"][0]
    high = solve_follower_response(case, [2.5])["elastic"][0]
    expected_ratio = (2.5 / 1.5) ** (-1.157)
    assert high < low
    assert abs(high / low - expected_ratio) < 1e-6


def test_follower_respects_shared_station_power_limit_with_rigid_load():
    case = GameCase(
        T=1, pv=[0.0], wind=[0.0], base_load=[100.0],
        users=[
            EVUser("a", 3.0, 500.0, (0,), 960.0),
            EVUser("b", 3.0, 500.0, (0,), 960.0),
        ],
        ev_service_limit_kw=960.0, ev_rigid_load_kw=[100.0],
        interval_hours=[0.5],
    )
    response = solve_follower_response(case, [1.5])
    total_power = sum(values[0] for values in response.values()) / 0.5
    assert total_power <= 860.0 + 1e-6


def test_soh_finalize_updates_effective_capacity():
    controller = DynamicDCSFSEController(horizon_hours=3, max_sweeps=1)
    controller.reset(soc=0.5, soh=1.0)
    result = controller.finalize_day([0.5, 0.6], [100.0], interval_hours=0.5)
    assert result["soh_end"] < 1.0
    assert controller.soc.config.capacity_kwh < controller.base_capacity_kwh


def test_provider_rejects_unavailable_noncyclic_cross_day_window():
    provider = CalibratedForecastProvider(horizon_hours=24, cyclic_profile=False)
    try:
        provider.bundle(provider.start.replace(hour=23), 0.5, 1.0)
    except ValueError as exc:
        assert "预测窗口越过" in str(exc)
    else:
        raise AssertionError("non-cyclic provider must reject an incomplete next-day window")


def test_excess_backlog_is_reported_separately():
    controller = DynamicDCSFSEController(
        horizon_hours=3, max_sweeps=1, backlog_limit_hours=1.0
    )
    controller.reset(soc=0.2, remaining_energy_kwh=1000.0)
    assert controller.backlog_limit_kwh == 960.0
