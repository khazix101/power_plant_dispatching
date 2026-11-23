"""D Stage-2 SiteStrategy adapter for the existing discrete price Agent.

The checkpoint remains the sole source of policy decisions.  This module only
maps the frozen D callback into the existing observation/game model and
returns a candidate action for D's independent safety projection.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import torch

from src.game.case import BatteryParams, EVUser, GameCase
from src.game.env import PricingEnv, Scenario
from src.game.price_agent import SCHEMA, load_policy, observation
from src.game.rl_env import RollingMDPState
from src.game.forecast import ForecastBundle

_FORECAST_FIELDS = ("pv_forecast_kw", "wind_forecast_kw", "load_forecast_kw", "ev_forecast_kw")
_BASE_FEATURES = 17


def _finite(value: Any, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{name} must be finite" + (" and nonnegative" if nonnegative else ""))
    return result


def _aware_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp string")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{name} must include a UTC offset")
    return stamp


def _checkpoint_horizon(features: int) -> int:
    remaining = int(features) - _BASE_FEATURES
    if remaining <= 0 or remaining % 4:
        raise ValueError("checkpoint feature count is incompatible with the price Agent observation schema")
    return remaining // 4


class CheckpointSiteStrategy:
    """Load a fixed B checkpoint and implement A v1 ``SiteStrategy``.

    Long D windows are safely prefix-truncated to the checkpoint's trained
    feature horizon.  A shorter-than-trained window is rejected; it is never
    padded or reshaped.  Per-call metadata records the conversion.
    """

    def __init__(self, site_id: str, checkpoint_path: str | Path,
                 price_levels: Sequence[float], action_factory=None):
        if not isinstance(site_id, str) or not site_id.strip():
            raise ValueError("site_id must be a non-empty string")
        if checkpoint_path is None:
            raise ValueError("an explicit existing Agent checkpoint is required")
        self.checkpoint_path = Path(checkpoint_path).expanduser().resolve()
        if not self.checkpoint_path.is_file():
            raise FileNotFoundError(f"Agent checkpoint not found: {self.checkpoint_path}")
        self.levels = tuple(_finite(v, "price_levels item") for v in price_levels)
        if not self.levels or len(set(self.levels)) != len(self.levels):
            raise ValueError("price_levels must be non-empty and unique")
        self.policy = load_policy(self.checkpoint_path, self.levels)
        self.policy.eval()
        self.site_id = site_id
        self.action_factory = action_factory
        self.checkpoint_sha256 = hashlib.sha256(self.checkpoint_path.read_bytes()).hexdigest()
        self.checkpoint_revision = None
        manifest_path = self.checkpoint_path.parent / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected = manifest.get("result_files", {}).get(self.checkpoint_path.name)
            if expected is not None and expected != self.checkpoint_sha256:
                raise ValueError("checkpoint hash does not match its training manifest")
            self.checkpoint_revision = manifest.get("source_revision")
        self.feature_horizon = _checkpoint_horizon(self.policy.body[0].in_features)
        self.action_horizon = int(self.policy.T)
        if self.action_horizon != self.feature_horizon:
            raise ValueError("checkpoint feature and action horizons do not match")
        self.calls: list[dict[str, Any]] = []
        self._trace_sha256: str | None = None
        self._last_step_index: int | None = None
        self._last_price: float | None = None
        self._last_storage_kw = 0.0
        self._last_call_input: tuple[float | None, float] | None = None
        self.strategy_metadata = {
            "strategy_version": SCHEMA,
            "checkpoint_name": self.checkpoint_path.name,
            "checkpoint_sha256": self.checkpoint_sha256,
            "checkpoint_revision": self.checkpoint_revision,
            "price_levels": list(self.levels),
            "checkpoint_feature_horizon": self.feature_horizon,
            "checkpoint_action_horizon": self.action_horizon,
            "forecast_mapping": "current-prefix truncation; shorter windows rejected",
        }
        identity = {"strategy_version": SCHEMA, "checkpoint_sha256": self.checkpoint_sha256,
                    "price_levels": list(self.levels), "feature_horizon": self.feature_horizon,
                    "action_horizon": self.action_horizon}
        self.strategy_metadata["strategy_sha256"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    def _validate_constraints(self, constraints: Mapping[str, Any]) -> tuple[float, dict[str, float]]:
        if not isinstance(constraints, Mapping):
            raise ValueError("constraints must be a mapping")
        required = {"contract_version", "trace_sha256", "step_index", "dt_hours", "assets",
                    "price_levels", "network_summary", "safety_summary"}
        missing = required - constraints.keys()
        if missing:
            raise ValueError(f"constraints missing required fields: {sorted(missing)}")
        if constraints["contract_version"] != "1.0.0":
            raise ValueError("unsupported contract_version")
        trace_hash = constraints["trace_sha256"]
        if not isinstance(trace_hash, str) or re.fullmatch(r"[0-9a-f]{64}", trace_hash) is None:
            raise ValueError("trace_sha256 must be a 64-character lowercase SHA-256")
        index = constraints["step_index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("step_index must be a non-negative integer")
        dt_hours = _finite(constraints["dt_hours"], "dt_hours")
        if dt_hours != 0.5:
            raise ValueError("contract v1 requires dt_hours=0.5")
        levels = constraints["price_levels"]
        if isinstance(levels, (str, bytes)) or not isinstance(levels, Sequence):
            raise ValueError("price_levels must be a sequence")
        supplied = tuple(_finite(v, "price_levels item") for v in levels)
        if supplied != self.levels:
            raise ValueError("price_levels do not match the locked checkpoint")

        raw_assets = constraints["assets"]
        if not isinstance(raw_assets, Mapping):
            raise ValueError("assets must be a mapping")
        asset_fields = ("battery_capacity_kwh", "battery_power_kw", "soc_min", "soc_reserve",
                        "soc_max", "eta_charge", "eta_discharge", "ev_service_limit_kw")
        missing_assets = set(asset_fields) - raw_assets.keys()
        if missing_assets:
            raise ValueError(f"assets missing required fields: {sorted(missing_assets)}")
        assets = {key: _finite(raw_assets[key], f"assets.{key}", nonnegative=True)
                  for key in asset_fields}
        if assets["battery_capacity_kwh"] <= 0 or assets["battery_power_kw"] <= 0:
            raise ValueError("battery capacity and power must be positive")
        if not 0 <= assets["soc_min"] <= assets["soc_reserve"] <= assets["soc_max"] <= 1:
            raise ValueError("asset SOC limits are inconsistent")
        if not (0 < assets["eta_charge"] <= 1 and 0 < assets["eta_discharge"] <= 1):
            raise ValueError("battery efficiencies must be in (0, 1]")
        if assets["ev_service_limit_kw"] <= 0:
            raise ValueError("ev_service_limit_kw must be positive")
        if not isinstance(constraints["network_summary"], Mapping):
            raise ValueError("network_summary must be an explicit mapping")
        safety = constraints["safety_summary"]
        if not isinstance(safety, Mapping) or not isinstance(safety.get("available"), bool):
            raise ValueError("safety_summary must include explicit availability")
        if not safety["available"] and (not isinstance(safety.get("reason"), str) or not safety["reason"].strip()):
            raise ValueError("unavailable safety_summary requires a reason")
        if safety["available"] and (not isinstance(safety.get("source"), str) or not safety["source"].strip()):
            raise ValueError("available safety_summary requires source provenance")
        return dt_hours, assets

    @staticmethod
    def _network_features(summary: Mapping[str, Any]) -> list[float]:
        available = summary.get("available")
        if not isinstance(available, bool):
            raise ValueError("network_summary.available must be explicit bool")
        if not available:
            if not isinstance(summary.get("reason"), str) or not summary["reason"].strip():
                raise ValueError("unavailable network_summary requires a reason")
            # The existing checkpoint schema has two numeric network features
            # and a separate availability bit; zero is an encoding placeholder.
            return [0.0, 0.0, 0.0]
        loading = _finite(summary.get("loading"), "network_summary.loading", nonnegative=True)
        voltage_margin = _finite(summary.get("voltage_margin"), "network_summary.voltage_margin")
        if not 0 <= loading <= 1 or not 0 <= voltage_margin <= 1:
            raise ValueError("network loading and voltage margin must be in [0, 1]")
        return [loading, voltage_margin, 1.0]

    @staticmethod
    def _validated_window(forecast_window: Any, dt_hours: float) -> tuple[list[dict[str, Any]], int]:
        if isinstance(forecast_window, (str, bytes)) or not isinstance(forecast_window, Sequence) or not forecast_window:
            raise ValueError("forecast_window must be a non-empty sequence")
        rows = []
        previous = None
        for index, raw in enumerate(forecast_window):
            if not isinstance(raw, Mapping):
                raise ValueError(f"forecast_window[{index}] must be a mapping")
            row = {"timestamp": raw.get("timestamp")}
            stamp = _aware_timestamp(row["timestamp"], f"forecast_window[{index}].timestamp")
            for field in _FORECAST_FIELDS:
                row[field] = _finite(raw.get(field), f"forecast_window[{index}].{field}", nonnegative=True)
            quality = raw.get("source_quality")
            if not isinstance(quality, Mapping):
                raise ValueError(f"forecast_window[{index}].source_quality must be a mapping")
            if all(isinstance(quality.get(field), Mapping) for field in _FORECAST_FIELDS):
                quality_items = [(field, quality[field]) for field in _FORECAST_FIELDS]
            else:
                # A-level fixture allows a row-level shared summary. D may
                # also attach a distinct source/status record per signal.
                quality_items = [("row", quality)]
            for field, item in quality_items:
                if not isinstance(item.get("source"), str) or not item["source"].strip():
                    raise ValueError(f"source_quality.{field}.source must be non-empty")
                status = item.get("status", item.get("quality"))
                if not isinstance(status, str) or not status.strip():
                    raise ValueError(f"source_quality.{field} requires status/quality")
                if status.lower() in {"missing", "stale", "invalid", "failed"}:
                    raise ValueError(f"source_quality.{field} is not usable: {status}")
            row["source_quality"] = quality
            if previous is not None and (stamp - previous).total_seconds() != dt_hours * 3600:
                raise ValueError("forecast_window timestamps must follow dt_hours cadence")
            rows.append(row)
            previous = stamp
        return rows, len(rows)

    def decide(self, site_id: str, forecast_window: Any, state: Any,
               constraints: Mapping[str, Any]):
        if site_id != self.site_id:
            raise ValueError("site ID mismatch")
        dt_hours, assets = self._validate_constraints(constraints)
        if not isinstance(state, Mapping) or not {"soc", "soh"} <= state.keys():
            raise ValueError("state must provide current soc and soh")
        soc = _finite(state["soc"], "state.soc")
        soh = _finite(state["soh"], "state.soh")
        if not 0 <= soc <= 1 or not 0 <= soh <= 1:
            raise ValueError("state soc and soh must be fractions in [0, 1]")
        trace_hash = constraints["trace_sha256"]
        step_index = constraints["step_index"]
        if self._trace_sha256 != trace_hash:
            self._trace_sha256 = trace_hash
            self._last_step_index = None
            self._last_price = None
            self._last_storage_kw = 0.0
            self._last_call_input = None
        elif step_index == 0 and self._last_step_index not in (None, 0):
            self._last_step_index = None
            self._last_price = None
            self._last_storage_kw = 0.0
            self._last_call_input = None
        elif self._last_step_index is not None and step_index < self._last_step_index:
            raise ValueError("step_index moved backwards within a replay trace")
        if self._last_step_index == step_index:
            # Retried callback must receive the exact same pre-action state.
            previous_price, previous_storage_kw = self._last_call_input
        else:
            previous_price, previous_storage_kw = self._last_price, self._last_storage_kw
        rows, supplied_horizon = self._validated_window(forecast_window, dt_hours)
        if len(rows) < self.feature_horizon:
            raise ValueError("forecast_window is shorter than the checkpoint feature horizon")
        current_stamp = _aware_timestamp(rows[0]["timestamp"], "forecast_window[0].timestamp")
        prefix = rows[:self.feature_horizon]
        used = self.feature_horizon
        pv = [row["pv_forecast_kw"] for row in prefix]
        wind = [row["wind_forecast_kw"] for row in prefix]
        load = [row["load_forecast_kw"] for row in prefix]
        ev = [row["ev_forecast_kw"] for row in prefix]
        # The frozen replay contract uses nominal capacity and keeps SOH as an
        # observation; matching D v1 avoids applying SOH twice to capacity.
        capacity = assets["battery_capacity_kwh"]
        reserve = capacity * assets["soc_reserve"]
        bundle = ForecastBundle(
            timestamp=current_stamp, horizon_hours=used, pv_forecast_kw=pv,
            wind_forecast_kw=wind, base_ev_load_forecast_kw=ev,
            non_ev_load_forecast_kw=load, current_ev_load_kw=ev[0],
            soc_current=soc, soh_current=soh, reserve_kwh=reserve,
        )
        network_features = self._network_features(constraints["network_summary"])
        quality_digest = hashlib.sha256(json.dumps(
            [row["source_quality"] for row in forecast_window[:self.feature_horizon]],
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        mdp_state = RollingMDPState(
            forecast_window=bundle, soc=soc, soh=soh,
            usable_capacity_kwh=capacity, reserve_kwh=reserve,
            backlog_kwh=0.0, previous_price=previous_price, time_index=step_index,
        )
        features = observation(mdp_state, previous_storage_kw=previous_storage_kw,
                               congestion=network_features)
        if len(features) != self.policy.body[0].in_features:
            raise ValueError("mapped observation does not match checkpoint feature shape")
        with torch.no_grad():
            selected_prices = self.policy.act_deterministic(torch.tensor(features, dtype=torch.float32)).cpu().numpy()
        prices = [float(value) for value in selected_prices.tolist()]
        if len(prices) != self.action_horizon or any(price not in self.levels for price in prices):
            raise RuntimeError("checkpoint produced an invalid action shape or price level")

        users = [EVUser(f"forecast_{i}", 1.9, value * dt_hours, (i,), max(value, 1.0), 0.0)
                 for i, value in enumerate(ev)]
        battery = BatteryParams(
            capacity_kwh=capacity, power_kw=assets["battery_power_kw"],
            charge_eff=assets["eta_charge"], discharge_eff=assets["eta_discharge"],
            soc0=soc, soc_min=assets["soc_min"], soc_max=assets["soc_max"],
            soc_end_min=assets["soc_min"],
        )
        case = GameCase(
            T=used, pv=pv, wind=wind, base_load=load, users=users,
            battery=battery, price_levels=list(self.levels), interval_hours=[dt_hours] * used,
            ev_service_limit_kw=assets["ev_service_limit_kw"],
        )
        result = PricingEnv(case, prefer="appsi_highs").evaluate_action(prices, Scenario(case, features))
        if not result.feasible:
            raise RuntimeError("price Agent safety plan failed validation")
        safe = result.safe_action
        requested_ev_kw = min(ev[0], max(0.0, sum(profile[0] for profile in result.response.values()) / dt_hours))
        storage_kw = float(safe["p_dis"][0] - safe["p_ch"][0])
        action_values = {
            "site_id": self.site_id,
            "timestamp": forecast_window[0]["timestamp"],
            "price_yuan_per_kwh": prices[0],
            "storage_kw": storage_kw,
            "requested_ev_kw": requested_ev_kw,
        }
        if action_values["timestamp"] != forecast_window[0]["timestamp"] or action_values["price_yuan_per_kwh"] not in self.levels:
            raise RuntimeError("adapter generated an invalid current-interval action")
        factory = self.action_factory
        if factory is None:
            # A mapping is an allowed SiteAction-equivalent at the wire edge,
            # and keeps B independently importable before worktree integration.
            action = action_values
        else:
            action = factory(**action_values)
        if self._last_step_index != step_index:
            self._last_call_input = (previous_price, previous_storage_kw)
        self._last_step_index = step_index
        self._last_price = action_values["price_yuan_per_kwh"]
        self._last_storage_kw = action_values["storage_kw"]
        self.calls.append({
            "trace_sha256": constraints["trace_sha256"],
            "step_index": constraints["step_index"],
            "timestamp": action_values["timestamp"],
            "forecast_horizon_received": supplied_horizon,
            "forecast_horizon_used": used,
            "forecast_horizon_truncated": supplied_horizon - used,
            "forecast_source_quality_sha256": quality_digest,
            "checkpoint_feature_horizon": self.feature_horizon,
            "feature_count": int(len(features)),
            "observation_sha256": hashlib.sha256(features.tobytes()).hexdigest(),
            "observation_soc": soc,
            "observation_soh": soh,
            "observation_soc_feature": float(features[4 * used]),
            "observation_soh_feature": float(features[4 * used + 1]),
            "observation_previous_price": previous_price,
            "observation_previous_storage_kw": previous_storage_kw,
            "network_summary_available": constraints["network_summary"]["available"],
            "safety_summary_sha256": hashlib.sha256(
                json.dumps(constraints["safety_summary"], sort_keys=True, allow_nan=False).encode()
            ).hexdigest(),
        })
        return action
