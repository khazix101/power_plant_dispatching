"""Stage 2 adapter conformance fixtures around the unchanged v1 wire contract."""

import json
import math
import re
import unittest
from pathlib import Path

from src.multisite.contract import ContractError, CoordinationRequest, MultiSiteInput


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "multisite_integration_v1.json"
ACTION_FIELDS = {"site_id", "timestamp", "price_yuan_per_kwh", "storage_kw", "requested_ev_kw"}
CONFIG_FIELDS = {"provenance", "method", "price_levels", "service_gap", "time_limit_seconds", "sites"}
SITE_CONFIG_FIELDS = {
    "soc_terminal", "reserve_requirement_kwh", "degradation_per_kwh",
    "base_min_fraction", "base_unserved_cost", "ev_unserved_cost",
}
FORECAST_FIELDS = {"pv_forecast_kw", "wind_forecast_kw", "load_forecast_kw", "ev_forecast_kw"}


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _conform_action(action, site_id, timestamp, price_levels):
    if not isinstance(action, dict) or set(action) != ACTION_FIELDS:
        raise ContractError("action fields must match SiteAction")
    if action["site_id"] != site_id or action["timestamp"] != timestamp:
        raise ContractError("action site/timestamp must match current decision")
    if not _finite_number(action["price_yuan_per_kwh"]) or action["price_yuan_per_kwh"] not in price_levels:
        raise ContractError("action price must be finite and in price_levels")
    if not _finite_number(action["storage_kw"]):
        raise ContractError("action storage_kw must be finite kW")
    if action["requested_ev_kw"] is not None and not _finite_number(action["requested_ev_kw"]):
        raise ContractError("action requested_ev_kw must be finite kW or null")


def _conform_config(config, site_ids):
    if not isinstance(config, dict) or not CONFIG_FIELDS.issubset(config):
        raise ContractError("config is missing required coordinator fields")
    provenance = config["provenance"]
    if (not isinstance(provenance, dict) or set(provenance) != {"data_class", "source"}
            or not isinstance(provenance["data_class"], str)
            or provenance["data_class"] not in {"synthetic", "measured", "anonymized"}
            or not isinstance(provenance["source"], str) or not provenance["source"].strip()):
        raise ContractError("config provenance requires data_class and non-empty source")
    if not isinstance(config["method"], str) or config["method"] not in {"centralized", "independent"}:
        raise ContractError("config method must be centralized or independent")
    levels = config["price_levels"]
    if (not isinstance(levels, list) or not levels or not all(_finite_number(x) and x > 0 for x in levels)
            or len(set(levels)) != len(levels)):
        raise ContractError("config price_levels must be unique positive finite prices")
    service_gap = config["service_gap"]
    if (not isinstance(service_gap, dict) or set(service_gap) != {"max_ev_service_fraction_gap"}
            or not _finite_number(service_gap["max_ev_service_fraction_gap"])
            or not 0 <= service_gap["max_ev_service_fraction_gap"] <= 1):
        raise ContractError("config service_gap requires max_ev_service_fraction_gap in [0, 1]")
    if not _finite_number(config["time_limit_seconds"]) or config["time_limit_seconds"] < 0:
        raise ContractError("config time_limit_seconds must be nonnegative and finite")
    sites = config["sites"]
    if not isinstance(sites, dict) or set(sites) != set(site_ids):
        raise ContractError("config sites must match configured site IDs")
    for site_id, site in sites.items():
        if not isinstance(site, dict) or not SITE_CONFIG_FIELDS.issubset(site):
            raise ContractError(f"config sites[{site_id}] is missing required fields")
        for field in SITE_CONFIG_FIELDS:
            if not _finite_number(site[field]):
                raise ContractError(f"config sites[{site_id}].{field} must be finite")
        if not 0 <= site["soc_terminal"] <= 1 or not 0 <= site["base_min_fraction"] <= 1:
            raise ContractError(f"config sites[{site_id}] fraction is outside [0, 1]")
        if any(site[field] < 0 for field in SITE_CONFIG_FIELDS - {"soc_terminal", "base_min_fraction"}):
            raise ContractError(f"config sites[{site_id}] cost/requirement cannot be negative")
    return levels


def _conform_strategy_call(call, timestamp):
    required = {"site_id", "forecast_window", "state", "constraints"}
    if not isinstance(call, dict) or not required.issubset(call):
        raise ContractError("strategy call is missing required fields")
    if not isinstance(call["forecast_window"], list) or not call["forecast_window"]:
        raise ContractError("forecast_window must be a non-empty sequence")
    for index, row in enumerate(call["forecast_window"]):
        if not isinstance(row, dict) or not FORECAST_FIELDS.issubset(row) or not {"timestamp", "source_quality"}.issubset(row):
            raise ContractError("forecast row is missing required signal/provenance fields")
        if index == 0 and row["timestamp"] != timestamp:
            raise ContractError("forecast_window must start at current interval")
        if not isinstance(row["source_quality"], dict):
            raise ContractError("forecast source_quality must be an object")
        if not all(_finite_number(row[field]) for field in FORECAST_FIELDS):
            raise ContractError("forecast values must be finite numbers")
    state = call["state"]
    if not isinstance(state, dict) or not {"soc", "soh"}.issubset(state):
        raise ContractError("state requires soc and soh")
    if not all(_finite_number(state[field]) and 0 <= state[field] <= 1 for field in ("soc", "soh")):
        raise ContractError("state soc/soh must be fractions in [0, 1]")
    constraints = call["constraints"]
    fields = {"contract_version", "trace_sha256", "step_index", "dt_hours", "assets", "price_levels", "network_summary", "safety_summary"}
    if not isinstance(constraints, dict) or not fields.issubset(constraints):
        raise ContractError("constraints are missing required fields")
    if not re.fullmatch(r"[0-9a-f]{64}", constraints["trace_sha256"]):
        raise ContractError("trace_sha256 must be lowercase SHA-256 hex")
    if constraints["contract_version"] != "1.0.0" or constraints["step_index"] < 0 or constraints["dt_hours"] != 0.5:
        raise ContractError("constraints version/index/duration is invalid")
    if not isinstance(constraints["assets"], dict) or not isinstance(constraints["network_summary"], dict) or not isinstance(constraints["safety_summary"], dict):
        raise ContractError("constraints summaries must be objects")
    return constraints["price_levels"]


