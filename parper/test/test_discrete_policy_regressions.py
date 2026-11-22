"""Regression tests for the legal discrete-price RL action schema."""

import numpy as np
import unittest

import torch

from src.game.rl import DiscretePolicyNet


def _policy(seed=0):
    return DiscretePolicyNet(
        n_features=5, T=3, price_levels=[1.1, 1.7, 2.4], hidden=16, seed=seed
    )


class DiscretePolicyTests(unittest.TestCase):
    def test_samples_only_legal_price_levels_and_scores_same_action(self):
        policy = _policy()
        feat = torch.tensor([0.1, 0.2, 0.3, 0.4, 0.5])
        action = policy.sample_action(feat, seed=23)

        self.assertEqual(tuple(action["indices"].shape), (3,))
        self.assertEqual(tuple(action["prices"].shape), (3,))
        self.assertLessEqual(set(action["prices"].tolist()), {1.1, 1.7, 2.4})
        expected_log_prob, entropy = policy.evaluate_actions(feat, action["indices"])
        self.assertTrue(torch.allclose(action["log_prob"], expected_log_prob))
        self.assertEqual(tuple(entropy.shape), ())
        self.assertTrue(torch.isfinite(action["log_prob"]))

    def test_seed_and_model_initialization_are_reproducible(self):
        feat = torch.tensor([[0.1, 0.2, 0.3, 0.4, 0.5]])
        left = _policy(seed=7)
        right = _policy(seed=7)
        self.assertTrue(torch.equal(left.act_deterministic(feat),
                                    right.act_deterministic(feat)))
        first = left.sample_action(feat, seed=99)
        second = left.sample_action(feat, seed=99)
        self.assertTrue(torch.equal(first["indices"], second["indices"]))
        self.assertTrue(torch.equal(first["prices"], second["prices"]))
        self.assertTrue(torch.equal(first["log_prob"], second["log_prob"]))

    def test_validates_action_shape_and_bounds(self):
        policy = _policy()
        feat = torch.zeros(5)
        with self.assertRaisesRegex(ValueError, "最后一维"):
            policy.log_prob(feat, torch.tensor([0, 1]))
        with self.assertRaisesRegex(ValueError, "索引"):
            policy.log_prob(feat, torch.tensor([0, 1, 3]))
        with self.assertRaisesRegex(TypeError, "整数"):
            policy.log_prob(feat, torch.tensor([0.0, 1.0, 2.0]))

    def test_supports_batched_observations(self):
        policy = _policy()
        feat = torch.zeros((4, 5))
        action = policy.sample_action(feat, seed=5)
        self.assertEqual(tuple(action["indices"].shape), (4, 3))
        self.assertEqual(tuple(action["prices"].shape), (4, 3))
        self.assertEqual(tuple(action["log_prob"].shape), (4,))
        self.assertTrue(np.isin(action["prices"].numpy(), [1.1, 1.7, 2.4]).all())


if __name__ == "__main__":
    unittest.main()
