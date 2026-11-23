import copy
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import unittest

from src.game.site_strategy import CheckpointSiteStrategy


ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = ROOT / "local" / "b-price-smoke-74ee75b" / "policy-11.pt"
EXPECTED_CHECKPOINT_SHA256 = "d667eba06d4c53ce0e3ea1fda6184b89ae0452136211345620492bd44a108c1f"
LEVELS = [1.5, 1.7, 1.9, 2.1, 2.3, 2.5]


def _window(count=1):
    start = datetime.fromisoformat("2026-01-01T12:00:00+08:00")
    rows = []
    for i in range(count):
        stamp = (start + timedelta(minutes=30 * i)).isoformat()
        rows.append({
            "timestamp": stamp,
            "pv_forecast_kw": 45.0 + i,
            "wind_forecast_kw": 3.0,
            "load_forecast_kw": 15.0,
            "ev_forecast_kw": 12.0 + i,
            "source_quality": {
                name: {"source": "fixture forecast", "status": "forecast"}
                for name in ("pv_forecast_kw", "wind_forecast_kw", "load_forecast_kw", "ev_forecast_kw")
            },
        })
    return rows


def _constraints():
    return {
        "contract_version": "1.0.0",
        "trace_sha256": "a" * 64,
        "step_index": 0,
        "dt_hours": 0.5,
        "assets": {
            "battery_capacity_kwh": 40.0,
            "battery_power_kw": 20.0,
            "soc_min": 0.1,
            "soc_reserve": 0.2,
            "soc_max": 0.9,
            "eta_charge": 0.95,
            "eta_discharge": 0.95,
            "ev_service_limit_kw": 30.0,
        },
        "price_levels": LEVELS,
        "network_summary": {"available": False, "reason": "fixture has no network telemetry"},
        "safety_summary": {"available": False, "reason": "D performs final projection"},
    }


class CheckpointSiteStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CHECKPOINT.is_file():
            raise unittest.SkipTest("the fixed ignored checkpoint fixture is unavailable in this checkout")
        digest = hashlib.sha256(CHECKPOINT.read_bytes()).hexdigest()
        if digest != EXPECTED_CHECKPOINT_SHA256:
            raise AssertionError("fixed checkpoint fixture hash changed")

    def setUp(self):
        self.strategy = CheckpointSiteStrategy("site-1", CHECKPOINT, LEVELS)

    def decide(self, window=None, state=None, constraints=None):
        return self.strategy.decide(
            "site-1", _window() if window is None else window,
            {"soc": 0.55, "soh": 0.98} if state is None else state,
            _constraints() if constraints is None else constraints,
        )

    def test_fixed_checkpoint_emits_current_legal_action_for_h1(self):
        action = self.decide()
        self.assertEqual(action["site_id"], "site-1")
        self.assertEqual(action["timestamp"], "2026-01-01T12:00:00+08:00")
        self.assertIn(action["price_yuan_per_kwh"], LEVELS)
        self.assertTrue(-20 <= action["storage_kw"] <= 20)
        self.assertGreaterEqual(action["requested_ev_kw"], 0)
        self.assertEqual(self.strategy.calls[-1]["forecast_horizon_used"], 1)
        self.assertEqual(self.strategy.calls[-1]["feature_count"], self.strategy.policy.body[0].in_features)

    def test_long_forecast_is_prefix_truncated_without_reshape(self):
        long_window = _window(4)
        action = self.decide(window=long_window)
        self.assertEqual(action["timestamp"], long_window[0]["timestamp"])
        call = self.strategy.calls[-1]
        self.assertEqual(call["forecast_horizon_received"], 4)
        self.assertEqual(call["forecast_horizon_used"], 1)
        self.assertEqual(call["forecast_horizon_truncated"], 3)
        self.assertEqual(call["feature_count"], 21)

    def test_deterministic_actions_and_quality_future_actuals_are_isolated(self):
        window = _window(3)
        first = self.decide(window=window)
        repeated = self.decide(window=copy.deepcopy(window))
        self.assertEqual(first, repeated)
        changed_future_actual = copy.deepcopy(window)
        changed_future_actual[1]["pv_actual_kw"] = 999999.0
        changed_future_actual[2]["load_actual_kw"] = 999999.0
        other = self.decide(window=changed_future_actual)
        self.assertEqual(first, other)
        calls = self.strategy.calls[-3:]
        self.assertEqual(calls[0]["observation_sha256"], calls[1]["observation_sha256"])
        self.assertEqual(calls[1]["observation_sha256"], calls[2]["observation_sha256"])

    def test_soc_soh_are_present_in_the_mapped_checkpoint_observation(self):
        self.decide(state={"soc": 0.61, "soh": 0.83})
        call = self.strategy.calls[-1]
        self.assertEqual(call["observation_soc"], 0.61)
        self.assertEqual(call["observation_soh"], 0.83)
        self.assertAlmostEqual(call["observation_soc_feature"], 0.61)
        self.assertAlmostEqual(call["observation_soh_feature"], 0.83)

    def test_previous_candidate_state_is_reused_for_next_step_and_retry(self):
        first = self.decide()
        next_window = _window(2)[1:]
        constraints = _constraints()
        constraints["step_index"] = 1
        second = self.decide(window=next_window, constraints=constraints)
        call = self.strategy.calls[-1]
        self.assertEqual(call["observation_previous_price"], first["price_yuan_per_kwh"])
        self.assertEqual(call["observation_previous_storage_kw"], first["storage_kw"])
        retry = self.decide(window=copy.deepcopy(next_window), constraints=constraints)
        self.assertEqual(second, retry)
        self.assertEqual(self.strategy.calls[-1]["observation_sha256"], call["observation_sha256"])

    def test_state_site_timestamp_and_checkpoint_are_required(self):
        with self.assertRaisesRegex(ValueError, "explicit existing Agent checkpoint"):
            CheckpointSiteStrategy("site-1", None, LEVELS)
        with self.assertRaises(ValueError):
            CheckpointSiteStrategy("site-1", CHECKPOINT, [1.0, 2.0])
        with self.assertRaisesRegex(ValueError, "site ID"):
            self.strategy.decide("site-2", _window(), {"soc": 0.5, "soh": 0.98}, _constraints())
        with self.assertRaisesRegex(ValueError, "current soc and soh"):
            self.strategy.decide("site-1", _window(), {"soc": 0.5}, _constraints())
        bad = _window()
        bad[0]["timestamp"] = "2026-01-01T12:00:00"
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            self.decide(window=bad)

    def test_rejects_missing_quality_illegal_price_and_short_window(self):
        bad = _window()
        del bad[0]["source_quality"]
        with self.assertRaisesRegex(ValueError, "source_quality"):
            self.decide(window=bad)
        constraints = _constraints()
        constraints["price_levels"] = [1.0, 2.0]
        with self.assertRaisesRegex(ValueError, "locked checkpoint"):
            self.decide(constraints=constraints)
        with self.assertRaisesRegex(ValueError, "non-empty sequence"):
            self.decide(window=[])

    def test_checkpoint_round_trip_and_manifest_metadata(self):
        self.assertEqual(self.strategy.checkpoint_sha256, EXPECTED_CHECKPOINT_SHA256)
        self.assertEqual(self.strategy.checkpoint_revision, "74ee75b32acf775902dcaec0d72ad2bb6d3a1e38")
        self.assertEqual(self.strategy.strategy_metadata["price_levels"], LEVELS)
        action = self.decide()
        self.assertIn(action["price_yuan_per_kwh"], self.strategy.strategy_metadata["price_levels"])


if __name__ == "__main__":
    unittest.main()
