"""Integration tests requiring A's shared v1 contract, never a local substitute."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

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
    cfg = dict(provenance={"data_class":"synthetic","source":"synthetic-c-fixture"},
               method="centralized",price_levels=[2.],
               service_gap={"max_ev_service_fraction_gap":.5},time_limit_seconds=60.,random_seed=0,
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
            self.assertEqual(row["solver_status"],"optimal")
            self.assertEqual(row["model_sha256"],result.metadata["model_sha256"])
            self.assertEqual(row["source_sha256"],result.metadata["source_sha256"])
            self.assertEqual(row["inputs"]["soc"]["source"],"synthetic-c-fixture")
            self.assertEqual(row["requested_action"]["price_yuan_per_kwh"],2.)
            self.assertEqual(result.metadata["solver_status"],"optimal")
            self.assertTrue(result.metadata["model_sha256"])
            self.assertTrue(result.metadata["source_sha256"])
            self.assertTrue(result.metadata["config_sha256"])
            self.assertFalse(result.metadata["safe_to_dispatch"])
            self.assertEqual(set(result.metadata["actions"]),{f"site{i+1}" for i in range(count)})
            schedule = {(r["site_id"],r["step"]):r for r in result.ledger if r["record_type"]=="site"}
            series = {s.site_id:s for s in request.input.series}
            for site_id, action in result.metadata["actions"].items():
                first = schedule[site_id,0]
                self.assertEqual(action["timestamp"],series[site_id].intervals[0].timestamp)
                self.assertIn(action["price_yuan_per_kwh"],request.config["price_levels"])
                self.assertEqual(action["storage_kw"],first["discharge"]-first["charge"])
                self.assertEqual(action["requested_ev_kw"],first["ev"])
            tie_rows = {r["line_id"]:r for r in result.ledger if r["record_type"]=="tie" and r["step"]==0}
            self.assertEqual(set(result.metadata["tie_line_flows_kw"]),set(tie_rows))
            for line_id, flow in result.metadata["tie_line_flows_kw"].items():
                self.assertEqual(flow,tie_rows[line_id]["forward"]-tie_rows[line_id]["reverse"])
            self.assertIn("input response price",result.metadata["price_semantics"])
            self.assertIn("demand-response",result.metadata["ev_service_semantics"])

    def test_independent_is_explicit_and_audited(self):
        request = request_fixture()
        request = replace(request,config=dict(request.config,method="independent"))
        result = CentralizedCoordinator().coordinate(request)
        self.assertEqual(result.metadata["method"],"independent")
        self.assertFalse(result.metadata["checks"]["passed"])

    def test_missing_unknown_and_illegal_price_fail(self):
        for kind in ("missing_price","unknown_loss","unknown_rating","unknown_upstream","illegal_price","reserve_mismatch"):
            request = request_fixture()
            payload = request.input.to_dict()
            if kind=="missing_price":
                sig = payload["series"][0]["intervals"][0]["signals"]["grid_buy_price_yuan_per_kwh"]
                sig.update(value=None,status="missing")
            elif kind=="unknown_loss":
                payload["tie_lines"][0]["loss_fraction"] = None
            elif kind=="unknown_rating":
                payload["sites"][0]["battery_power_kw"] = None
            elif kind=="unknown_upstream":
                payload["upstream_import_limit_kw"] = None
            elif kind=="illegal_price":
                payload["series"][0]["intervals"][0]["signals"]["price_yuan_per_kwh"]["value"] = 3.
            else:
                payload["series"][0]["intervals"][0]["signals"]["reserve_margin_kwh"]["value"] = 1.
            result = CentralizedCoordinator().coordinate(replace(request,input=MultiSiteInput.from_dict(payload)))
            self.assertEqual(result.status,"invalid_input",(kind,result.metadata))
            self.assertEqual(result.ledger,[])
            self.assertFalse(result.metadata["safe_to_dispatch"])
            self.assertEqual(result.metadata["solver_status"],"not_run")

    def test_required_config_fields_are_not_silently_defaulted(self):
        request = request_fixture()
        config = dict(request.config)
        config.pop("method")
        result = CentralizedCoordinator().coordinate(replace(request,config=config))
        self.assertEqual(result.status,"invalid_input")
        self.assertEqual(result.failure_code,"invalid_input_or_config")
        self.assertEqual(result.metadata["solver_status"],"not_run")
        self.assertNotIn("actions",result.metadata)

    def test_frozen_provenance_object_is_preserved(self):
        request = request_fixture()
        provenance = {"data_class":"synthetic","source":"synthetic-c-fixture"}
        request = replace(request,config=dict(request.config,provenance=provenance))
        result = CentralizedCoordinator().coordinate(request)
        self.assertEqual(result.status,"optimal",result.metadata)
        self.assertEqual(result.metadata["provenance"],provenance)

    def test_unmapped_method_or_service_policy_is_rejected(self):
        request = request_fixture()
        for config in (dict(request.config,method="future-method"),
                       dict(request.config,service_gap={"target":"caller-policy"}),
                       dict(request.config,provenance={"data_class":"synthetic","source":"fixture","extra":True})):
            result = CentralizedCoordinator().coordinate(replace(request,config=config))
            self.assertEqual(result.status,"invalid_input")
            self.assertEqual(result.failure_code,"invalid_input_or_config")
            self.assertEqual(result.metadata["solver_status"],"not_run")
            self.assertNotIn("actions",result.metadata)

    def test_frozen_config_domains_are_enforced(self):
        request = request_fixture()
        invalid_configs = (
            dict(request.config,price_levels=[2.,2.]),
            dict(request.config,price_levels=[0.]),
            dict(request.config,price_levels={"only":2.}),
            dict(request.config,time_limit_seconds=-1.),
            dict(request.config,service_gap={"max_ev_service_fraction_gap":1.1}),
            dict(request.config,sites={"site1":dict(request.config["sites"]["site1"],ev_unserved_cost=-1.),
                                       "site2":request.config["sites"]["site2"]}),
        )
        for config in invalid_configs:
            result = CentralizedCoordinator().coordinate(replace(request,config=config))
            self.assertEqual(result.status,"invalid_input",result.metadata)
            self.assertEqual(result.metadata["solver_status"],"not_run")

    def test_action_price_comes_from_its_own_site_input(self):
        request = request_fixture()
        payload = request.input.to_dict()
        payload["series"][1]["intervals"][0]["signals"]["price_yuan_per_kwh"]["value"] = 3.
        request = replace(request,input=MultiSiteInput.from_dict(payload),
                          config=dict(request.config,price_levels=[2.,3.]))
        result = CentralizedCoordinator().coordinate(request)
        self.assertEqual(result.status,"optimal",result.metadata)
        self.assertEqual(result.metadata["actions"]["site2"]["price_yuan_per_kwh"],3.)

    def test_timeout_and_infeasible_are_preserved_without_actions(self):
        request = request_fixture()
        timeout_result = dict(status="timeout",termination="maxTimeLimit",schedule=[],ties=[],checks={"passed":False},
                              reason="synthetic timeout")
        with patch("src.game.multisite_coordinator.solve_centralized",return_value=timeout_result):
            result = CentralizedCoordinator().coordinate(request)
        self.assertEqual(result.status,"timeout")
        self.assertEqual(result.failure_code,"timeout")
        self.assertEqual(result.metadata["solver_status"],"maxTimeLimit")
        self.assertNotIn("actions",result.metadata)

        payload = request.input.to_dict()
        payload["sites"][0]["transformer_import_limit_kw"] = 1.
        payload["series"][0]["intervals"][0]["signals"]["load_forecast_kw"]["value"] = 20.
        payload["series"][0]["intervals"][0]["signals"]["pv_forecast_kw"]["value"] = 0.
        infeasible_request = replace(request,input=MultiSiteInput.from_dict(payload))
        result = CentralizedCoordinator().coordinate(infeasible_request)
        self.assertEqual(result.status,"infeasible",result.metadata)
        self.assertEqual(result.failure_code,"infeasible")
        self.assertEqual(result.metadata["solver_status"],"infeasible")
        self.assertNotIn("actions",result.metadata)
        self.assertFalse(result.metadata["safe_to_dispatch"])

    def test_repeated_input_hash_and_schedule(self):
        request = request_fixture()
        a = CentralizedCoordinator().coordinate(request)
        b = CentralizedCoordinator().coordinate(request)
        self.assertEqual(a.metadata["input_config_sha256"],b.metadata["input_config_sha256"])
        self.assertEqual(a.ledger,b.ledger)


if __name__ == "__main__":
    unittest.main()
