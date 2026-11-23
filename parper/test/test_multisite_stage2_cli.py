"""Opt-in command-level test using explicit local A/B/C checkout roots.

Set D_STAGE2_INTEGRATION_ROOTS to an os.pathsep-separated list of roots.
The matching untracked B checkpoint is required; missing local evidence skips.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4


class Stage2CommandSmoke(unittest.TestCase):
    def test_actual_four_arm_cli(self):
        configured = os.environ.get("D_STAGE2_INTEGRATION_ROOTS")
        if not configured:
            self.skipTest("set D_STAGE2_INTEGRATION_ROOTS to explicit A/B/C roots for local integration")
        roots = [Path(value).resolve() for value in configured.split(os.pathsep) if value]
        required = ("src/multisite/contract.py", "src/game/site_strategy.py",
                    "src/game/multisite_coordinator.py", "local/b-price-smoke-74ee75b/policy-11.pt")
        missing = [name for name in required if not any((root / name).is_file() for root in roots)]
        if missing:
            self.skipTest("local integration evidence unavailable: " + ", ".join(missing))
        workspace = Path(__file__).resolve().parents[2]
        output = workspace / "local/results/d_validation" / ("test-stage2-cli-" + uuid4().hex)
        command = [sys.executable, "scripts/run_multisite_replay.py", "--stage2-smoke",
                   "--output", str(output)]
        for root in roots:
            command.extend(["--integration-root", str(root)])
        completed = subprocess.run(command, cwd=workspace, capture_output=True,
                                   text=True, check=True, timeout=120)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["data_class"], "synthetic")
        self.assertFalse(result["ranking_authorized"])
        expected = {"fixed-price", "b-checkpoint-agent", "independent-site", "c-centralized"}
        self.assertEqual(set(result["results"]), expected)
        for arm in result["results"].values():
            self.assertEqual(arm["status"], "completed")
            self.assertEqual(arm["steps_executed"], 4)
            self.assertEqual(arm["common_input_hashes"], result["common_input_hashes"])
            self.assertLess(arm["max_abs_energy_residual_kwh"], 1e-6)
        self.assertEqual(result["results"]["b-checkpoint-agent"]["provider"]["provider_type"],
                         "B_checkpoint_site_strategy")
        self.assertEqual(result["results"]["c-centralized"]["provider"]["provider_type"],
                         "C_CentralizedCoordinator")
        # Retain generated evidence, including failures, in the ignored unique directory.
        for filename in ("manifest.json", "summary.json", "execution_ledger.json"):
            self.assertTrue((output / filename).is_file())


if __name__ == "__main__":
    unittest.main()
