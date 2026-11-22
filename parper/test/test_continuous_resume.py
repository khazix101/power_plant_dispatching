"""Verify day-boundary recovery against a completed continuous trajectory."""

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.battery.state import BatteryState


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("continuous_runner", ROOT / "scripts/run_continuous_three_day.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ContinuousResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "parper/experiment_runs/phase1_pilot_20261114/tight_soh090/fixed_price_bess.json"
        if not path.exists():
            raise unittest.SkipTest("Phase 1 trajectory is unavailable")
        cls.trajectory = json.loads(path.read_text(encoding="utf8"))

    def test_completed_days_reconstruct_full_state(self):
        run = copy.deepcopy(self.trajectory)
        run["days"] = run["days"][:2]
        state = BatteryState.new(total_batteries=1, eb_kwh=2000.0, init_soh=[0.9])
        state, soc, count = RUNNER.replay_completed_days(run, state, 0.5, run["dates"], 24.0)
        self.assertEqual(count, 2)
        self.assertEqual(state.as_dict(), run["days"][1]["output_state"])
        self.assertEqual(soc, run["days"][1]["summary"]["soc_end"])

    def test_changed_trace_is_rejected(self):
        run = copy.deepcopy(self.trajectory)
        run["days"] = run["days"][:1]
        run["days"][0]["trace_sha256"] = "invalid"
        state = BatteryState.new(total_batteries=1, eb_kwh=2000.0, init_soh=[0.9])
        with self.assertRaisesRegex(ValueError, "input trace"):
            RUNNER.replay_completed_days(run, state, 0.5, run["dates"], 24.0)

    def test_precision_runner_resumes_interrupted_day(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "parper/experiment_runs") as folder:
            dates = Path(folder) / "dates.txt"
            output = Path(folder) / "run.json"
            dates.write_text("2020-05-01\n2020-05-02\n", encoding="utf8")
            command = [sys.executable, str(ROOT / "scripts/run_continuous_three_day_precision.py"),
                       "--arm", "fixed_price_bess", "--output", str(output),
                       "--dates-file", str(dates), "--hours", "0.5"]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
            expected = json.loads(output.read_text(encoding="utf8"))
            self.assertEqual(expected["status"], "continuous_diagnostic_completed")
            interrupted = copy.deepcopy(expected)
            interrupted["status"] = "running"
            interrupted["days"] = interrupted["days"][:1]
            interrupted.pop("final_state", None)
            interrupted.pop("final_soc", None)
            interrupted.pop("source_hashes_unchanged", None)
            output.write_text(json.dumps(interrupted), encoding="utf8")
            subprocess.run(command + ["--resume"], cwd=ROOT, check=True, capture_output=True, text=True)
            actual = json.loads(output.read_text(encoding="utf8"))
            self.assertEqual(actual["status"], expected["status"])
            self.assertEqual(actual["final_state"], expected["final_state"])
            self.assertEqual(actual["final_soc"], expected["final_soc"])
            for expected_day, actual_day in zip(expected["days"], actual["days"]):
                for key in ("date", "input_state", "input_soc", "trace_sha256", "output_state"):
                    self.assertEqual(actual_day[key], expected_day[key])
                for key in ("served_kwh", "shed_kwh", "revenue_yuan", "aging_cost_yuan",
                            "energy_identity_residual_kwh"):
                    self.assertEqual(actual_day["summary"][key], expected_day["summary"][key])


if __name__ == "__main__":
    unittest.main()
