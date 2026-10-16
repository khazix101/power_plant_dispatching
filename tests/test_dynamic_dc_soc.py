from datetime import datetime

from src.game.dynamic_dc import DynamicDCSFSEController


def test_dynamic_dc_controller_updates_soc_and_returns_voltage():
    controller = DynamicDCSFSEController(horizon_hours=6, max_sweeps=1)
    controller.reset(soc=0.5, remaining_energy_kwh=0.0)
    decision = controller.step(
        datetime(2024, 1, 1),
        pv_forecast_kw=[200, 300, 400, 300, 100, 0],
        wind_forecast_kw=[50, 50, 50, 50, 50, 50],
        ev_base_forecast_kw=[180, 220, 200, 160, 120, 80],
        non_ev_forecast_kw=[50, 50, 50, 50, 50, 50],
    )
    assert decision.power_flow["voltage_feasible"]
    assert 0.1 <= decision.soc <= 0.9
    assert decision.ev_served_kw >= 0.0
    assert decision.remaining_energy_kwh >= 0.0
