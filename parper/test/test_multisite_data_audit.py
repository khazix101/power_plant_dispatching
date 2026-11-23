"""Read-only scope and quality checks for the tracked example-data audit."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.validation.data_audit import FILE_DICTIONARY, audit_site_data


class MultiSiteDataAuditTests(unittest.TestCase):
    def test_audit_lists_only_documented_example_files_and_no_site_claim(self):
        root = Path(__file__).resolve().parents[2]
        report = audit_site_data(root / "data")
        self.assertFalse(report["privacy"]["private_raw_data_read"])
        self.assertEqual(report["site_coverage"]["site_count"], 0)
        self.assertEqual({row["file"] for row in report["files"]},
                         {"A.csv", "B.csv", "C.csv", "D.csv", "E.csv"})
        for row in report["files"]:
            if row.get("status") == "missing":
                continue
            self.assertEqual(row["row_count"], 24 if row["file"] != "E.csv" else 30)
            self.assertTrue(row["sha256"])
        hourly = next(row for row in report["files"] if row["file"] == "A.csv")
        self.assertEqual(hourly["time"]["sampling_interval_seconds_mode"], 3600.0)
        self.assertEqual(hourly["time"]["naive_timestamp_count"], 24)
        for row in report["files"]:
            self.assertEqual(row["duplicate_row_count"], 0)
            self.assertTrue(all(column["missing_count"] == 0 for column in row["columns"].values()))
        self.assertEqual(hourly["time"]["missing_regular_samples_estimate"], 0)

    def test_audit_allowlist_is_limited_to_documented_example_files(self):
        self.assertEqual(tuple(FILE_DICTIONARY), ("A.csv", "B.csv", "C.csv", "D.csv", "E.csv"))
        root = Path(__file__).resolve().parents[2]
        with self.assertRaisesRegex(ValueError, "Dataset/.*excluded"):
            audit_site_data(root / "Dataset")


if __name__ == "__main__":
    unittest.main()
