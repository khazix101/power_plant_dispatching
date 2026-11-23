"""Integration tests requiring A's shared v1 contract, never a local substitute."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import unittest

try:
    from src.multisite.contract import CONTRACT_VERSION, MultiSiteInput, CoordinationRequest
    HAS_CONTRACT = True
except ModuleNotFoundError:
    HAS_CONTRACT = False

from src.game.multisite_coordinator import CentralizedCoordinator


def request_fixture(count=2):
    signals = {name:dict(value=value,unit=unit,source="synthetic-c-fixture",status="assumed",assumption="synthetic fixture")
               for name,value,unit in (("pv_forecast_kw",0.,"kW"),("wind_forecast_kw",0.,"kW"),
               ("load_forecast_kw",2.,"kW"),("ev_forecast_kw",8.,"kW"),("price_yuan_per_kwh",2.,"CNY/kWh"),
               ("grid_buy_price_yuan_per_kwh",1.,"CNY/kWh"),("grid_sell_price_yuan_per_kwh",0.,"CNY/kWh"),
               ("ev_delivery_price_yuan_per_kwh",2.,"CNY/kWh"),("soc",.5,"fraction"),("soh",.95,"fraction"),
               ("reserve_margin_kwh",4.,"kWh"))}
    ids = [f"site{i+1}" for i in range(count)]
    sites = [dict(site_id=s,timezone="Asia/Shanghai",source="synthetic-c-fixture",transformer_import_limit_kw=10.,transformer_export_limit_kw=1.,
                  pv_rated_kw=12.,wind_rated_kw=1.,battery_capacity_kwh=10.,battery_power_kw=2.,
                  battery_charge_efficiency=.95,battery_discharge_efficiency=.95,soc_min=.1,soc_max=.9,
                  soh_min=.7,ev_power_limit_kw=8.,minimum_ev_service_fraction=0.,metadata={"source":"synthetic-c-fixture"}) for s in ids]
    series = []
    for i,s in enumerate(ids):
        values = deepcopy(signals)
        values["pv_forecast_kw"]["value"] = 12. if i%2==0 else 0.
        series.append(dict(site_id=s,intervals=[dict(timestamp=(datetime.fromisoformat("2026-01-01T00:00:00+08:00")+timedelta(minutes=30*t)).isoformat(),signals=deepcopy(values)) for t in range(4)]))
    data = MultiSiteInput.from_dict(dict(contract_version=CONTRACT_VERSION,timezone="Asia/Shanghai",interval_minutes=30,
                                        prediction_horizon_steps=4,sites=sites,series=series,
                                        tie_lines=[dict(line_id=f"tie{i}",from_site=ids[i],to_site=ids[i+1],import_limit_kw=6.,
                                                        export_limit_kw=6.,loss_fraction=.02,source="synthetic-c-fixture") for i in range(count-1)],
                                        upstream_import_limit_kw=4.*count,upstream_export_limit_kw=1.))
    cfg = dict(provenance="synthetic",price_levels=[2.],service_gap=.5,time_limit_seconds=60.,random_seed=0,
               sites={s:dict(soc_terminal=.5,reserve_requirement_kwh=0.,degradation_per_kwh=.01,
                             base_min_fraction=1.,base_unserved_cost=100.,ev_unserved_cost=10.) for s in ids})
    return CoordinationRequest(CONTRACT_VERSION,"synthetic-c-test",data,cfg)


@unittest.skipUnless(HAS_CONTRACT,"Integrate A's src.multisite.contract first")
class CoordinatorTest(unittest.TestCase):
    def test_two_four_contract_ledger(self):
        for count in (2,4):
            request = request_fixture(count)
            result = CentralizedCoordinator().coordinate(request)
            self.assertEqual(result.status,"optimal",result.metadata)
            self.assertIsNone(result.failure_code)
            self.assertTrue(result.metadata["checks"]["passed"])
            self.assertEqual(len(result.ledger),2*count*4)
            self.assertEqual(result.metadata["capacity_basis"],"nominal")
            row = result.ledger[0]
            self.assertEqual(row["soh_start"],row["soh_end"])
            self.assertFalse(row["safe_to_dispatch"])
            self.assertEqual(row["inputs"]["soc"]["source"],"synthetic-c-fixture")
            self.assertEqual(row["requested_action"]["price_yuan_per_kwh"],2.)

    def test_independent_is_explicit_and_audited(self):
        request = request_fixture()
        request = replace(request,config=dict(request.config,method="independent"))
        result = CentralizedCoordinator().coordinate(request)
        self.assertEqual(result.metadata["method"],"independent")
        self.assertFalse(result.metadata["checks"]["passed"])

    def test_missing_unknown_and_illegal_price_fail(self):
        for kind in ("missing_price","unknown_loss","unknown_rating","illegal_price","reserve_mismatch"):
            request = request_fixture()
            payload = request.input.to_dict()
            if kind=="missing_price":
                sig = payload["series"][0]["intervals"][0]["signals"]["grid_buy_price_yuan_per_kwh"]
                sig.update(value=None,status="missing")
            elif kind=="unknown_loss":
                payload["tie_lines"][0]["loss_fraction"] = None
            elif kind=="unknown_rating":
                payload["sites"][0]["battery_power_kw"] = None
            elif kind=="illegal_price":
                payload["series"][0]["intervals"][0]["signals"]["price_yuan_per_kwh"]["value"] = 3.
            else:
                payload["series"][0]["intervals"][0]["signals"]["reserve_margin_kwh"]["value"] = 1.
            result = CentralizedCoordinator().coordinate(replace(request,input=MultiSiteInput.from_dict(payload)))
            self.assertEqual(result.status,"invalid_input",(kind,result.metadata))
            self.assertEqual(result.ledger,[])

    def test_repeated_input_hash_and_schedule(self):
        request = request_fixture()
        a = CentralizedCoordinator().coordinate(request)
        b = CentralizedCoordinator().coordinate(request)
        self.assertEqual(a.metadata["input_config_sha256"],b.metadata["input_config_sha256"])
        self.assertEqual(a.ledger,b.ledger)


if __name__ == "__main__":
    unittest.main()
