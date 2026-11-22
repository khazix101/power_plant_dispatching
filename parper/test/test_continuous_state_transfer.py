"""Check actual carried-state evidence without changing shared runner code."""
import json
import unittest
from pathlib import Path


class ContinuousStateTransferTests(unittest.TestCase):
    def test_recorded_fixed_arm_carries_full_state_and_energy_revaluation(self):
        path = Path(__file__).resolve().parents[1] / "experiment_runs/continuous_three_day_20261112/fixed_price_bess.json"
        if not path.exists():
            self.skipTest("continuous diagnostic artifact not available")
        run = json.loads(path.read_text(encoding="utf8"))
        self.assertEqual(len(run["days"]), 3)
        self.assertTrue(run["source_hashes_unchanged"])
        previous = None
        for day in run["days"]:
            self.assertEqual(len(day["steps"]), 48)
            if previous:
                self.assertEqual(day["input_state"], previous["output_state"])
                self.assertEqual(day["input_soc"], previous["summary"]["soc_end"])
            summary = day["summary"]
            self.assertAlmostEqual(summary["soh_start"], day["input_state"]["soh_system"])
            self.assertAlmostEqual(summary["soh_end_posthoc"], day["output_state"]["soh_system"])
            self.assertLess(summary["soh_end_posthoc"], summary["soh_start"])
            self.assertEqual(summary["initial_marginal_degradation_cost_yuan_per_kwh"], run["initial_matched_marginal_cost_yuan_per_kwh"])
            self.assertAlmostEqual(summary["capacity_revaluation_loss_kwh"], summary["soc_end"] * (summary["usable_capacity_start_kwh"] - summary["usable_capacity_end_kwh"]))
            self.assertEqual(summary["replacement_cost_yuan"], 0)
            if abs(summary["energy_identity_residual_kwh"]) > 1e-6:
                self.assertEqual(day["status"], "diagnostic_failed_gate")
            previous = day

    def test_completed_paired_summary_and_secondary_ledger_remain_separate(self):
        folder=Path(__file__).resolve().parents[1]/"experiment_runs/continuous_three_day_20261112"
        if not (folder/"original_continuous_summary.json").exists():
            self.skipTest("continuous summary not available")
        summary=json.loads((folder/"original_continuous_summary.json").read_text(encoding="utf8"))
        for arm,total in summary["arm_totals"].items():
            self.assertEqual(total["original_gate_failed_days"],3)
            self.assertTrue(total["source_hashes_unchanged"])
            self.assertAlmostEqual(total["final_soh"],.8998262918856126)
            run=json.loads((folder/f"{arm}.json").read_text(encoding="utf8"))
            for i,day in enumerate(run["days"]):
                if i:
                    self.assertEqual(day["input_state"],run["days"][i-1]["output_state"])
                    self.assertEqual(day["input_soc"],run["days"][i-1]["summary"]["soc_end"])
        for path in folder.glob("*secondary_ledger.json"):
            audit=json.loads(path.read_text(encoding="utf8"))
            for day in audit["days"]:
                self.assertEqual(day["original_status"],"diagnostic_failed_gate")
                self.assertLess(abs(day["original_identity_plus_step_sum_kwh"]),1e-10)
                self.assertTrue(day["precise_replay_gate_passed"])
                self.assertLess(abs(day["precise_replay_identity_kwh"]),1e-6)
                self.assertLess(day["max_abs_power_deviation_kw"],1e-10)
                self.assertEqual(day["delivered_revenue_delta_yuan"],0)
                self.assertEqual(day["aging_cost_delta_yuan"],0)


if __name__ == "__main__":
    unittest.main()
