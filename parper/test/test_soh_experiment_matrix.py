import copy
import unittest

from scripts.run_soh_experiment_matrix import (
    MatrixContractError,
    PERFORMANCE_FIELDS,
    PHYSICAL_RESIDUAL_FIELDS,
    SOLVER_RESIDUAL_FIELDS,
    build_manifest,
    manifest_hash,
    summarize_results,
    validate_manifest,
    validate_result_record,
)


def _record(status="converged", seed=11):
    record = {
        "initial_soh": 0.8,
        "demand_quantile": "P90",
        "replicate": {"kind": "seed", "id": f"seed-{seed}", "seed": seed},
        "mechanisms": {
            "capacity_feedback_enabled": True,
            "degradation_cost_feedback_enabled": False,
        },
        "status": status,
        "failure_diagnostic": "iteration limit" if status == "nonconverged" else None,
    }
    for field in SOLVER_RESIDUAL_FIELDS + PHYSICAL_RESIDUAL_FIELDS:
        record[field] = 0.0
    for field in PERFORMANCE_FIELDS:
        record[field] = 0.5 if field in {"soc_end", "soh_end"} else 1.0
    if status != "nonconverged":
        record.pop("failure_diagnostic")
    return record


class SOHMatrixContractTests(unittest.TestCase):
    def test_default_manifest_covers_full_design(self):
        manifest = build_manifest()
        validate_manifest(manifest)
        self.assertEqual(len(manifest["cases"]), 144)
        self.assertEqual(manifest["execution"], "not_run")

    def test_manifest_rejects_legacy_combined_switch(self):
        manifest = build_manifest()
        manifest["cases"][0]["use_soh_in_game"] = True
        manifest["manifest_sha256"] = manifest_hash(manifest)
        with self.assertRaisesRegex(MatrixContractError, "use_soh_in_game"):
            validate_manifest(manifest)

    def test_manifest_rejects_missing_replicate(self):
        manifest = build_manifest()
        manifest["cases"] = [c for c in manifest["cases"]
                              if not (c["initial_soh"] == 0.7 and c["demand_quantile"] == "P10")]
        manifest["manifest_sha256"] = manifest_hash(manifest)
        with self.assertRaisesRegex(MatrixContractError, "has 0 replicates"):
            validate_manifest(manifest)

    def test_result_requires_all_physical_residuals(self):
        record = _record()
        record.pop("energy_balance_residual_kwh")
        with self.assertRaisesRegex(MatrixContractError, "energy_balance_residual_kwh"):
            validate_result_record(record)

    def test_nonconverged_is_excluded_from_performance_mean(self):
        converged = _record("converged", seed=11)
        nonconverged = _record("nonconverged", seed=23)
        nonconverged["served_kwh"] = 99.0
        summary = summarize_results([converged, nonconverged])[0]
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["converged"], 1)
        self.assertEqual(summary["nonconverged"], 1)
        self.assertEqual(summary["performance_n"], 1)
        self.assertEqual(summary["performance_mean_converged_only"]["served_kwh"], 1.0)

    def test_failed_result_requires_diagnostic(self):
        record = _record("failed")
        record["failure_diagnostic"] = ""
        with self.assertRaisesRegex(MatrixContractError, "failure_diagnostic"):
            validate_result_record(record)


if __name__ == "__main__":
    unittest.main()
