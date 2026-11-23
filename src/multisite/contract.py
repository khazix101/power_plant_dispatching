"""Strict, dependency-free wire contract for multi-site scheduling v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTRACT_VERSION = "1.0.0"
INTERVAL_MINUTES = 30
SIGNAL_UNITS = {
    "pv_forecast_kw": "kW", "pv_actual_kw": "kW",
    "wind_forecast_kw": "kW", "wind_actual_kw": "kW",
    "load_forecast_kw": "kW", "load_actual_kw": "kW",
    "ev_forecast_kw": "kW", "ev_actual_kw": "kW",
    "price_yuan_per_kwh": "CNY/kWh",
    "grid_buy_price_yuan_per_kwh": "CNY/kWh",
    "grid_sell_price_yuan_per_kwh": "CNY/kWh",
    "ev_delivery_price_yuan_per_kwh": "CNY/kWh",
    "soc": "fraction", "soh": "fraction", "reserve_margin_kwh": "kWh",
    "grid_import_kw": "kW", "tie_flow_kw": "kW",
}


class ContractError(ValueError):
    """Stable validation error for malformed contract payloads."""


def _required(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ContractError(f"{context}: missing required field '{key}'")
    return mapping[key]


@dataclass(frozen=True)
class Signal:
    value: Optional[float]
    unit: str
    source: str
    status: str = "observed"
    assumption: Optional[str] = None

    def __post_init__(self) -> None:
        if self.unit not in set(SIGNAL_UNITS.values()):
            raise ContractError(f"unsupported signal unit: {self.unit}")
        if not self.source.strip():
            raise ContractError("signal source must be non-empty")
        if self.status not in {"observed", "forecast", "missing", "assumed", "stale", "invalid"}:
            raise ContractError(f"unsupported signal status: {self.status}")
        if self.status == "missing" and self.value is not None:
            raise ContractError("missing signal must have value=null")
        if self.status != "missing" and self.value is None:
            raise ContractError("null signal value requires status='missing'")
        if self.value is not None:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ContractError("signal value must be numeric or null")
            try:
                numeric = float(self.value)
            except (TypeError, ValueError) as exc:
                raise ContractError("signal value must be numeric or null") from exc
            if numeric != numeric or abs(numeric) == float("inf"):
                raise ContractError("signal value must be finite")
        if self.status == "assumed" and not self.assumption:
            raise ContractError("assumed signal requires an assumption description")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Signal":
        return cls(
            value=data.get("value"), unit=_required(data, "unit", "signal"),
            source=_required(data, "source", "signal"), status=data.get("status", "observed"),
            assumption=data.get("assumption"),
        )


@dataclass(frozen=True)
class SiteDefinition:
    site_id: str
    timezone: str
    source: str
    transformer_import_limit_kw: Optional[float] = None
    transformer_export_limit_kw: Optional[float] = None
    pv_rated_kw: Optional[float] = None
    wind_rated_kw: Optional[float] = None
    battery_capacity_kwh: Optional[float] = None
    battery_power_kw: Optional[float] = None
    battery_charge_efficiency: Optional[float] = None
    battery_discharge_efficiency: Optional[float] = None
    soc_min: Optional[float] = None
    soc_max: Optional[float] = None
    soh_min: Optional[float] = None
    ev_power_limit_kw: Optional[float] = None
    minimum_ev_service_fraction: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.site_id.strip():
            raise ContractError("site_id must be non-empty")
        if not self.source.strip():
            raise ContractError("site definition source must be non-empty")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ContractError(f"unknown IANA timezone: {self.timezone}") from exc
        for label, limit in (("transformer_import_limit_kw", self.transformer_import_limit_kw),
                             ("transformer_export_limit_kw", self.transformer_export_limit_kw),
                             ("pv_rated_kw", self.pv_rated_kw), ("wind_rated_kw", self.wind_rated_kw),
                             ("battery_capacity_kwh", self.battery_capacity_kwh),
                             ("battery_power_kw", self.battery_power_kw), ("ev_power_limit_kw", self.ev_power_limit_kw)):
            if limit is not None and (not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit <= 0):
                raise ContractError(f"{label} must be positive when specified")
        for label, efficiency in (("battery_charge_efficiency", self.battery_charge_efficiency),
                                  ("battery_discharge_efficiency", self.battery_discharge_efficiency)):
            if efficiency is not None and not 0 < efficiency <= 1:
                raise ContractError(f"{label} must be in (0, 1]")
        if self.soc_min is not None and not 0 <= self.soc_min <= 1:
            raise ContractError("soc_min must be in [0, 1]")
        if self.soc_max is not None and not 0 <= self.soc_max <= 1:
            raise ContractError("soc_max must be in [0, 1]")
        if self.soc_min is not None and self.soc_max is not None and self.soc_min > self.soc_max:
            raise ContractError("soc_min must not exceed soc_max")
        if self.soh_min is not None and not 0 <= self.soh_min <= 1:
            raise ContractError("soh_min must be in [0, 1]")
        if self.minimum_ev_service_fraction is not None and not 0 <= self.minimum_ev_service_fraction <= 1:
            raise ContractError("minimum_ev_service_fraction must be in [0, 1]")


@dataclass(frozen=True)
class TieLine:
    line_id: str
    from_site: str
    to_site: str
    import_limit_kw: Optional[float]
    export_limit_kw: Optional[float]
    source: str
    loss_fraction: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.line_id.strip() or self.from_site == self.to_site:
            raise ContractError("tie line requires an ID and two distinct endpoints")
        if not self.source.strip():
            raise ContractError("tie line source must be non-empty")
        for label, limit in (("import_limit_kw", self.import_limit_kw), ("export_limit_kw", self.export_limit_kw)):
            if limit is None or not isinstance(limit, (int, float)) or isinstance(limit, bool) or limit < 0:
                raise ContractError(f"tie line {label} must be a known non-negative limit")
        if self.loss_fraction is not None and not 0 <= self.loss_fraction < 1:
            raise ContractError("tie line loss_fraction must be in [0, 1)")


@dataclass(frozen=True)
class IntervalRecord:
    timestamp: str
    signals: Mapping[str, Signal]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntervalRecord":
        raw_signals = _required(data, "signals", "interval")
        if not isinstance(raw_signals, Mapping):
            raise ContractError("interval signals must be an object")
        signals = {}
        for name, raw in raw_signals.items():
            if name not in SIGNAL_UNITS:
                raise ContractError(f"unknown signal '{name}'")
            expected_unit = SIGNAL_UNITS[name]
            actual_unit = _required(raw, "unit", name)
            if actual_unit != expected_unit:
                raise ContractError(f"{name} unit must be {expected_unit}, got {actual_unit}")
            sig = Signal.from_dict(raw)
            signals[name] = sig
        for name in ("pv_forecast_kw", "wind_forecast_kw", "load_forecast_kw", "ev_forecast_kw",
                     "price_yuan_per_kwh", "grid_buy_price_yuan_per_kwh",
                     "grid_sell_price_yuan_per_kwh", "ev_delivery_price_yuan_per_kwh",
                     "soc", "soh", "reserve_margin_kwh"):
            if name not in signals:
                raise ContractError(f"interval: missing required field '{name}'")
        return cls(timestamp=_required(data, "timestamp", "interval"), signals=signals)


@dataclass(frozen=True)
class SiteSeries:
    site_id: str
    intervals: Sequence[IntervalRecord]


@dataclass(frozen=True)
class MultiSiteInput:
    contract_version: str
    timezone: str
    interval_minutes: int
    prediction_horizon_steps: int
    sites: Sequence[SiteDefinition]
    tie_lines: Sequence[TieLine]
    series: Sequence[SiteSeries]
    upstream_import_limit_kw: Optional[float] = None
    upstream_export_limit_kw: Optional[float] = None

    def __post_init__(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ContractError(f"contract_version must be {CONTRACT_VERSION}")
        if self.interval_minutes != INTERVAL_MINUTES:
            raise ContractError("interval_minutes must be 30")
        if self.prediction_horizon_steps <= 0:
            raise ContractError("prediction_horizon_steps must be positive")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ContractError(f"unknown IANA timezone: {self.timezone}") from exc
        ids = [site.site_id for site in self.sites]
        if not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
            raise ContractError("site definitions must contain 2-4 unique site IDs")
        if any(site.timezone != self.timezone for site in self.sites):
            raise ContractError("site timezone must match input timezone")
        if len(self.series) != len(ids) or {s.site_id for s in self.series} != set(ids):
            raise ContractError("series site IDs must exactly match configured site IDs")
        for label, limit in (("upstream_import_limit_kw", self.upstream_import_limit_kw),
                             ("upstream_export_limit_kw", self.upstream_export_limit_kw)):
            if limit is not None and limit <= 0:
                raise ContractError(f"{label} must be positive when specified")
        for line in self.tie_lines:
            if line.from_site not in ids or line.to_site not in ids:
                raise ContractError(f"tie line '{line.line_id}' references unknown site ID")
        reference = None
        for site_series in self.series:
            if not site_series.intervals:
                raise ContractError(f"site '{site_series.site_id}' has no intervals")
            if len(site_series.intervals) != self.prediction_horizon_steps:
                raise ContractError(f"site '{site_series.site_id}' interval count does not match horizon")
            stamps = []
            for interval in site_series.intervals:
                stamp = _parse_timestamp(interval.timestamp, self.timezone)
                for signal_name in ("soc", "soh"):
                    value = interval.signals[signal_name].value
                    if value is not None and not 0 <= value <= 1:
                        raise ContractError(f"{signal_name} must be in [0, 1]")
                if interval.signals["reserve_margin_kwh"].value is not None and interval.signals["reserve_margin_kwh"].value < 0:
                    raise ContractError("reserve_margin_kwh must be non-negative")
                stamps.append(stamp)
            if len(set(stamps)) != len(stamps):
                raise ContractError(f"site '{site_series.site_id}' has duplicate timestamps")
            if any(b - a != timedelta(minutes=INTERVAL_MINUTES) for a, b in zip(stamps, stamps[1:])):
                raise ContractError(f"site '{site_series.site_id}' timestamps must increase by 30 minutes")
            if reference is None:
                reference = stamps
            elif stamps != reference:
                raise ContractError("all sites must use the same timestamp axis")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MultiSiteInput":
        try:
            sites = [SiteDefinition(**v) for v in _required(data, "sites", "input")]
            lines = [TieLine(**v) for v in data.get("tie_lines", [])]
            series = []
            for item in _required(data, "series", "input"):
                intervals = [IntervalRecord.from_dict(v) for v in _required(item, "intervals", "site series")]
                series.append(SiteSeries(_required(item, "site_id", "site series"), intervals))
            return cls(
                contract_version=_required(data, "contract_version", "input"),
                timezone=_required(data, "timezone", "input"),
                interval_minutes=_required(data, "interval_minutes", "input"),
                prediction_horizon_steps=_required(data, "prediction_horizon_steps", "input"),
                sites=sites, tie_lines=lines, series=series,
                upstream_import_limit_kw=data.get("upstream_import_limit_kw"),
                upstream_export_limit_kw=data.get("upstream_export_limit_kw"),
            )
        except (TypeError, KeyError) as exc:
            raise ContractError(f"invalid input structure: {exc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_timestamp(value: str, timezone: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("timestamp must include an explicit UTC offset")
    try:
        expected = parsed.astimezone(ZoneInfo(timezone)).utcoffset()
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ContractError(f"unknown IANA timezone: {timezone}") from exc
    if parsed.utcoffset() != expected:
        raise ContractError(f"timestamp UTC offset does not match declared timezone {timezone}")
    return parsed.astimezone(ZoneInfo("UTC"))


@dataclass(frozen=True)
class SiteAction:
    site_id: str
    timestamp: str
    price_yuan_per_kwh: float  # Published/selected EV retail response price.
    storage_kw: float  # Positive means battery discharge; negative means charge.
    requested_ev_kw: Optional[float] = None


@dataclass(frozen=True)
class SiteResult:
    site_id: str
    timestamp: str
    status: str
    action: Optional[SiteAction] = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoordinationRequest:
    contract_version: str
    run_id: str
    input: MultiSiteInput
    config: Mapping[str, Any]


@dataclass(frozen=True)
class CoordinationResult:
    contract_version: str
    run_id: str
    status: str
    failure_code: Optional[str]
    ledger: Sequence[Mapping[str, Any]]
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SiteStrategy(Protocol):
    def decide(self, site_id: str, forecast: Any, state: Any, constraints: Mapping[str, Any]) -> SiteAction: ...


class Coordinator(Protocol):
    def coordinate(self, request: CoordinationRequest) -> CoordinationResult: ...
