import unittest
from unittest.mock import patch

from src.game.bri import _network_candidate_metrics, evaluate_price_vector
from src.game.case import BatteryParams, EVUser, GameCase
from src.game.dc_microgrid import DCBranch, DCNetworkConfig, DCMicrogridPowerFlow


def _case():
    return GameCase(
        T=1,
        pv=[600.0],
        wind=[0.0],
        base_load=[450.0],
        users=[EVUser("ev", 3.0, 100.0, (0,), 100.0)],
        battery=BatteryParams(
            capacity_kwh=100.0, power_kw=0.0, soc0=0.5,
            soc_min=0.1, soc_max=0.9, soc_end_min=0.5,
        ),
        price_levels=[1.0, 3.0],
        penalty_shed=100.0,
        interval_hours=[1.0],
    )


def _network(voltage_min=0.95, current_limit=0.0):
    return DCNetworkConfig(
        voltage_min_pu=voltage_min,
        branches=[
            DCBranch("Storage_Bus", "PV_Bus", 0.005, current_limit),
            DCBranch("Storage_Bus", "Wind_Bus", 0.005, current_limit),
            DCBranch("Storage_Bus", "Load_Bus", 0.005, current_limit),
        ],
    )


class NetworkPriceLoopTests(unittest.TestCase):
    def test_network_metrics_report_voltage_loss_and_unverified_thermal_rating(self):
        flow = DCMicrogridPowerFlow().solve(
            {"PV_Bus": 400.0, "Wind_Bus": 100.0, "Load_Bus": -480.0}
        )
        self.assertTrue(flow["voltage_feasible"])
        self.assertFalse(flow["thermal_limits_verified"])
        self.assertIsNone(flow["thermal_feasible"])
        self.assertIsNone(flow["max_loading_pu"])
        self.assertAlmostEqual(flow["losses_kw"], 12.5125)
        self.assertEqual(DCNetworkConfig().ev_post_kw, 120.0)

    def test_candidate_metrics_include_scheduled_bess_and_balance_residual(self):
        case = GameCase(
            T=1, pv=[100.0], wind=[0.0], base_load=[50.0],
            users=[], battery=BatteryParams(power_kw=100.0),
            interval_hours=[1.0],
        )
        metrics = _network_candidate_metrics(
            case,
            {"p_dis": [25.0], "p_ch": [0.0], "shed": [0.0], "curt": [0.0]},
            [0.0],
            DCNetworkConfig(),
        )
        self.assertEqual(metrics["scheduled_bess_kw"], [25.0])
        self.assertEqual(len(metrics["power_balance_residual_kw"]), 1)
        self.assertAlmostEqual(
            metrics["power_balance_residual_kw"][0],
            50.0 + metrics["snapshots"][0]["losses_kw"] - 100.0 - 25.0,
        )
        self.assertTrue(metrics["reference_bus_injection_is_slack"])

    def test_candidate_metrics_apply_curtailment_and_shed_to_snapshot(self):
        case = GameCase(
            T=1, pv=[100.0], wind=[100.0], base_load=[100.0],
            users=[], battery=BatteryParams(power_kw=0.0),
            interval_hours=[1.0],
        )
        metrics = _network_candidate_metrics(
            case,
            {"p_dis": [0.0], "p_ch": [0.0], "shed": [30.0], "curt": [150.0]},
            [100.0],
            DCNetworkConfig(),
        )
        self.assertEqual(metrics["pv_used_kw"], [0.0])
        self.assertEqual(metrics["wind_used_kw"], [50.0])
        self.assertEqual(metrics["load_after_shed_kw"], [170.0])

    def test_network_filter_is_opt_in(self):
        with patch("src.game.bri.DCMicrogridPowerFlow") as flow_factory:
            profit, _, sched = evaluate_price_vector(_case(), [1.0])
        self.assertTrue(flow_factory.call_count == 0)
        self.assertIsNone(sched["network_metrics"])
        self.assertIsNone(sched["network_feasible"])
        self.assertEqual(sched["network_penalty"], 0.0)
        self.assertNotEqual(profit, float("-inf"))

    def test_network_filter_rejects_voltage_infeasible_price_candidate(self):
        case = _case()
        config = _network(voltage_min=0.984)

        legacy_profit, _, _ = evaluate_price_vector(case, [1.0])
        filtered_profit, _, filtered = evaluate_price_vector(
            case, [1.0], network_config=config, network_filter=True
        )
        _, _, feasible = evaluate_price_vector(
            case, [3.0], network_config=config, network_filter=True
        )

        self.assertEqual(filtered_profit, float("-inf"))
        self.assertFalse(filtered["network_feasible"])
        self.assertFalse(filtered["network_metrics"]["voltage_feasible"])
        self.assertTrue(feasible["network_feasible"])
        self.assertGreater(legacy_profit, filtered_profit)

    def test_hard_thermal_filter_requires_rating_on_every_branch(self):
        with self.assertRaisesRegex(ValueError, "every branch"):
            evaluate_price_vector(
                _case(), [1.0], network_config=_network(), network_filter=True,
                network_hard_thermal=True,
            )

    def test_hard_thermal_filter_uses_explicit_branch_ratings(self):
        config = _network(voltage_min=0.90, current_limit=10.0)
        _, _, metrics = evaluate_price_vector(
            _case(), [1.0], network_config=config, network_filter=True,
            network_hard_voltage=False, network_hard_thermal=True,
        )

        self.assertTrue(metrics["network_metrics"]["thermal_limits_verified"])
        self.assertGreater(metrics["network_metrics"]["max_loading_pu"], 1.0)
        self.assertFalse(metrics["network_metrics"]["thermal_feasible"])
        self.assertFalse(metrics["network_feasible"])
        self.assertEqual(metrics["profit"], float("-inf"))


if __name__ == "__main__":
    unittest.main()
