"""Regression checks for energy/revenue units and short-run evidence gates."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_24h_dc_experiment as daily


class ExperimentAccountingTests(unittest.TestCase):
    def test_half_hour_revenue_and_curtailment_integrate_power(self):
        provider = SimpleNamespace(start=datetime(2021, 5, 4), site="test", date="2021-05-04",
                                   ev_load=[10.0] * 24, cyclic_profile=True)
        provider.bundle = lambda *args, **kwargs: None
        decision = SimpleNamespace(soc=0.5, storage_kw=0.0, price=2.0,
            ev_requested_kw=10.0, ev_served_kw=10.0, ev_shed_kw=0.0,
            pv_curtailed_kw=3.0, wind_curtailed_kw=1.0, overflow_unserved_kwh=0.0,
            power_flow={"vmin_pu": 1.0, "vmax_pu": 1.0, "branches": []})
        controller = SimpleNamespace(soc=SimpleNamespace(soc=0.5), soh=1.0,
            ev_service_limit_kw=960.0, reset=lambda **kwargs: None,
            step=lambda bundle: decision,
            finalize_day=lambda *args, **kwargs: {"delta_soh_total": 0.0, "aging_cost_yuan": 0.0})
        with patch.object(daily, "CalibratedForecastProvider", return_value=provider), \
             patch.object(daily, "DynamicDCSFSEController", return_value=controller), \
             patch.object(daily, "run_fixed_price_bess_baseline", return_value={}), \
             patch.object(daily, "run_no_storage_baseline", return_value={}):
            result = daily.run()
        self.assertAlmostEqual(result["ev_served_kwh"], 240.0)
        self.assertAlmostEqual(result["ev_revenue_yuan"], 480.0)
        self.assertAlmostEqual(result["renewable_curtailed_kwh"], 96.0)
        self.assertTrue(result["input_provenance"]["ev_cap_discrepancy_unresolved"])
        self.assertFalse(result["input_provenance"]["weather_date_matched"])

    def test_recorded_common_trace_diagnostic_preserves_requests_and_energy(self):
        path = Path(__file__).resolve().parents[1] / "experiment_runs/short_window_cap480_diagnostic_20261112.json"
        if not path.exists():
            self.skipTest("optional recorded smoke-run artifact not present")
        output = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(output["results"]["agent_soh_aware"]["status"], "not_implemented")
        requests = []
        for arm in ("fixed_price_bess", "dynamic_fixed_cost", "soh_aware"):
            result = output["results"][arm]
            requests.append(result["summary"]["requested_kwh"])
            self.assertEqual(result["status"], "diagnostic_completed")
            self.assertFalse(result["eligible_for_policy_ranking"])
            self.assertLess(abs(result["summary"]["energy_identity_residual_kwh"]), 1e-6)
            for step in result["steps"]:
                self.assertLess(abs(step["energy_residual_kwh"]), 1e-6)
                self.assertAlmostEqual(step["revenue_yuan"], step["price"] * step["served_kwh"])
        self.assertEqual(requests, [requests[0]] * 3)

    def test_full_day_base_shortfall_is_not_counted_as_delivery(self):
        path = Path(__file__).resolve().parents[1] / "experiment_runs/full_day_cap480_20210504_fast_ledger_replay_20261112.json"
        if not path.exists():
            self.skipTest("optional full-day failed checkpoint not present")
        result = json.loads(path.read_text(encoding="utf-8"))
        summary = result["summary"]
        self.assertEqual(result["status"], "failed_physical_gate")
        self.assertFalse(result["eligible_for_policy_ranking"])
        self.assertGreater(summary["non_ev_unserved_kwh"], 0)
        self.assertAlmostEqual(summary["non_ev_requested_kwh"],
            summary["non_ev_served_kwh"] + summary["non_ev_unserved_kwh"])
        self.assertLess(abs(summary["energy_identity_residual_kwh"]), 1e-6)
        self.assertGreater(summary["raw_power_balance_residual_kwh"], 1)

    def test_corrected_execution_closes_balance_without_hiding_shortage(self):
        path = Path(__file__).resolve().parents[1] / "experiment_runs/full_day_cap480_20210504_corrected_execution_ledger_20261112.json"
        if not path.exists():
            self.skipTest("optional corrected replay not present")
        result = json.loads(path.read_text(encoding="utf-8"))
        summary = result["summary"]
        self.assertEqual(result["status"], "service_shortfall_completed")
        self.assertFalse(result["eligible_for_policy_ranking"])
        self.assertGreater(summary["non_ev_unserved_kwh"], 0)
        self.assertLess(abs(summary["energy_identity_residual_kwh"]), 1e-6)
        self.assertLess(abs(summary["raw_power_balance_residual_kwh"]), 1e-6)

    def test_completed_corrected_full_day_and_declared_value_boundary(self):
        folder = Path(__file__).resolve().parents[1] / "experiment_runs"
        path = folder / "full_day_cap480_20210504_corrected_priority_20261112.json"
        if not path.exists():
            self.skipTest("optional corrected full day not present")
        output = json.loads(path.read_text(encoding="utf-8"))
        for arm in ("fixed_price_bess", "dynamic_fixed_cost", "soh_aware"):
            result = output["results"][arm]
            self.assertEqual(result["status"], "diagnostic_completed")
            self.assertEqual(len(result["steps"]), 48)
            s = result["summary"]
            self.assertLess(abs(s["energy_identity_residual_kwh"]), 1e-6)
            self.assertLess(s["max_abs_step_energy_residual_kwh"], 1e-6)
            self.assertAlmostEqual(s["served_kwh"] + s["shed_kwh"], s["requested_kwh"])
            self.assertAlmostEqual(s["non_ev_served_kwh"] + s["non_ev_unserved_kwh"], 1440)
            self.assertEqual(s["nonconverged_steps"], 0)
        summary_path = folder / "full_day_cap480_20210504_corrected_priority_summary_20261112.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for row in summary["rows"]:
            s = row["metrics"]
            if s:
                expected = s["revenue_yuan"] - s["aging_cost_yuan"] - 100 * s["shed_kwh"] - 1000 * s["non_ev_unserved_kwh"] - 0.2 * s["curtailment_kwh"]
                self.assertAlmostEqual(s["modeled_operating_value_yuan"], expected)

    def test_three_date_summary_preserves_failed_scenario_and_raw_pairs(self):
        path = Path(__file__).resolve().parents[1] / "experiment_runs/three_date_cap480_paired_diagnostics_20261112.json"
        if not path.exists():
            self.skipTest("optional three-date summary not present")
        output = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(output["n_distinct_dates"], 3)
        self.assertIsNone(output["uncertainty_interval"])
        dates = {day["date"]: day for day in output["dates"]}
        self.assertEqual(set(dates), {"2021-05-04", "2020-05-30", "2021-05-01"})
        for date, day in dates.items():
            for row in day["rows"]:
                if row["metrics"]:
                    self.assertEqual(row["metrics"]["convergence"]["executed_steps"], 48)
                    expected = "diagnostic_failed_gate" if date == "2021-05-01" else "diagnostic_completed"
                    self.assertEqual(row["status"], expected)
            for pair in day["paired_descriptive_deltas"]:
                self.assertEqual(pair["accounting_gate_passed_both"], date != "2021-05-01")
                self.assertFalse(pair["policy_ranking_authorized"])
                self.assertEqual(pair["differences"]["served_kwh"], 0)


if __name__ == "__main__":
    unittest.main()
