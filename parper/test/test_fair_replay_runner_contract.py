import copy
import unittest

from scripts.run_fair_replay_experiments import (
    ContractError,
    build_sample_manifest,
    default_method_configs,
    execute_common_trace,
    trace_hash,
    validate_manifest,
    validate_method_configs,
)


class FairReplayContractTests(unittest.TestCase):
    def setUp(self):
        self.manifest = build_sample_manifest()
        self.methods = default_method_configs(self.manifest)

    def test_sample_manifest_satisfies_schema_and_hash(self):
        validate_manifest(self.manifest)
        validate_method_configs(self.manifest, self.methods)

    def test_rejects_step_dt_mismatch(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["steps"][0]["dt_hours"] = 1.0
        manifest["trace_sha256"] = trace_hash(manifest)
        with self.assertRaisesRegex(ContractError, "differs from manifest"):
            validate_manifest(manifest)

    def test_rejects_modified_trace_hash(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["steps"][0]["pv_kw"] = 10.0
        with self.assertRaisesRegex(ContractError, "trace_sha256 mismatch"):
            validate_manifest(manifest)

    def test_rejects_method_ev_cap_mismatch(self):
        methods = copy.deepcopy(self.methods)
        methods[1]["ev_service_cap_kw"] = 1000.0
        with self.assertRaisesRegex(ContractError, "inconsistent ev_service_cap_kw"):
            validate_method_configs(self.manifest, methods)

    def test_rejects_method_residual_unit_mismatch(self):
        methods = copy.deepcopy(self.methods)
        methods[1]["residual_units"]["power_balance"] = "kWh"
        with self.assertRaisesRegex(ContractError, "inconsistent residual_units"):
            validate_method_configs(self.manifest, methods)

    def test_rejects_unisolated_ev_cap(self):
        manifest = build_sample_manifest(481.0)
        with self.assertRaisesRegex(ContractError, "isolated cap_480kw or cap_960kw"):
            validate_manifest(manifest)

    def test_shared_trace_execution_integrates_kw_by_dt(self):
        manifest = build_sample_manifest(480.0)
        manifest["steps"] = [
            {**manifest["steps"][0], "pv_kw": 20.0, "wind_kw": 0.0,
             "base_load_kw": 0.0, "ev_requested_kwh": 10.0},
            {**manifest["steps"][0], "step_index": 1, "timestamp": "2024-01-01T00:30:00",
             "pv_kw": 20.0, "wind_kw": 0.0, "base_load_kw": 0.0,
             "ev_requested_kwh": 10.0},
        ]
        manifest["trace_sha256"] = trace_hash(manifest)
        methods = default_method_configs(manifest)
        providers = {
            "method_a": lambda step, state: {"accepted_ev_kw": 20.0,
                                               "p_schedule_kw": 0.0,
                                               "price": 1.5},
            "method_b": lambda step, state: {"accepted_ev_kw": 20.0,
                                               "p_schedule_kw": 0.0,
                                               "price": 1.5},
        }
        result = execute_common_trace(manifest, methods, providers)
        summary = result["results"]["method_a"]["summary"]
        self.assertEqual(summary["steps_included"], 2)
        self.assertAlmostEqual(summary["requested_kwh"], 20.0)
        self.assertAlmostEqual(summary["accepted_kwh"], 20.0)
        self.assertAlmostEqual(summary["served_kwh"], 20.0, places=6)
        self.assertAlmostEqual(summary["energy_residual_kwh"], 0.0, places=6)
        self.assertEqual(result["trace_sha256"], manifest["trace_sha256"])

    def test_nonconverged_step_is_isolated(self):
        manifest = build_sample_manifest()
        manifest["steps"].append({**manifest["steps"][0], "step_index": 1,
                                   "timestamp": "2024-01-01T00:30:00"})
        manifest["trace_sha256"] = trace_hash(manifest)
        methods = default_method_configs(manifest)

        def provider(step, state):
            return {"accepted_ev_kw": 0.0, "p_schedule_kw": 0.0,
                    "converged": step["step_index"] == 0,
                    "residuals": {"price": 1.0}}

        result = execute_common_trace(manifest, methods,
                                      {"method_a": provider, "method_b": provider})
        summary = result["results"]["method_a"]["summary"]
        self.assertEqual(summary["steps_included"], 1)
        self.assertEqual(summary["steps_nonconverged_excluded"], 1)
        self.assertEqual(result["results"]["method_a"]["steps"][1]["execution_status"],
                         "nonconverged_skipped")


if __name__ == "__main__":
    unittest.main()
