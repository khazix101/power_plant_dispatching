"""Regression tests for interval-aware safety projection SOC dynamics."""

from src.game.case import BatteryParams, EVUser, GameCase
from src.game.safety import check_feasible, project_action


def _case(interval_hours):
    return GameCase(
        T=2,
        pv=[80.0, 0.0],
        wind=[0.0, 0.0],
        base_load=[0.0, 0.0],
        users=[EVUser("ev", 1.0, 0.0, (0, 1), 1.0)],
        battery=BatteryParams(
            capacity_kwh=100.0,
            power_kw=100.0,
            charge_eff=1.0,
            discharge_eff=1.0,
            soc0=0.5,
            soc_min=0.1,
            soc_max=0.9,
            soc_end_min=0.1,
        ),
        interval_hours=interval_hours,
    )


def test_project_action_uses_half_hour_first_slot_soc_update():
    case = _case([0.5, 1.0])
    safe = project_action(case, [80.0, 0.0], [0.0, 0.0], {"ev": [0.0, 0.0]})

    # 80 kW for 0.5 h adds 40 kWh, so SOC reaches 0.9 exactly. A hard-coded
    # one-hour step would incorrectly force the first action down to 40 kW.
    assert abs(safe["p_ch"][0] - 80.0) <= 1e-6
    assert abs(safe["soc"][1] - 0.9) <= 1e-6
    assert abs(safe["soc"][2] - 0.9) <= 1e-6
    assert safe["feasible"]


def test_check_feasible_uses_per_slot_intervals_and_scalar_broadcast():
    case = _case([0.5, 1.0])
    valid = {
        "p_ch": [80.0, 0.0],
        "p_dis": [0.0, 0.0],
        "shed": [0.0, 0.0],
        "curt": [0.0, 0.0],
        "ev": {"ev": [0.0, 0.0]},
        "soc": [0.5, 0.9, 0.9],
    }
    assert check_feasible(case, valid)

    invalid = dict(valid, soc=[0.5, 1.3, 1.3])
    assert not check_feasible(case, invalid)

    scalar_case = _case([0.5, 0.5])
    scalar_case.interval_hours = 0.5
    scalar_valid = dict(valid, soc=[0.5, 0.9, 0.9])
    assert check_feasible(scalar_case, scalar_valid)


def test_project_action_converts_ev_interval_energy_to_average_power():
    case = _case([0.5, 1.0])
    safe = project_action(case, [0.0, 0.0], [0.0, 0.0], {"ev": [40.0, 0.0]})

    # 40 kWh over 0.5 h is 80 kW in the interval power balance.
    assert abs(safe["ev"]["ev"][0] - 40.0) <= 1e-6
    assert safe["feasible"]
