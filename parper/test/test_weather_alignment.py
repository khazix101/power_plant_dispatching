"""Guard the hour-ending ERA5 solar-radiation join."""

import unittest
from pathlib import Path

import pandas as pd

from scripts.run_short_window_comparison import build_trace


class WeatherAlignmentTests(unittest.TestCase):
    def test_solar_radiation_uses_end_of_dispatch_hour(self):
        root = Path(__file__).resolve().parents[2]
        path = root / "Dataset/10.09/dataset_1.csv"
        steps, provenance = build_trace("Expressway Service District A", "2021-05-04", 10, 1, path)
        weather = pd.read_csv(path)
        local = pd.to_datetime(weather.time, utc=True).dt.tz_convert("Asia/Shanghai")
        source = weather.loc[local == pd.Timestamp("2021-05-04 11:00", tz="Asia/Shanghai")].iloc[0]
        expected_pv = 400 * min(1, max(0, source.ssrd_j_m2 / 3_600_000))
        self.assertAlmostEqual(steps[0]["pv_kw"], expected_pv)
        self.assertAlmostEqual(steps[1]["pv_kw"], expected_pv)
        self.assertIn("validity timestamp is interval end", provenance["ssrd_semantics"])


if __name__ == "__main__":
    unittest.main()
