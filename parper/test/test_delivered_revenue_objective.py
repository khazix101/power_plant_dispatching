"""The leader objective must bill only delivered EV energy."""

import unittest

from src.game.baseline import solve_leader_fixed_profile
from src.game.case import BatteryParams, GameCase


class DeliveredRevenueObjectiveTests(unittest.TestCase):
    def test_zero_supply_gives_zero_ev_revenue_and_separate_shortfalls(self):
        case = GameCase(
            T=1, pv=[0.0], wind=[0.0], base_load=[5.0], users=[],
            battery=BatteryParams(capacity_kwh=100.0, power_kw=0.0,
                                  soc0=0.2, soc_min=0.2, soc_end_min=0.2),
            interval_hours=[0.5], penalty_shed=1.0,
        )
        result = solve_leader_fixed_profile(case, [10.0], 20.0, prices=[2.0])
        self.assertAlmostEqual(result["revenue_yuan"], 0.0)
        self.assertAlmostEqual(result["ev_shed"][0] * 0.5, 10.0)
        self.assertAlmostEqual(result["non_ev_shed"][0] * 0.5, 2.5)

    def test_non_ev_priority_when_supply_is_scarce(self):
        case = GameCase(
            T=1, pv=[5.0], wind=[0.0], base_load=[5.0], users=[],
            battery=BatteryParams(capacity_kwh=100.0, power_kw=0.0,
                                  soc0=0.2, soc_min=0.2, soc_end_min=0.2),
            interval_hours=[0.5], penalty_shed=100.0,
        )
        result = solve_leader_fixed_profile(case, [5.0], 10.0, prices=[2.0])
        self.assertAlmostEqual(result["non_ev_shed"][0], 0.0)
        self.assertAlmostEqual(result["ev_shed"][0], 10.0)


if __name__ == "__main__":
    unittest.main()
