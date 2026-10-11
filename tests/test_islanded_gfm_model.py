from pathlib import Path

from dss import DSS

from src.model.opendss import IslandedGFMConfig, IslandedGFMQSTS, MicrogridModel


def test_islanded_model_moves_numerical_vtheta_to_storage_bus():
    root = Path(__file__).resolve().parents[1]
    model = MicrogridModel(base_kv=0.4, base_freq=50.0)
    model.compile(root / "model" / "islanded_gfm.dss", islanded=True,
                  vtheta_bus="Storage_Bus")
    DSS.ActiveCircuit.Vsources.First
    assert DSS.ActiveCircuit.Vsources.Name.lower() == "source"
    DSS.ActiveCircuit.SetActiveElement("Vsource.source")
    assert "storage_bus" in DSS.ActiveCircuit.ActiveCktElement.BusNames[0].lower()


def make_controller():
    root = Path(__file__).resolve().parents[1]
    model = MicrogridModel(base_kv=0.4, base_freq=50.0)
    model.compile(root / "model" / "islanded_gfm.dss", islanded=True,
                  vtheta_bus="Storage_Bus")
    config = IslandedGFMConfig(
        pcs_kva=1000.0,
        pcs_q_kvar=600.0,
        coupling="X_gt_R",
    )
    return IslandedGFMQSTS(model, config)


def test_gfm_step_has_no_external_supply_and_soc_stays_bounded():
    controller = make_controller()
    result = controller.solve_step(
        pv_kw=200.0,
        wind_kw=50.0,
        ev_base_kw=180.0,
        ev_load_kvar=60.0,
        p_bess_schedule_kw=0.0,
        q_bess_schedule_kvar=0.0,
        dt_hours=1.0,
    )
    assert result["converged"]
    assert abs(result["external_supply_kw"]) == 0.0
    assert abs(result["residual_p_kw"]) <= 0.5
    assert abs(result["residual_q_kvar"]) <= 0.5
    assert 0.2 <= result["soc"] <= 0.9
    assert result["ev_shed_kw"] == 0.0
    assert result["line_thermal_limits_verified"] is False


def test_low_soc_causes_explicit_ev_shedding_not_grid_supply():
    controller = make_controller()
    controller.reset(soc_pct=25.0)
    result = controller.solve_step(
        pv_kw=0.0,
        wind_kw=0.0,
        ev_base_kw=1000.0,
        ev_load_kvar=330.0,
        p_bess_schedule_kw=800.0,
        q_bess_schedule_kvar=0.0,
        dt_hours=1.0,
    )
    assert result["converged"]
    assert result["ev_shed_kw"] > 0.0
    assert result["external_supply_kw"] == 0.0
    assert result["soc"] >= 0.2 - 1e-6
    assert abs(result["residual_p_kw"]) <= 0.5
