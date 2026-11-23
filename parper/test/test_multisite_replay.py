"""Focused contract, accounting, failure-retention and repeatability tests."""

from __future__ import annotations

import copy
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.validation.multisite_replay import (
    LEDGER_TOLERANCE_KWH,
    ReplayContractError,
    adapt_coordinator,
    adapt_site_strategies,
    build_synthetic_manifest,
    input_sha256,
    reference_providers,
    run_paired_replay,
    write_replay_artifacts,
)


class MultiSiteReplayTests(unittest.TestCase):
    def setUp(self):
        self.manifest = build_synthetic_manifest(seed=17, step_count=4)

    def test_time_alignment_requires_common_timezone_aware_half_hour_axis(self):
        changed = copy.deepcopy(self.manifest)
        changed["series"][1]["intervals"][1]["timestamp"] = "2024-01-01T00:35:00+08:00"
        changed["input_sha256"] = input_sha256(changed)
        with self.assertRaisesRegex(ReplayContractError, "30-minute|same timestamp axis"):
            run_paired_replay(changed, {"fixed-price": reference_providers()["fixed-price"]})

    def test_site_definition_provenance_is_required(self):
        changed = copy.deepcopy(self.manifest)
        changed["sites"][0].pop("source")
        changed["input_sha256"] = input_sha256(changed)
        with self.assertRaisesRegex(ReplayContractError, "site definition requires source"):
            run_paired_replay(changed, {"fixed-price": reference_providers()["fixed-price"]})

    def test_missing_data_is_rejected_or_explicitly_marked_without_imputation(self):
        changed = copy.deepcopy(self.manifest)
        signal = changed["series"][0]["intervals"][1]["signals"]["pv_forecast_kw"]
        signal.update(value=None, status="missing")
        changed["input_sha256"] = input_sha256(changed)
        with self.assertRaisesRegex(ReplayContractError, "required data quality issue"):
            run_paired_replay(changed, {"fixed-price": reference_providers()["fixed-price"]})
        result = run_paired_replay(changed, {"fixed-price": reference_providers()["fixed-price"]},
                                   missing_policy="mark")
        rows = result["results"]["fixed-price"]["steps"]
        self.assertEqual(rows[1]["execution_status"], "input_missing_marked")
        self.assertIn("SYN-01.pv_forecast_kw:missing", rows[1]["quality_issues"])
        self.assertEqual(rows[1]["site_ledgers"], {})
        self.assertEqual(result["results"]["fixed-price"]["summary"]["steps_executed"], 3)

    def test_stale_input_can_be_marked_but_never_used_as_a_forecast(self):
        changed = copy.deepcopy(self.manifest)
        changed["series"][1]["intervals"][0]["signals"]["ev_forecast_kw"]["status"] = "stale"
        changed["input_sha256"] = input_sha256(changed)
        result = run_paired_replay(changed, {"fixed-price": reference_providers()["fixed-price"]},
                                   missing_policy="mark")
        step = result["results"]["fixed-price"]["steps"][0]
        self.assertEqual(step["execution_status"], "input_missing_marked")
        self.assertIn("SYN-02.ev_forecast_kw:stale", step["quality_issues"])

    def test_shared_import_cap_records_shortage_without_breaking_energy_identity(self):
        changed = copy.deepcopy(self.manifest)
        changed["upstream_import_limit_kw"] = 1.0
        for site in changed["series"]:
            for interval in site["intervals"]:
                for signal_name in ("pv_forecast_kw", "wind_forecast_kw"):
                    interval["signals"][signal_name]["value"] = 0.0
                interval["signals"]["load_forecast_kw"]["value"] = 10.0
                interval["signals"]["ev_forecast_kw"]["value"] = 20.0
        changed["input_sha256"] = input_sha256(changed)
        result = run_paired_replay(changed, {"fixed-price": reference_providers()["fixed-price"]})
        summary = result["results"]["fixed-price"]["summary"]
        self.assertGreater(summary["unserved_kwh"], 0.0)
        self.assertGreater(summary["base_load_unserved_kwh"], 0.0)
        self.assertTrue(summary["energy_ledger_passed"])

    def test_all_arms_share_trace_initial_assets_and_forecast_windows(self):
        seen = {"a": [], "b": []}
        def provider(label):
            def decide(context):
                seen[label].append({"trace": context["trace_sha256"],
                                    "windows": context["forecast_windows"],
                                    "initial": context["states"] if context["step_index"] == 0 else None,
                                    "assets": context["site_configs"]})
                return reference_providers()["fixed-price"](context)
            return decide
        result = run_paired_replay(self.manifest, {"method-a": provider("a"), "method-b": provider("b")})
        self.assertEqual(result["results"]["method-a"]["trace_sha256"],
                         result["results"]["method-b"]["trace_sha256"])
        self.assertEqual(seen["a"], seen["b"])
        self.assertEqual(len(seen["a"][0]["windows"]["SYN-01"]), 4)
        self.assertEqual(seen["a"][0]["initial"]["SYN-01"], {"soc": 0.55, "soh": 0.98})

    def test_accounting_closes_and_reports_requested_delivered_and_cost_metrics(self):
        result = run_paired_replay(self.manifest, reference_providers())
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["ranking_authorized"])
        for method in result["results"].values():
            self.assertEqual(method["status"], "completed")
            summary = method["summary"]
            self.assertTrue(summary["energy_ledger_passed"])
            self.assertLess(summary["max_abs_energy_residual_kwh"], LEDGER_TOLERANCE_KWH)
            for step in method["steps"]:
                system = step["system_ledger"]
                self.assertLess(abs(system["energy_residual_kwh"]), LEDGER_TOLERANCE_KWH)
                self.assertTrue(system["energy_identity_passed"])
                self.assertAlmostEqual(system["delivered_revenue_yuan"], system["user_cost_yuan"])
                for site in step["site_ledgers"].values():
                    self.assertLess(abs(site["energy_residual_kwh"]), LEDGER_TOLERANCE_KWH)
                    self.assertAlmostEqual(site["requested_kwh"],
                                           site["served_kwh"] + site["unserved_kwh"])
                    self.assertAlmostEqual(site["soc_change"],
                                           site["final_state"]["soc"] - site["initial_state"]["soc"])
            for key in ("requested_kwh", "served_kwh", "unserved_kwh", "delivered_revenue_yuan",
                        "user_cost_yuan", "curtailment_kwh", "storage_conversion_loss_kwh",
                        "network_loss_kwh", "degradation_cost_yuan", "terminal_states",
                        "constraint_violation_count", "projection_rate", "runtime_seconds"):
                self.assertIn(key, summary)

    def test_repeated_run_uses_identical_inputs_actions_and_ledgers(self):
        first = run_paired_replay(self.manifest, reference_providers())
        second = run_paired_replay(self.manifest, reference_providers())
        self.assertEqual(first["trace_sha256"], second["trace_sha256"])
        for name in first["results"]:
            left = copy.deepcopy(first["results"][name])
            right = copy.deepcopy(second["results"][name])
            left["summary"].pop("runtime_seconds")
            right["summary"].pop("runtime_seconds")
            for row in left["steps"] + right["steps"]:
                for key in ("step_runtime_seconds", "provider_runtime_seconds", "execution_runtime_seconds"):
                    row.pop(key, None)
            self.assertEqual(left, right)

    def test_failure_is_preserved_and_existing_run_directory_is_not_overwritten(self):
        def fail(context):
            if context["step_index"] == 1:
                return {"status": "infeasible", "solver_status": "infeasible",
                        "message": "synthetic retained failure"}
            return reference_providers()["fixed-price"](context)
        result = run_paired_replay(self.manifest, {"failure-arm": fail})
        arm = result["results"]["failure-arm"]
        self.assertEqual(arm["status"], "infeasible")
        self.assertEqual(arm["steps"][1]["execution_status"], "failed")
        self.assertEqual(arm["steps"][1]["failure"]["message"], "synthetic retained failure")
        self.assertEqual(arm["summary"]["steps_executed"], 1)
        target = Path(__file__).resolve().parents[2] / "local/results/d_validation" / f"test_append_only_{uuid4().hex}"
        try:
            write_replay_artifacts(result, self.manifest, target)
            ledger_before = (target / "execution_ledger.json").read_text(encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_replay_artifacts(result, self.manifest, target)
            # A repeat run cannot replace a failed run's ledger.
            self.assertEqual((target / "execution_ledger.json").read_text(encoding="utf-8"),
                             ledger_before)
        finally:
            if target.exists():
                allowed_root = (Path(__file__).resolve().parents[2] / "local/results/d_validation").resolve()
                if target.resolve().parent != allowed_root:
                    raise AssertionError("test cleanup target escaped its generated-artifact directory")
                shutil.rmtree(target)

    def test_projected_actions_are_counted_and_tie_lines_are_not_silently_ignored(self):
        def unsafe(context):
            return {site_id: {"timestamp": context["timestamp"],
                              "price_yuan_per_kwh": 3.0,
                              "requested_ev_kw": 999.0,
                              "storage_kw": 999.0}
                    for site_id in context["forecasts"]}
        result = run_paired_replay(self.manifest, {"unsafe": unsafe})
        summary = result["results"]["unsafe"]["summary"]
        self.assertGreater(summary["projection_rate"], 0)
        self.assertTrue(all(site["action"]["projected"]
                            for site in result["results"]["unsafe"]["steps"][0]["site_ledgers"].values()))
        changed = copy.deepcopy(self.manifest)
        changed["tie_lines"] = [{"line_id": "L1", "from_site": "SYN-01", "to_site": "SYN-02",
                                 "import_limit_kw": 10.0, "export_limit_kw": 10.0}]
        changed["input_sha256"] = input_sha256(changed)
        with self.assertRaisesRegex(ReplayContractError, "does not yet model tie_lines"):
            run_paired_replay(changed, {"fixed-price": reference_providers()["fixed-price"]})

    def test_site_strategy_and_coordinator_adapters_consume_the_common_request(self):
        class Strategy:
            def __init__(self):
                self.calls = []

            def decide(self, site_id, forecast, state, constraints):
                self.calls.append((forecast[0]["timestamp"], state["soc"],
                                   constraints["assets"]["battery_capacity_kwh"]))
                return {"site_id": site_id, "timestamp": forecast[0]["timestamp"],
                        "price_yuan_per_kwh": 1.8, "storage_kw": 0.0}
        strategies = {site_id: Strategy() for site_id in ("SYN-01", "SYN-02")}
        result = run_paired_replay(self.manifest, {"agent": adapt_site_strategies(strategies)})
        agent = result["results"]["agent"]
        self.assertEqual(agent["summary"]["steps_executed"], 4)
        self.assertIsNotNone(agent["summary"]["site_summaries"]["SYN-01"]["provider_runtime_seconds"])
        self.assertEqual(strategies["SYN-01"].calls[0][0], "2024-01-01T00:00:00+08:00")

        class Coordinator:
            def coordinate(self, request):
                self.last_request = request
                return {"status": "optimal", "solver_status": "optimal",
                        "actions": reference_providers()["fixed-price"](request)}
        coordinator = Coordinator()
        coordinated = run_paired_replay(
            self.manifest, {"coordinator": adapt_coordinator(coordinator)})
        self.assertEqual(coordinated["results"]["coordinator"]["steps"][0]["solver_status"],
                         "optimal")
        self.assertEqual(coordinator.last_request["input_payload"]["prediction_horizon_steps"], 1)


if __name__ == "__main__":
    unittest.main()
