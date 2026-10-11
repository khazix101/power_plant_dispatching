"""标定高速服务区 EV 基础负荷与价格响应参数。

数据边界:
  - 只使用 Location Information 为 Expressway Service District* 的记录;
  - 充电会话按开始/结束时间跨小时分摊电量;
  - 基础负荷使用正常交易记录的小时聚合;
  - 价格响应只报告观测关联和条件回归，不宣称因果弹性;
  - Dataset/processed/ 由 .gitignore 排除，不提交原始或派生数据。

运行:
  D:\miniconda3\python.exe scripts/calibrate_ev_model.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "Dataset"
OUTPUT = DATASET / "processed" / "ev_calibration"


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def _flag(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(False, index=frame.index)
    return frame[column].astype(str).str.strip().isin({"1", "1.0", "true", "True"})


def load_sessions() -> pd.DataFrame:
    path = DATASET / "Charging_Data.csv"
    try:
        frame = pd.read_csv(path, low_memory=False, encoding="utf-8")
    except UnicodeDecodeError:
        # The supplied transaction export is commonly GB18030/GBK encoded
        # despite its .csv extension.
        frame = pd.read_csv(path, low_memory=False, encoding="gb18030")
    frame = frame[
        frame["Location Information"].astype(str).str.startswith("Expressway Service District")
    ].copy()

    frame["start_time"] = pd.to_datetime(frame["Start Time"], errors="coerce")
    frame["end_time"] = pd.to_datetime(frame["End Time"], errors="coerce")
    frame["energy_kwh"] = _number(frame, "Transaction power/kwh")
    frame["electricity_cost_yuan"] = _number(frame, "Electricity cost/Yuan")
    frame["service_charge_yuan"] = _number(frame, "Service charge/Yuan")
    frame["duration_hours"] = (
        frame["end_time"] - frame["start_time"]
    ).dt.total_seconds() / 3600.0
    frame["avg_power_kw"] = frame["energy_kwh"] / frame["duration_hours"]
    frame["effective_price_yuan_per_kwh"] = (
        frame["electricity_cost_yuan"] + frame["service_charge_yuan"]
    ) / frame["energy_kwh"]

    frame["is_full"] = _flag(frame, "is_full_charge")
    frame["is_user_stop"] = _flag(frame, "is_user_stop")
    frame["is_abnormal_flag"] = _flag(frame, "is_abnormal")
    fault_cols = [
        "is_nonsystem_fault", "is_system_issue", "is_EV_fault",
        "is_other_fault", "is_abnormal",
    ]
    frame["has_fault"] = False
    for column in fault_cols:
        frame["has_fault"] |= _flag(frame, column)

    frame["valid_time_energy"] = (
        frame["start_time"].notna()
        & frame["end_time"].notna()
        & frame["duration_hours"].between(1 / 60, 24.0, inclusive="both")
        & (frame["energy_kwh"] > 0)
        & np.isfinite(frame["avg_power_kw"])
        & (frame["avg_power_kw"] > 0)
    )
    # Do not silently discard high-power observations. Keep them flagged and
    # exclude only clear transaction anomalies from the normal baseline.
    p995 = frame.loc[frame["valid_time_energy"], "avg_power_kw"].quantile(0.995)
    frame["power_outlier"] = frame["avg_power_kw"] > max(float(p995), 1.0)
    frame["normal_for_baseline"] = frame["valid_time_energy"] & ~frame["has_fault"]
    frame["date"] = frame["start_time"].dt.date.astype("string")
    frame["hour"] = frame["start_time"].dt.hour
    frame["weekday"] = frame["start_time"].dt.weekday
    frame["location"] = frame["Location Information"]
    return frame


def allocate_energy_by_hour(frame: pd.DataFrame) -> pd.DataFrame:
    """按会话与小时的重叠时长分摊电量。"""
    rows: List[Dict] = []
    columns = [
        "location", "start_time", "end_time", "energy_kwh", "avg_power_kw",
        "is_full", "is_user_stop", "normal_for_baseline",
    ]
    for row in frame.loc[frame["valid_time_energy"], columns].itertuples(index=False):
        start = row.start_time.to_pydatetime()
        end = row.end_time.to_pydatetime()
        total_seconds = (end - start).total_seconds()
        cursor = start.replace(minute=0, second=0, microsecond=0)
        while cursor < end:
            nxt = cursor + pd.Timedelta(hours=1).to_pytimedelta()
            overlap = max(0.0, (min(end, nxt) - max(start, cursor)).total_seconds())
            if overlap > 0:
                share = overlap / total_seconds
                rows.append({
                    "location": row.location,
                    "date": cursor.date().isoformat(),
                    "hour": cursor.hour,
                    "energy_kwh": float(row.energy_kwh) * share,
                    "normal_energy_kwh": float(row.energy_kwh) * share
                    if row.normal_for_baseline else 0.0,
                    "session_count": 1,
                    "normal_session_count": int(row.normal_for_baseline),
                })
            cursor = nxt
    return pd.DataFrame(rows)


def fit_price_response(frame: pd.DataFrame) -> Dict:
    """拟合观测价格关联，含小时和站点固定效应，不作因果声明。"""
    data = frame.loc[
        frame["normal_for_baseline"]
        & frame["effective_price_yuan_per_kwh"].between(0, 10)
        & frame["energy_kwh"].between(0, 300)
    ].copy()
    if len(data) < 20:
        return {"status": "insufficient_data", "n": int(len(data))}

    price = data["effective_price_yuan_per_kwh"].to_numpy(float)
    energy = data["energy_kwh"].to_numpy(float)
    hours = pd.get_dummies(data["hour"].astype(str), prefix="h", dtype=float)
    sites = pd.get_dummies(data["location"].astype(str), prefix="s", dtype=float)
    X = np.column_stack([np.ones(len(data)), price, hours.to_numpy(), sites.to_numpy()])
    beta, *_ = np.linalg.lstsq(X, energy, rcond=None)
    pred = X @ beta
    ss_res = float(np.sum((energy - pred) ** 2))
    ss_tot = float(np.sum((energy - energy.mean()) ** 2))

    # Log-log elasticity is only an observational conditional association.
    positive = (price > 0) & (energy > 0)
    elasticity = float("nan")
    if positive.sum() >= 20:
        log_x = np.column_stack([
            np.ones(positive.sum()),
            np.log(price[positive]),
        ])
        elasticity = float(np.linalg.lstsq(
            log_x, np.log(energy[positive]), rcond=None
        )[0][1])
    return {
        "status": "ok",
        "n": int(len(data)),
        "price_min": float(price.min()),
        "price_median": float(np.median(price)),
        "price_max": float(price.max()),
        "conditional_energy_slope_kwh_per_yuan": float(beta[1]),
        "conditional_r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        "log_log_observational_elasticity": elasticity,
        "warning": "association with hour/site controls, not causal price elasticity",
    }


def build_outputs(frame: pd.DataFrame) -> Dict:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    valid = frame.loc[frame["valid_time_energy"]].copy()
    normal = frame.loc[frame["normal_for_baseline"]].copy()
    allocations = allocate_energy_by_hour(frame)

    hourly = allocations.groupby(["location", "date", "hour"], as_index=False).agg(
        energy_kwh=("energy_kwh", "sum"),
        normal_energy_kwh=("normal_energy_kwh", "sum"),
        session_count=("session_count", "sum"),
        normal_session_count=("normal_session_count", "sum"),
    )
    hourly["normal_avg_power_kw"] = hourly["normal_energy_kwh"]
    hourly.to_csv(OUTPUT / "expressway_hourly_ev_load.csv", index=False)

    frame.loc[valid.index, [
        "UserID", "Charging Post ID", "location", "start_time", "end_time",
        "energy_kwh", "duration_hours", "avg_power_kw",
        "effective_price_yuan_per_kwh", "is_full", "is_user_stop",
        "has_fault", "normal_for_baseline", "power_outlier",
    ]].to_csv(OUTPUT / "expressway_clean_sessions.csv", index=False)

    site_hour = normal.groupby(["location", "hour"], as_index=False).agg(
        sessions=("energy_kwh", "size"),
        energy_kwh=("energy_kwh", "sum"),
        mean_energy_kwh=("energy_kwh", "mean"),
        mean_duration_hours=("duration_hours", "mean"),
        mean_avg_power_kw=("avg_power_kw", "mean"),
        full_rate=("is_full", "mean"),
        user_stop_rate=("is_user_stop", "mean"),
    )
    site_hour.to_csv(OUTPUT / "expressway_user_hour_parameters.csv", index=False)

    # User-group parameters are empirical proxies, not causal utility values.
    normal = normal.copy()
    normal["energy_group"] = pd.qcut(
        normal["energy_kwh"], 3, labels=["low", "medium", "high"], duplicates="drop"
    )
    normal["duration_group"] = pd.qcut(
        normal["duration_hours"], 3, labels=["short", "medium", "long"], duplicates="drop"
    )
    groups = normal.groupby(["location", "energy_group", "duration_group"], observed=True).agg(
        sessions=("energy_kwh", "size"),
        mean_energy_kwh=("energy_kwh", "mean"),
        p95_energy_kwh=("energy_kwh", lambda x: x.quantile(0.95)),
        mean_duration_hours=("duration_hours", "mean"),
        p95_avg_power_kw=("avg_power_kw", lambda x: x.quantile(0.95)),
        full_rate=("is_full", "mean"),
        user_stop_rate=("is_user_stop", "mean"),
        mean_effective_price=("effective_price_yuan_per_kwh", "mean"),
    ).reset_index()
    # This is a conservative service floor proxy for the game, not a measured
    # battery requirement. A later inverse-choice calibration can replace it.
    groups["energy_min_kwh_proxy"] = 0.8 * groups["mean_energy_kwh"]
    groups["value_per_kwh_proxy_p75_price"] = groups["mean_effective_price"]
    groups["calibration_note"] = "observed price/energy proxy; not causal willingness-to-pay"
    groups.to_csv(OUTPUT / "ev_user_group_parameters.csv", index=False)

    price_bins = normal.copy()
    price_bins["price_bin"] = pd.qcut(
        price_bins["effective_price_yuan_per_kwh"], 8,
        labels=False, duplicates="drop"
    )
    price_summary = price_bins.groupby("price_bin", as_index=False).agg(
        sessions=("energy_kwh", "size"),
        mean_price=("effective_price_yuan_per_kwh", "mean"),
        mean_energy_kwh=("energy_kwh", "mean"),
        mean_duration_hours=("duration_hours", "mean"),
        full_rate=("is_full", "mean"),
        user_stop_rate=("is_user_stop", "mean"),
    )
    price_summary.to_csv(OUTPUT / "observed_price_response_bins.csv", index=False)

    response = fit_price_response(frame)
    summary = {
        "source": str(DATASET / "Charging_Data.csv"),
        "filter": "Location Information starts with Expressway Service District",
        "total_rows_after_filter": int(len(frame)),
        "valid_time_energy_rows": int(len(valid)),
        "normal_baseline_rows": int(len(normal)),
        "locations": sorted(frame["location"].dropna().unique().tolist()),
        "date_min": str(frame["start_time"].min()),
        "date_max": str(frame["start_time"].max()),
        "abnormal_rate": float(frame["has_fault"].mean()),
        "user_stop_rate_normal": float(normal["is_user_stop"].mean()) if len(normal) else None,
        "full_rate_normal": float(normal["is_full"].mean()) if len(normal) else None,
        "price_response_observation": response,
        "model_mapping": {
            "base_ev_load": "expressway_hourly_ev_load.normal_energy_kwh",
            "user_groups": "ev_user_group_parameters.csv",
            "price_response": "observed_price_response_bins.csv + conditional regression",
            "causal_warning": True,
        },
    }
    with open(OUTPUT / "calibration_summary.json", "w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2, default=str)
    return summary


def main():
    summary = build_outputs(load_sessions())
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
