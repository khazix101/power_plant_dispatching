import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np
import torch

from scripts.train_price_agent import synthetic_partitions
from src.game.price_agent import (PriceAdapter, load_policy, save_policy, replay_policy,
                                  validate_split, train, ContractPriceStrategy)
from src.game.rl import DiscretePolicyNet


class PriceAgentTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        self.partitions = synthetic_partitions()
        self.trace = self.partitions["test"][0]
        self.levels = [1.5, 1.7, 1.9, 2.1, 2.3, 2.5]

    def policy(self):
        adapter = PriceAdapter(self.trace, self.levels)
        feat, case = adapter.inputs(self.trace["steps"][0], self.trace["initial_states"])
        return DiscretePolicyNet(len(feat), case.T, self.levels, hidden=64, seed=11), feat

    def test_legal_and_seeded_actions(self):
        policy, feat = self.policy()
        x = torch.tensor(feat)
        a, b = policy.sample_action(x, seed=23), policy.sample_action(x, seed=23)
        self.assertTrue(torch.equal(a["indices"], b["indices"]))
        self.assertTrue(all(p in self.levels for p in a["prices"].tolist()))
        with self.assertRaises(ValueError):
            policy.prices_from_indices(torch.tensor([100]))
        with self.assertRaises(ValueError):
            PriceAdapter(self.trace, [1, 2], "agent", policy)

    def test_checkpoint_roundtrip(self):
        policy, feat = self.policy()
        scratch = Path(__file__).resolve().parents[2] / "local" / "b-agent-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            path = Path(directory) / "model.pt"
            save_policy(path, policy)
            restored = load_policy(path, self.levels)
            self.assertTrue(torch.equal(policy(torch.tensor(feat)), restored(torch.tensor(feat))))
            with self.assertRaises(ValueError):
                load_policy(path, [1, 2])

    def test_date_and_forecast_leakage_rejected(self):
        validate_split(self.partitions)
        bad = copy.deepcopy(self.partitions)
        bad["test"][0]["steps"][0]["forecast"]["timestamps"] = ["2024-01-01T12:00:00+08:00"]
        with self.assertRaisesRegex(ValueError, "leakage"):
            validate_split(bad)

    def test_no_actual_future_information(self):
        adapter = PriceAdapter(self.trace, self.levels)
        a, _ = adapter.inputs(self.trace["steps"][0], self.trace["initial_states"])
        other = copy.deepcopy(self.trace)
        other["steps"][1]["pv_kw"] = 99999
        b, _ = PriceAdapter(other, self.levels).inputs(other["steps"][0], other["initial_states"])
        np.testing.assert_array_equal(a, b)

    def test_projection_and_executed_ledger(self):
        from unittest.mock import patch
        import src.game.env as env
        original = env.project_action
        with patch.object(env, "project_action", wraps=original) as safety:
            adapter, replay = replay_policy(self.trace, self.levels, "fixed")
        self.assertGreaterEqual(safety.call_count, 2)
        summary = replay["summary"]
        self.assertAlmostEqual(summary["requested_kwh"], 80)
        self.assertAlmostEqual(summary["delivered_revenue_yuan"], summary["user_cost_yuan"])
        self.assertAlmostEqual(summary["requested_kwh"] - summary["served_kwh"], summary["unserved_kwh"])
        self.assertIn("hard_violation_counts", summary)
        self.assertIn("storage_loss_kwh", summary)
        self.assertTrue(all(a["safe_feasible"] for a in adapter.actions))

    def test_constrained_replay_changes_storage_schedule(self):
        trace = copy.deepcopy(self.trace)
        trace["execution_config"]["battery_power_kw"] = 1.0
        for row in trace["steps"]:
            row.update(pv_kw=0, wind_kw=0)
            row["forecast"].update(pv_kw=[0], wind_kw=[0])
        from scripts.run_fair_replay_experiments import trace_hash
        trace["trace_sha256"] = trace_hash(trace)
        _, replay = replay_policy(trace, self.levels, "fixed")
        self.assertGreater(replay["summary"]["unserved_kwh"], 0)
        self.assertTrue(all(abs(r["p_bess_kw"]) <= 1.000001 for r in replay["executed_ledger"]["steps"]))

    def test_unsafe_storage_request_is_projected(self):
        from src.game.safety import SafeController, check_feasible
        adapter = PriceAdapter(self.trace, self.levels)
        _, case = adapter.inputs(self.trace["steps"][0], self.trace["initial_states"])
        safe = SafeController(case, prefer="appsi_highs").act([1e6], [1e6], {"forecast_0": [40]})
        self.assertTrue(check_feasible(case, safe))
        self.assertGreater(safe["dev_l1"], 0)
        self.assertLessEqual(safe["p_dis"][0], case.battery.power_kw + 1e-6)

    def test_a_strategy_contract_bridge(self):
        adapter = PriceAdapter(self.trace, self.levels)
        strategy = ContractPriceStrategy("site-1", adapter, action_factory=dict)
        constraints = {"contract_version": "1.0.0", "price_levels": self.levels,
                       "trace_sha256": self.trace["trace_sha256"]}
        action = strategy.decide("site-1", self.trace["steps"][0], self.trace["initial_states"], constraints)
        self.assertEqual(action["site_id"], "site-1")
        self.assertIn(action["price_yuan_per_kwh"], self.levels)
        with self.assertRaises(ValueError):
            strategy.decide("site-2", self.trace["steps"][0], self.trace["initial_states"], constraints)

    def test_terminal_violation_is_counted_and_penalized(self):
        from src.game.price_agent import executed_metrics
        adapter, replay = replay_policy(self.trace, self.levels, "fixed")
        raw = copy.deepcopy(replay["executed_ledger"])
        raw["summary"]["terminal_soc_feasible"] = False
        rows, summary = executed_metrics(raw, adapter.actions, self.trace)
        self.assertEqual(summary["terminal_violation_count"], 1)
        self.assertAlmostEqual(summary["reward"], replay["summary"]["reward"] - 1000)

    def test_training_reproducible_and_bc(self):
        a, ha = train(self.partitions, self.levels, 11, episodes=1, bc_epochs=1)
        b, hb = train(self.partitions, self.levels, 11, episodes=1, bc_epochs=1)
        for key, value in a.state_dict().items():
            self.assertTrue(torch.equal(value, b.state_dict()[key]), key)
        self.assertEqual(ha[0]["reward"], hb[0]["reward"])


if __name__ == "__main__":
    unittest.main()
