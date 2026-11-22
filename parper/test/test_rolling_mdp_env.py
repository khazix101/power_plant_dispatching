"""Contract tests for the independent sequential rolling-MDP adapter."""

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from src.game.forecast import ForecastBundle
from src.game.rolling import RollingSFSEController
from src.game.rl_env import RollingSFSEMDPEnv


def _bundle(hour=0, *, soc=0.5, soh=0.9, reserve=12.0, backlog=None):
    history = {} if backlog is None else {"ev_backlog_kwh": [backlog]}
    return ForecastBundle(
        timestamp=datetime(2026, 1, 1) + timedelta(hours=hour),
        horizon_hours=2,
        pv_forecast_kw=[0.0, 10.0],
        wind_forecast_kw=[10.0, 10.0],
        base_ev_load_forecast_kw=[8.0, 6.0],
        non_ev_load_forecast_kw=[20.0, 20.0],
        current_ev_load_kw=2.0,
        soc_current=soc,
        soh_current=soh,
        reserve_kwh=reserve,
        history=history,
    )


def _env(seed=1):
    return RollingSFSEMDPEnv(
        RollingSFSEController(price_levels=[1.5, 2.0], max_sweeps=0), seed=seed
    )


class RollingMDPEnvTests(unittest.TestCase):
    def test_reset_trace_selection_is_seeded_and_state_is_explicit(self):
        trace = [_bundle(i, backlog=float(i + 1)) for i in range(4)]
        a = _env(seed=31).reset(trace, time_index=7, previous_price=1.5)
        b = _env(seed=31).reset(trace, time_index=7, previous_price=1.5)
        self.assertEqual(a.forecast_window.timestamp, b.forecast_window.timestamp)
        self.assertEqual(a.soc, 0.5)
        self.assertEqual(a.soh, 0.9)
        self.assertAlmostEqual(a.usable_capacity_kwh, 0.9 * 1000.0)
        self.assertEqual(a.reserve_kwh, 12.0)
        self.assertEqual(a.backlog_kwh, b.backlog_kwh)
        self.assertEqual(a.previous_price, 1.5)
        self.assertEqual(a.time_index, 7)

    def test_step_requires_explicit_next_bundle_until_termination(self):
        env = _env()
        env.reset(_bundle(backlog=4.0))
        with self.assertRaisesRegex(ValueError, "显式提供 next_bundle"):
            env.step([1.5, 2.0])

        next_state, reward, done, info = env.step(
            [1.5, 2.0], next_bundle=_bundle(1, soc=0.6, soh=0.85, reserve=14.0)
        )
        self.assertFalse(done)
        self.assertIsInstance(reward, float)
        self.assertEqual(next_state.time_index, 1)
        self.assertEqual(next_state.soc, 0.6)
        self.assertEqual(next_state.soh, 0.85)
        self.assertEqual(next_state.reserve_kwh, 14.0)
        self.assertEqual(next_state.backlog_kwh, 4.0)
        self.assertEqual(next_state.previous_price, 1.5)
        self.assertFalse(info["is_actual_execution_reward"])
        self.assertEqual(info["reward_source"], "planned_safe_projection")

    def test_terminated_step_needs_no_next_bundle_and_closes_episode(self):
        env = _env()
        env.reset(_bundle())
        _, _, done, _ = env.step([1.5, 2.0], terminated=True)
        self.assertTrue(done)
        with self.assertRaisesRegex(RuntimeError, "terminated"):
            env.step([1.5, 2.0], terminated=True)

    def test_price_plan_requires_exact_finite_grid_values(self):
        for plan in ([1.5], [1.5, 2.01], [1.5, float("nan")], [True, 2.0]):
            with self.subTest(plan=plan):
                env = _env()
                env.reset(_bundle())
                with self.assertRaises((ValueError, TypeError)):
                    env.step(plan, next_bundle=_bundle(1))

    def test_first_slot_reward_is_computed_from_safe_plan_not_dc_execution(self):
        env = _env()
        env.reset(_bundle(), previous_price=2.0)
        with patch.object(
            env.controller, "step_with_prices", wraps=env.controller.step_with_prices
        ) as run:
            _, reward, _, info = env.step([1.5, 2.0], next_bundle=_bundle(1))
        self.assertEqual(run.call_count, 1)
        parts = info["planned_reward_breakdown"]
        expected = (
            parts["revenue_yuan"] - parts["degradation_cost_yuan"]
            - parts["shed_cost_yuan"] - parts["curtailment_cost_yuan"]
            - parts["ev_unserved_cost_yuan"] - parts["price_ramp_cost_yuan"]
            - parts["congestion_cost_yuan"]
        )
        self.assertAlmostEqual(reward, expected)
        self.assertFalse(info["is_actual_execution_reward"])


if __name__ == "__main__":
    unittest.main()
