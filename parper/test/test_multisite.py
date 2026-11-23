"""Synthetic optimization and independently reconstructed energy checks."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from src.game.dc_microgrid import DCSOCConfig, DCSOCState
from src.game.multisite import (NetworkInput, SiteInput, TieInput, build_model,
                               check_schedule, classify_termination,
                               solve_centralized, solve_independent, synthetic_case)


def hand_case(limit=6., upstream=0., loss=0.):
    donor = SiteInput("donor",(10.,),(0.,),(0.,),(0.,),(1.,),(0.,),(2.,),0.,0.,10.,0.)
    receiver = replace(donor,site_id="receiver",pv_kw=(0.,),ev_kw=(10.,),import_kw=10.)
    return NetworkInput((donor,receiver),(TieInput("link","donor","receiver",limit,limit,1-loss),),
                        (upstream,),(0.,))


class MultiSiteTest(unittest.TestCase):
    def optimal(self,case):
        r = solve_centralized(case)
        self.assertEqual(r["status"],"optimal",r)
        self.assertTrue(r["checks"]["passed"],r["checks"])
        return r

    def test_two_four_sites(self):
        for count in (2,4):
            with self.subTest(count=count):
                c = synthetic_case(count)
                r = self.optimal(c)
                independent = solve_independent(c)
                self.assertEqual(r["method"],"centralized")
                self.assertEqual(independent["method"],"independent")
                self.assertEqual(independent["status"],"optimal")
                self.assertLessEqual(r["objective"],independent["objective"]+1e-6)
                self.assertGreater(r["metrics"]["operating_value"],independent["metrics"]["operating_value"])
                self.assertTrue(r["provenance"]=="synthetic")
                self.assertFalse(r["electrical_feasibility_verified"])

    def test_hand_and_tightened_tie(self):
        for limit in (6.,2.,0.):
            r = self.optimal(hand_case(limit))
            self.assertAlmostEqual(r["metrics"]["ev_served_kwh"],limit*.5)
            self.assertAlmostEqual(r["metrics"]["curtailment_kwh"],(10-limit)*.5)
            self.assertAlmostEqual(r["objective"],(10-limit)*5-limit)

    def test_hand_upstream_and_loss(self):
        for upper,expected in ((4.,5.),(2.,4.),(0.,3.)):
            r = self.optimal(hand_case(upstream=upper))
            self.assertAlmostEqual(r["metrics"]["ev_served_kwh"],expected)
        r = self.optimal(hand_case(loss=.1))
        self.assertAlmostEqual(r["metrics"]["ev_served_kwh"],2.7)
        self.assertAlmostEqual(r["metrics"]["network_loss_kwh"],.3)

    def test_reverse_flow(self):
        c = hand_case()
        c = replace(c,ties=(TieInput("link","receiver","donor",1.,6.,1.),))
        r = self.optimal(c)
        self.assertAlmostEqual(r["ties"][0]["reverse"],6.)

    def test_storage_matches_existing_soc(self):
        s = SiteInput("s",(10.,0.),(0.,0.),(0.,0.),(0.,10.),(1.,1.),(0.,0.),(2.,2.),
                      0.,0.,10.,4.,soc_initial=.5,soc_terminal=.5)
        c = NetworkInput((s,),(),(0.,0.),(0.,0.))
        r = self.optimal(c)
        state = DCSOCState(DCSOCConfig(capacity_kwh=10.,power_kw=4.,soc_min=.1,soc_reserve=.1,soc_max=.9),soc=.5)
        for row in r["schedule"]:
            state.advance(row["discharge"]-row["charge"],.5)
            self.assertAlmostEqual(state.soc,row["soc_end"])
        self.assertGreater(r["metrics"]["storage_loss_kwh"],0)
        self.assertGreater(r["metrics"]["ev_served_kwh"],0)

    def test_priority(self):
        c = hand_case(limit=0.,upstream=2.)
        receiver = replace(c.sites[1],base_kw=(2.,),base_min_fraction=0.,ev_price=(10000.,))
        r = self.optimal(replace(c,sites=(c.sites[0],receiver)))
        row = next(x for x in r["schedule"] if x["site_id"]=="receiver")
        self.assertAlmostEqual(row["base"],2.)
        self.assertAlmostEqual(row["ev"],0.)

    def test_fairness_and_minimum(self):
        c = hand_case(limit=0.,upstream=4.)
        a = replace(c.sites[1],site_id="a",ev_price=(100.,),ev_min_fraction=.2)
        b = replace(a,site_id="b",ev_price=(1.,))
        c = replace(c,sites=(a,b),ties=(),service_gap=0.)
        r = self.optimal(c)
        self.assertAlmostEqual(r["metrics"]["ev_service_fraction_gap"],0.)
        self.assertAlmostEqual(r["metrics"]["ev_served_kwh"],2.)
        impossible = replace(c,sites=(replace(a,ev_min_fraction=.5),replace(b,ev_min_fraction=.5)))
        self.assertEqual(solve_centralized(impossible)["status"],"infeasible")

    def test_infeasible_not_fallback(self):
        c = hand_case(limit=0.)
        c = replace(c,sites=(c.sites[0],replace(c.sites[1],base_kw=(1.,))))
        r = solve_centralized(c)
        self.assertEqual(r["status"],"infeasible")
        self.assertEqual(r["schedule"],[])
        self.assertIn("No feasible",r["reason"])

    def test_independent_shared_violations_retained(self):
        c = synthetic_case()
        r = solve_independent(c)
        self.assertFalse(r["checks"]["passed"])
        self.assertGreater(r["checks"]["by_category"]["upstream_import"],0.)
        self.optimal(c)

    def test_auditor_detects_corruption(self):
        c = hand_case()
        r = self.optimal(c)
        r["schedule"][0]["charge"] = 20.
        checks = check_schedule(c,r)
        self.assertFalse(checks["passed"])
        self.assertGreater(checks["by_category"]["site_balance_kwh"],0.)
        self.assertGreater(checks["by_category"]["power_bounds"],0.)

    def test_invalid_inputs(self):
        c = synthetic_case()
        bad = [replace(c,dt_hours=float("nan")),replace(c,sites=(c.sites[0],c.sites[0])),
               replace(c,upstream_import_kw=()),replace(c,sites=(replace(c.sites[0],pv_kw=(None,)*4),)),
               replace(c,ties=(TieInput("x","unknown","site1",1,1),))]
        for case in bad:
            with self.assertRaises((ValueError,TypeError)):
                build_model(case)

    def test_ev_rating_preserves_unserved_request(self):
        c = hand_case(limit=10.)
        c = replace(c,sites=(c.sites[0],replace(c.sites[1],ev_power_kw=3.)))
        r = self.optimal(c)
        self.assertAlmostEqual(r["metrics"]["ev_served_kwh"],1.5)
        self.assertAlmostEqual(r["metrics"]["ev_unserved_kwh"],3.5)

    def test_statuses_and_solver_failure(self):
        for term,expected in (("optimal","optimal"),("infeasible","infeasible"),("maxTimeLimit","timeout"),("unbounded","solver_error"),("infeasibleOrUnbounded","solver_error")):
            self.assertEqual(classify_termination(term),expected)
        with patch("src.game.multisite.get_solver_name",side_effect=RuntimeError("unavailable")):
            r = solve_centralized(hand_case())
        self.assertEqual(r["status"],"solver_error")
        self.assertIn("unavailable",r["reason"])

    def test_real_timeout(self):
        r = solve_centralized(synthetic_case(4,96),time_limit=0.)
        self.assertEqual(r["status"],"timeout",{k:r[k] for k in ("status","solver","runtime_seconds")})
        self.assertEqual(r["schedule"],[])


if __name__ == "__main__":
    unittest.main()
