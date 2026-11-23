"""Read-only audit for the tracked example profiles (never scans Dataset/)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


FILE_DICTIONARY = {
    "A.csv": {"meaning": "wind power profile", "columns": {"power_kW": "kW"},
              "data_class": "forecast_example"},
    "B.csv": {"meaning": "photovoltaic power profile", "columns": {"power_kW": "kW"},
              "data_class": "forecast_example"},
    "C.csv": {"meaning": "base EV/load profile; EV vs total-load semantics need confirmation",
              "columns": {"power_kW": "kW"}, "data_class": "forecast_example"},
    "D.csv": {"meaning": "weather profile", "columns": {"temperature_c": "degC",
              "weather_type": "category"}, "data_class": "forecast_example"},
    "E.csv": {"meaning": "holiday calendar", "columns": {"date": "local date"},
              "data_class": "calendar_example"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp_audit(values: pd.Series) -> dict[str, Any]:
    parsed = []
    explicit_offset = []
    for raw in values.tolist():
        if pd.isna(raw):
            parsed.append(pd.NaT)
            explicit_offset.append(False)
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            parsed.append(pd.Timestamp(stamp))
            explicit_offset.append(stamp.tzinfo is not None and stamp.utcoffset() is not None)
        except (TypeError, ValueError):
            parsed.append(pd.NaT)
            explicit_offset.append(False)
    valid = [stamp for stamp in parsed if not pd.isna(stamp)]
    if valid:
        # This example data is homogeneous, but normalize aware stamps to UTC
        # before measuring elapsed sampling time.
        normalized = [stamp.tz_convert("UTC") if stamp.tzinfo else stamp for stamp in valid]
        unique_sorted = sorted(set(normalized))
        deltas = sorted((b - a).total_seconds() for a, b in zip(unique_sorted, unique_sorted[1:]))
        counts = {delta: deltas.count(delta) for delta in set(deltas)}
        mode_seconds = max(counts, key=lambda value: (counts[value], -value)) if counts else None
        span_seconds = (unique_sorted[-1] - unique_sorted[0]).total_seconds() if len(unique_sorted) > 1 else 0.0
        expected = int(round(span_seconds / mode_seconds)) + 1 if mode_seconds and mode_seconds > 0 else len(unique_sorted)
        unique_count = len(unique_sorted)
        gap_count = max(0, expected - unique_count)
        non_increasing = sum(1 for left, right in zip(normalized, normalized[1:]) if right <= left)
    else:
        mode_seconds = None
        expected = len(valid)
        gap_count = 0
        non_increasing = 0
    return {
        "start": str(valid[0]) if valid else None,
        "end": str(valid[-1]) if valid else None,
        "parsed_count": len(valid),
        "invalid_timestamp_count": len(values) - len(valid),
        "explicit_timezone_offset_count": sum(explicit_offset),
        "naive_timestamp_count": sum(1 for ok, stamp in zip(explicit_offset, parsed)
                                      if not ok and not pd.isna(stamp)),
        "sampling_interval_seconds_mode": mode_seconds,
        "non_increasing_time_step_count": non_increasing,
        "expected_regular_samples": expected,
        "missing_regular_samples_estimate": gap_count,
        "duplicate_timestamp_count": len(valid) - len(set(valid)),
    }


def audit_file(path: Path) -> dict[str, Any]:
    """Audit one known example CSV without opening any private/raw dataset."""
    info = FILE_DICTIONARY.get(path.name, {"meaning": "unclassified file", "columns": {},
                                            "data_class": "unknown"})
    frame = pd.read_csv(path)
    column_report: dict[str, Any] = {}
    for name in frame.columns:
        values = frame[name]
        missing = int(values.isna().sum())
        column_report[name] = {
            "unit": info["columns"].get(name, "unspecified"),
            "missing_count": missing,
            "missing_rate": (missing / len(frame)) if len(frame) else 0.0,
            "unique_count": int(values.nunique(dropna=True)),
        }

    if "valid_time" in frame:
        time_report = _timestamp_audit(frame["valid_time"])
        duplicate_rows = int(frame.duplicated(subset=["valid_time"]).sum())
    elif "date" in frame:
        time_report = _timestamp_audit(frame["date"])
        duplicate_rows = int(frame.duplicated(subset=["date"]).sum())
        time_report["time_kind"] = "calendar_date"
        time_report["sampling_interval_seconds_mode"] = None
        time_report["missing_regular_samples_estimate"] = None
        time_report["explicit_timezone_offset_count"] = 0
        time_report["naive_timestamp_count"] = 0
    else:
        time_report = {"start": None, "end": None, "sampling_interval_seconds_mode": None,
                       "explicit_timezone_offset_count": 0, "naive_timestamp_count": 0,
                       "missing_regular_samples_estimate": None,
                       "duplicate_timestamp_count": 0}
        duplicate_rows = int(frame.duplicated().sum())

    return {
        "file": path.name,
        "meaning": info["meaning"],
        "data_class": info["data_class"],
        "row_count": len(frame),
        "sha256": _sha256(path),
        "columns": column_report,
        "time": time_report,
        "duplicate_row_count": duplicate_rows,
        "station_id_present": "site_id" in frame.columns,
        "timezone_declared": False,
    }


def audit_site_data(data_dir: str | Path) -> dict[str, Any]:
    """Audit only the five documented small example files in ``data_dir``."""
    root = Path(data_dir).resolve()
    if any(part.casefold() == "dataset" for part in root.parts):
        raise ValueError("Dataset/ private or raw data roots are excluded from this audit")
    reports = []
    for name in FILE_DICTIONARY:
        path = root / name
        if path.exists():
            resolved_file = path.resolve()
            if any(part.casefold() == "dataset" for part in resolved_file.parts):
                raise ValueError(f"{name} resolves into Dataset/; private or raw data is excluded")
            reports.append(audit_file(path))
        else:
            reports.append({"file": name, "status": "missing", "data_class": FILE_DICTIONARY[name]["data_class"]})
    return {
        "schema_version": "d-data-quality-audit-v1",
        "scope": "tracked data/ examples only; Dataset/ and other private/raw roots excluded",
        "data_directory": str(root.resolve()),
        "files": reports,
        "site_coverage": {
            "site_ids_available": [],
            "site_count": 0,
            "interpretation": "The files are generic single-scenario profiles, not site-keyed records.",
        },
        "asset_parameter_sources": {
            "site_specific_parameters_present": False,
            "model_default_source": "src/game/dc_microgrid.py (generic simulation defaults)",
            "interpretation": "Code defaults are not verified physical parameters for any real site.",
        },
        "privacy": {
            "private_raw_data_read": False,
            "private_raw_data_root": "Dataset/ (excluded from this audit)",
            "site_privacy_classification_available": False,
        },
        "limitations": [
            "No station IDs or station-specific equipment metadata are present in data/.",
            "The tracked profiles are documented as small examples / forecast inputs, not measured station data.",
            "Timestamp fields in the hourly example profiles are timezone-naive.",
            "The 1-hour sample interval does not match the 30-minute multi-site contract.",
            "C.csv is called load in the loader and treated as base EV demand by one provider; its meaning needs owner confirmation.",
        ],
    }
