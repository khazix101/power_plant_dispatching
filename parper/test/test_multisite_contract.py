import json
import unittest

from src.multisite.contract import ContractError, CONTRACT_VERSION, MultiSiteInput


def valid_payload():
    required = {
        "pv_forecast_kw": (1.0, "kW", "forecast"),
        "wind_forecast_kw": (0.0, "kW", "forecast"),
        "load_forecast_kw": (3.0, "kW", "forecast"),
        "ev_forecast_kw": (1.0, "kW", "forecast"),
        "price_yuan_per_kwh": (None, "CNY/kWh", "missing"),
        "soc": (0.5, "fraction", "observed"),
        "soh": (0.98, "fraction", "observed"),
        "reserve_margin_kwh": (2.0, "kWh", "observed"),
    }
    signals = {
        name: {"value": value, "unit": unit, "source": "synthetic-fixture", "status": status}
        for name, (value, unit, status) in required.items()
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "timezone": "Asia/Shanghai",
        "interval_minutes": 30,
        "prediction_horizon_steps": 2,
        "upstream_import_limit_kw": None,
        "upstream_export_limit_kw": None,
        "sites": [
            {"site_id": "site-1", "timezone": "Asia/Shanghai", "source": "synthetic-fixture"},
            {"site_id": "site-2", "timezone": "Asia/Shanghai", "source": "synthetic-fixture"},
        ],
        "tie_lines": [{"line_id": "link-1", "from_site": "site-1", "to_site": "site-2",
                       "import_limit_kw": 10.0, "export_limit_kw": 10.0,
                       "source": "synthetic-fixture", "loss_fraction": None}],
        "series": [
            {"site_id": site, "intervals": [
                {"timestamp": stamp, "signals": signals}
                for stamp in ("2026-01-01T00:00:00+08:00", "2026-01-01T00:30:00+08:00")
            ]}
            for site in ("site-1", "site-2")
        ],
    }


class MultiSiteContractTests(unittest.TestCase):
    def test_two_site_round_trip(self):
        original = valid_payload()
        loaded = MultiSiteInput.from_dict(original)
        wire = json.loads(json.dumps(loaded.to_dict()))
        restored = MultiSiteInput.from_dict(wire)
        self.assertEqual([s.site_id for s in restored.sites], ["site-1", "site-2"])
        self.assertEqual(restored.interval_minutes, 30)
        self.assertEqual(restored.series[0].intervals[0].signals["price_yuan_per_kwh"].status, "missing")

    def assert_invalid(self, payload, message):
        with self.assertRaisesRegex(ContractError, message):
            MultiSiteInput.from_dict(payload)

    def test_rejects_wrong_unit(self):
        payload = valid_payload()
        payload["series"][0]["intervals"][0]["signals"]["pv_forecast_kw"]["unit"] = "MW"
        self.assert_invalid(payload, "unit must be kW")

    def test_rejects_timezone_offset_mismatch(self):
        payload = valid_payload()
        payload["series"][0]["intervals"][0]["timestamp"] = "2026-01-01T00:00:00Z"
        self.assert_invalid(payload, "offset does not match declared timezone")

    def test_rejects_missing_required_field(self):
        payload = valid_payload()
        del payload["series"][0]["intervals"][0]["signals"]["ev_forecast_kw"]
        self.assert_invalid(payload, "missing required field 'ev_forecast_kw'")

    def test_rejects_naive_timestamp(self):
        payload = valid_payload()
        payload["series"][0]["intervals"][0]["timestamp"] = "2026-01-01T00:00:00"
        self.assert_invalid(payload, "explicit UTC offset")

    def test_rejects_duplicate_timestamp(self):
        payload = valid_payload()
        payload["series"][0]["intervals"][1]["timestamp"] = payload["series"][0]["intervals"][0]["timestamp"]
        self.assert_invalid(payload, "duplicate timestamps")

    def test_rejects_unknown_site_id(self):
        payload = valid_payload()
        payload["series"][0]["site_id"] = "site-unknown"
        self.assert_invalid(payload, "exactly match configured site IDs")

    def test_rejects_invalid_tie_line_limit(self):
        payload = valid_payload()
        payload["tie_lines"][0]["import_limit_kw"] = -1
        self.assert_invalid(payload, "import_limit_kw must be a known non-negative limit")

    def test_zero_tie_line_limit_disables_direction(self):
        payload = valid_payload()
        payload["tie_lines"][0]["import_limit_kw"] = 0
        self.assertEqual(MultiSiteInput.from_dict(payload).tie_lines[0].import_limit_kw, 0)

    def test_rejects_unmarked_null(self):
        payload = valid_payload()
        signal = payload["series"][0]["intervals"][0]["signals"]["pv_forecast_kw"]
        signal["value"] = None
        self.assert_invalid(payload, "null signal value requires status='missing'")

    def test_rejects_invalid_site_rating(self):
        payload = valid_payload()
        payload["sites"][0]["battery_capacity_kwh"] = 0
        self.assert_invalid(payload, "battery_capacity_kwh must be positive")

    def test_rejects_out_of_range_soc(self):
        payload = valid_payload()
        payload["series"][0]["intervals"][0]["signals"]["soc"]["value"] = 1.1
        self.assert_invalid(payload, "soc must be in \\[0, 1\\]")


if __name__ == "__main__":
    unittest.main()
