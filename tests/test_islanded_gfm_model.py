"""五类 GFM 离网 QSTS 自动化测试。"""

from pathlib import Path

from dss import DSS

from src.model.opendss import IslandedGFMConfig, IslandedGFMQSTS, MicrogridModel


def make_controller(*, soc=0.50, pcs_kva=1000.0):
    root = Path(__file__).resolve().parents[1]
    model = MicrogridModel(base_kv=0.4, base_freq=50.0)
    model.compile(root / "model" / "islanded_gfm.dss", islanded=True,
                  vtheta_bus="Storage_Bus")
    config = IslandedGFMConfig(
        pcs_kva=pcs_kva,
        pcs_q_kvar=600.0,
        coupling="X_gt_R",
        soc_initial=soc,
    )
    return IslandedGFMQSTS(model, config)


def test_topology_uses_storage_bus_as_numerical_vtheta_reference():
    root = Path(__file__).resolve().parents[1]
    model = MicrogridModel(base_kv=0.4, base_freq=50.0)
    model.compile(root / "model" / "islanded_gfm.dss", islanded=True,
                  vtheta_bus="Storage_Bus")
    DSS.ActiveCircuit.Vsources.First
    assert DSS.ActiveCircuit.Vsources.Name.lower() == "source"
    DSS.ActiveCircuit.SetActiveElement("Vsource.source")
    assert "storage_bus" in DSS.ActiveCircuit.ActiveCktElement.BusNames[0].lower()


def test_1_basic_supply_demand_balance_has_no_external_exchange():
    controller = make_controller()
    result = controller.solve_step(
        pv_kw=200.0, wind_kw=50.0,
        ev_base_kw=180.0, ev_load_kvar=60.0,
        p_bess_schedule_kw=0.0, q_bess_schedule_kvar=0.0,
    )
    assert result["converged"]
    assert result["ev_shed_kw"] == 0.0
    assert abs(result["residual_p_kw"]) <= 0.5
    assert abs(result["residual_q_kvar"]) <= 0.5
    assert result["external_supply_kw"] == 0.0
    assert 0.20 <= result["soc"] <= 0.90
    assert 0.95 <= result["vmin_pu"] <= result["vmax_pu"] <= 1.05


def test_2_renewable_surplus_charges_battery_or_curtails_without_grid_export():
    controller = make_controller()
    result = controller.solve_step(
        pv_kw=400.0, wind_kw=100.0,
        ev_base_kw=80.0, ev_load_kvar=30.0,
        p_bess_schedule_kw=0.0, q_bess_schedule_kvar=0.0,
    )
    assert result["converged"]
    assert result["p_bess_kw"] < 0.0 or (
        result["pv_curtailed_kw"] + result["wind_curtailed_kw"] > 0.0
    )
    assert result["soc"] > 0.50
    assert result["external_supply_kw"] == 0.0
    assert abs(result["residual_p_kw"]) <= 0.5


def test_3_supply_deficit_discharges_battery_without_ev_shedding():
    controller = make_controller()
    result = controller.solve_step(
        pv_kw=200.0, wind_kw=50.0,
        ev_base_kw=350.0, ev_load_kvar=120.0,
        p_bess_schedule_kw=0.0, q_bess_schedule_kvar=0.0,
    )
    assert result["converged"]
    assert result["p_bess_kw"] > 0.0
    assert result["soc"] < 0.50
    assert result["ev_shed_kw"] == 0.0
    assert result["external_supply_kw"] == 0.0
    assert abs(result["residual_p_kw"]) <= 0.5


def test_4_soc_reserve_saturation_becomes_explicit_ev_shedding():
    controller = make_controller(soc=0.25)
    result = controller.solve_step(
        pv_kw=0.0, wind_kw=0.0,
        ev_base_kw=1000.0, ev_load_kvar=330.0,
        p_bess_schedule_kw=800.0, q_bess_schedule_kvar=0.0,
    )
    assert result["converged"]
    assert result["ev_shed_kw"] > 0.0
    assert result["ev_served_kw"] + result["ev_shed_kw"] == 1000.0
    assert result["soc"] >= 0.20 - 1e-6
    assert result["external_supply_kw"] == 0.0
    assert abs(result["residual_p_kw"]) <= 0.5


def test_5_pq_apparent_power_and_soc_limits_are_enforced():
    controller = make_controller(soc=0.50, pcs_kva=900.0)
    p, q = controller._droop_setpoints(
        p_schedule_kw=800.0,
        q_schedule_kvar=600.0,
        frequency_hz=50.0,
        voltage_pu=1.0,
        p_slope=0.5 / 800.0,
        q_slope=0.01 / 600.0,
        dt_hours=1.0,
    )
    assert abs(p) <= 800.0 + 1e-6
    assert abs(q) <= 600.0 + 1e-6
    assert p * p + q * q <= 900.0**2 + 1e-3

    controller.reset(soc_pct=25.0)
    p_limited = controller._soc_limited_p(800.0, 1.0)
    expected = (0.25 - 0.20) * 2000.0 * 0.95
    assert abs(p_limited - expected) <= 1e-6
