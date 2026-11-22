"""Freeze a chronological, leakage-aware Site A date split for future Agent work."""

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path.cwd()
EV = ROOT / "Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv"
WEATHER = ROOT / "Dataset/10.09/dataset_1.csv"
OUT = ROOT / "parper/agent_date_split.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


ev = pd.read_csv(EV, usecols=["location", "date"])
dates = sorted(set(ev.loc[ev.location == "Expressway Service District A", "date"].astype(str)))
weather = pd.read_csv(WEATHER, usecols=["time"])
weather_days = set(pd.to_datetime(weather.time, utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"))

def select(start, end):
    return [day for day in dates if start <= day <= end and day in weather_days]

partitions = {
    "train": select("2020-01-02", "2020-12-31"),
    "validation": select("2021-01-01", "2021-08-31"),
    "historically_inspected_excluded": select("2021-09-01", "2021-10-31"),
    "prospective_test": select("2021-11-01", "2021-12-30"),
}
assert all(partitions.values())
assert len(set(day for group in partitions.values() for day in group)) == sum(map(len, partitions.values()))
payload = {
    "status": "split_declared_agent_not_trained",
    "site": "Expressway Service District A",
    "timezone": "Asia/Shanghai",
    "source_ev_sha256": sha(EV), "source_weather_sha256": sha(WEATHER),
    "rule": "chronological; September-October 2021 excluded because used in first-draft diagnosis",
    "boundary_note": "Exclude 1 January 2020 and 31 December 2021 to avoid UTC/local weather edge effects; require all 24 hourly weather records at training time",
    "partitions": partitions,
    "counts": {name: len(group) for name, group in partitions.items()},
    "agent_training": "not_run", "hyperparameter_selection": "not_run",
    "test_evaluation": "not_run",
}
payload["split_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(OUT, payload["counts"], payload["split_sha256"])
