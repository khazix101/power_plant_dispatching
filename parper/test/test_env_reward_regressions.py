"""Regression tests for the executed-action PricingEnv reward ledger."""

from unittest.mock import patch
from math import isclose

from src.game.case import BatteryParams, EVUser, GameCase
from src.game.env import PricingEnv, Scenario
import numpy as np


def _scenario():
    case = GameCase(
        T=2,
        pv=[0.0, 0.0],
        wind=[0.0, 0.0],
        base_load=[0.0, 0.0],
        users=[EVUser("ev", 3.0, 60.0, (0, 1), 100.0)],
        battery=BatteryParams(
            capacity_kwh=100.0,
            power_kw=100.0,
            charge_eff=1.0,
            discharge_eff=1.0,
            deg_cost_per_kwh=0.1,
        ),
        price_levels=[1.5, 2.0],
        price_ramp_penalty=0.3,
        ev_congestion_penalty=4.0,
        ev_service_limit_kw=100.0,
        penalty_shed=2.0,
        penalty_curt=0.5,
        interval_hours=[0.5, 1.0],
    )
    return case, Scenario(case, np.zeros(6, dtype=np.float32))


def test_reward_charges_only_executed_ev_service_and_integrates_half_hour():
    case, scenario = _scenario()
    response = {"ev": [40.0, 20.0]}
    safe = {
        "p_ch": [10.0, 4.0],
        "p_dis": [0.0, 2.0],
        "shed": [3.0, 1.0],
        "curt": [2.0, 1.0],
        "ev": {"ev": [20.0, 20.0]},
        "soc": [0.5, 0.55, 0.57],
        "feasible": True,
    }
    schedule = {"p_ch": [10.0, 4.0], "p_dis": [0.0, 2.0]}
    with patch("src.game.env.solve_follower_response", return_value=response), \
         patch("src.game.env.solve_leader_fixed_profile", return_value=schedule), \
         patch("src.game.env.project_action", return_value=safe):
        result = PricingEnv(case).evaluate_action([2.0, 1.5], scenario)

    # Requested EV energy is 60 kWh, but only 40 kWh is executed and billed.
    assert result.ev_requested_kwh == 60.0
    assert result.ev_served_kwh == 40.0
    assert result.ev_unserved_kwh == 20.0
    assert isclose(result.revenue_yuan, 70.0)
    assert isclose(result.degradation_cost_yuan, 1.1)
    assert isclose(result.shed_cost_yuan, 5.0)  # 2 * (3*0.5 + 1*1)
    assert isclose(result.curtailment_cost_yuan, 1.0)  # 0.5 * (2*0.5 + 1*1)
    assert isclose(result.ev_unserved_cost_yuan, 40.0)
    assert isclose(result.price_ramp_cost_yuan, 0.075)
    assert isclose(result.congestion_cost_yuan, 0.48)
    assert isclose(result.profit, 22.345)


def test_reward_wrapper_preserves_legacy_tuple_contract():
    case, scenario = _scenario()
    response = {"ev": [10.0, 10.0]}
    safe = {
        "p_ch": [0.0, 0.0], "p_dis": [0.0, 0.0],
        "shed": [0.0, 0.0], "curt": [0.0, 0.0],
        "ev": {"ev": [10.0, 10.0]}, "feasible": True,
    }
    schedule = {"p_ch": [0.0, 0.0], "p_dis": [0.0, 0.0]}
    with patch("src.game.env.solve_follower_response", return_value=response), \
         patch("src.game.env.solve_leader_fixed_profile", return_value=schedule), \
         patch("src.game.env.project_action", return_value=safe):
        reward, feasible, returned_response = PricingEnv(case).reward(
            [1.5, 1.5], scenario
        )
    assert isclose(reward, 29.88)
    assert feasible is True
    assert returned_response == response