def _conform_first_interval_result(result, site_ids, line_ids, timestamp, price_levels, line_limits):
    metadata = result.get("metadata") if isinstance(result, dict) else None
    if not isinstance(metadata, dict) or metadata.get("safe_to_dispatch") is not False:
        raise ContractError("metadata.safe_to_dispatch must be false")
    actions = metadata.get("actions")
    flows = metadata.get("tie_line_flows_kw")
    if not isinstance(actions, dict) or set(actions) != set(site_ids):
        raise ContractError("metadata.actions must contain each configured site")
    for site_id, action in actions.items():
        _conform_action(action, site_id, timestamp, price_levels)
    if not isinstance(flows, dict) or set(flows) != set(line_ids):
        raise ContractError("metadata.tie_line_flows_kw must contain each configured line")
    for line_id, flow in flows.items():
        if not _finite_number(flow):
            raise ContractError("tie-line flow must be a finite signed kW number")
        reverse_limit, forward_limit = line_limits[line_id]
        if flow < -reverse_limit or flow > forward_limit:
            raise ContractError("tie-line flow exceeds directional limit")


class MultiSiteStage2ConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_constructs_wire_request_and_conforms(self):
        raw = self.fixture["request"]
        shared_input = MultiSiteInput.from_dict(raw["input"])
        request = CoordinationRequest(
            contract_version=raw["contract_version"], run_id=raw["run_id"],
            input=shared_input, config=raw["config"],
        )
        site_ids = {site.site_id for site in request.input.sites}
        levels = _conform_config(request.config, site_ids)
        call = self.fixture["strategy_call"]
        timestamp = request.input.series[0].intervals[0].timestamp
        strategy_levels = _conform_strategy_call(call, timestamp)
        self.assertEqual(strategy_levels, levels)
        self.assertEqual(call["constraints"]["contract_version"], request.contract_version)
        first = self.fixture["first_interval_result"]
        self.assertEqual(first["contract_version"], request.contract_version)
        self.assertEqual(first["run_id"], request.run_id)
        line_ids = {line.line_id for line in request.input.tie_lines}
        line_limits = {line.line_id: (line.import_limit_kw, line.export_limit_kw) for line in request.input.tie_lines}
        _conform_first_interval_result(first, site_ids, line_ids, timestamp, levels, line_limits)
        self.assertLess(first["metadata"]["tie_line_flows_kw"]["link-1"], 0, "negative flow is valid reverse flow")

    def test_rejects_missing_coordinator_config(self):
        with self.assertRaisesRegex(ContractError, "missing required coordinator fields"):
            _conform_config({"method": "centralized"}, {"site-1", "site-2"})

    def test_rejects_unexecutable_service_gap_or_unknown_method(self):
        config = json.loads(json.dumps(self.fixture["request"]["config"]))
        config["service_gap"] = {"target": "synthetic-test-only"}
        with self.assertRaisesRegex(ContractError, "max_ev_service_fraction_gap"):
            _conform_config(config, {"site-1", "site-2"})
        config = json.loads(json.dumps(self.fixture["request"]["config"]))
        config["service_gap"]["max_ev_service_fraction_gap"] = 0.5
        config["method"] = "unknown"
        with self.assertRaisesRegex(ContractError, "centralized or independent"):
            _conform_config(config, {"site-1", "site-2"})

    def test_rejects_wrong_action_timestamp_and_price(self):
        action = dict(self.fixture["first_interval_result"]["metadata"]["actions"]["site-1"])
        action["timestamp"] = "2026-01-01T00:30:00+08:00"
        with self.assertRaisesRegex(ContractError, "site/timestamp"):
            _conform_action(action, "site-1", "2026-01-01T00:00:00+08:00", [0.4, 0.6, 0.8])
        action["timestamp"] = "2026-01-01T00:00:00+08:00"
        action["price_yuan_per_kwh"] = 0.7
        with self.assertRaisesRegex(ContractError, "price_levels"):
            _conform_action(action, "site-1", "2026-01-01T00:00:00+08:00", [0.4, 0.6, 0.8])

    def test_rejects_invalid_signed_tie_line_flow_shape_or_limit(self):
        result = json.loads(json.dumps(self.fixture["first_interval_result"]))
        site_ids = {"site-1", "site-2"}
        line_ids = {"link-1"}
        line_limits = {"link-1": (4.0, 5.0)}
        args = (site_ids, line_ids, "2026-01-01T00:00:00+08:00", [0.4, 0.6, 0.8], line_limits)
        result["metadata"]["tie_line_flows_kw"]["link-1"] = "reverse"
        with self.assertRaisesRegex(ContractError, "finite signed kW"):
            _conform_first_interval_result(result, *args)
        result["metadata"]["tie_line_flows_kw"]["link-1"] = -4.1
        with self.assertRaisesRegex(ContractError, "directional limit"):
            _conform_first_interval_result(result, *args)


if __name__ == "__main__":
    unittest.main()
