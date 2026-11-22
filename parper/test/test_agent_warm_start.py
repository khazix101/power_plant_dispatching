"""The learned agent may initialize prices, never replace constrained solving."""
from types import SimpleNamespace
import unittest

from src.game.agent_warm_start import PriceWarmStartAgent, case_features


class WarmStartAgentTests(unittest.TestCase):
    def case(self):
        return SimpleNamespace(T=1, interval_hours=[.5], users=[], pv=[100],
            wind=[20], base_load=[60], price_levels=[1.5,1.7,1.9],
            battery=SimpleNamespace(soc0=.5, capacity_kwh=1600,
                                    deg_cost_per_kwh=.1))

    def test_agent_only_changes_initialization_and_preserves_solver_options(self):
        case=self.case()
        features=case_features(case)[0]
        agent=PriceWarmStartAgent([dict(features=features,price=1.8)])
        calls=[]
        def exact_solver(received_case, **kwargs):
            calls.append((received_case,kwargs))
            return {'profit': 42, 'prices': [1.5]}
        result=agent.solve(case,exact_solver,init_prices=[1.5],max_sweeps=7,
                           network_filter=True)
        self.assertIs(calls[0][0],case)
        self.assertIn(calls[0][1]['init_prices'][0],case.price_levels)
        self.assertEqual(calls[0][1]['max_sweeps'],7)
        self.assertTrue(calls[0][1]['network_filter'])
        self.assertEqual(result['profit'],42)
        self.assertEqual(result['prices'],[1.5])

    def test_examples_ties_are_deterministic(self):
        case=self.case()
        x=case_features(case)[0]
        examples=[dict(features=x,price=1.5),dict(features=x,price=1.9)]
        self.assertEqual(PriceWarmStartAgent(examples,1).predict(case),[1.5])
        self.assertEqual(PriceWarmStartAgent(examples,2).predict(case),[1.7])


if __name__=='__main__':
    unittest.main()
