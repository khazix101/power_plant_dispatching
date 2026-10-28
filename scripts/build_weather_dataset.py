"""Build per-grid-point weather time series from ERA5 GRIB files.

The input files are 2x2 regular latitude/longitude grids. The four grid
points are labelled 1..4 following the GRIB scanning order (west to east,
north to south):

    1: (30.75, 120.00)   2: (30.75, 120.25)
    3: (30.50, 120.00)   4: (30.50, 120.25)

Each grid point is written to its own CSV. Low cloud cover (``lcc``) is
intentionally dropped so that all years share the same schema.

Run with the dedicated Miniconda environment::

    D:\\miniconda3\\Scripts\\conda.exe run -n grib_decode ^
        python scripts\\build_weather_dataset.py
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta
from pathlib import Path

import eccodes


DEFAULT_INPUTS = [
    Path("Dataset") / "10.09" / "data.grib",
    Path("Dataset") / "10.09" / "4be969e0cf4eb24bf77b25e39779b8f1.grib",
]

DROP_VARIABLES = {"lcc"}

VARIABLE_COLUMNS = {
    "2t": "t2m_k",
    "2d": "d2m_k",
    "sp": "sp_pa",
    "10u": "u10_ms",
    "10v": "v10_ms",
    "100u": "u100_ms",
    "100v": "v100_ms",
    "ssrd": "ssrd_j_m2",
    "strd": "strd_j_m2",
    "tp": "tp_m",
    "tcc": "tcc",
}

BASE_COLUMNS = [
    "time",
    "grid_point",
    "latitude",
    "longitude",
    "t2m_c",
    "t2m_k",
    "d2m_c",
    "d2m_k",
    "sp_pa",
    "u10_ms",
    "v10_ms",
    "wind_speed_10m_ms",
    "u100_ms",
    "v100_ms",
    "wind_speed_100m_ms",
    "ssrd_j_m2",
    "strd_j_m2",
    "tp_mm",
    "tp_m",
    "tcc",
]


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        type=Path,
        nargs="+",
        default=[root / path for path in DEFAULT_INPUTS],
        help="GRIB input files (defaults cover 2020 and 2021).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "Dataset" / "10.09",
        help="Directory that receives dataset_1.csv ... dataset_4.csv.",
    )
    return parser.parse_args()


def message_time(gid):
    """Return the UTC validity time of a GRIB message."""
    try:
        date = eccodes.codes_get(gid, "validityDate")
        time = eccodes.codes_get(gid, "validityTime")
        return datetime.strptime(f"{date:08d}{time:04d}", "%Y%m%d%H%M")
    except Exception:
        date = eccodes.codes_get(gid, "dataDate")
        time = eccodes.codes_get(gid, "dataTime")
        step = eccodes.codes_get(gid, "forecastTime")
        base = datetime.strptime(f"{date:08d}{time:04d}", "%Y%m%d%H%M")
        return base + timedelta(hours=step)


def point_coordinates(gid, count):
    try:
        lats = eccodes.codes_get_array(gid, "latitudes")
        lons = eccodes.codes_get_array(gid, "longitudes")
        return [(float(lat), float(lon)) for lat, lon in zip(lats, lons)]
    except Exception:
        return [(None, None)] * count


def collect(inputs):
    records = {}
    for path in inputs:
        if not path.exists():
            raise SystemExit(f"Input GRIB file does not exist: {path}")
        print(f"Reading {path} ...", flush=True)
        with open(path, "rb") as handle:
            while True:
                gid = eccodes.codes_grib_new_from_file(handle)
                if gid is None:
                    break
                short = eccodes.codes_get(gid, "shortName")
                if short in DROP_VARIABLES:
                    eccodes.codes_release(gid)
                    continue
                column = VARIABLE_COLUMNS.get(short)
                if column is None:
                    eccodes.codes_release(gid)
                    continue
                stamp = message_time(gid)
                values = eccodes.codes_get_values(gid)
                coords = point_coordinates(gid, len(values))
                for index, value in enumerate(values):
                    key = (index, stamp)
                    row = records.get(key)
                    if row is None:
                        lat, lon = coords[index]
                        row = {
                            "time": stamp,
                            "grid_point": index + 1,
                            "latitude": lat,
                            "longitude": lon,
                        }
                        records[key] = row
                    row[column] = float(value)
                eccodes.codes_release(gid)
    return records


def derive(row):
    if "t2m_k" in row:
        row["t2m_c"] = round(row["t2m_k"] - 273.15, 3)
    if "d2m_k" in row:
        row["d2m_c"] = round(row["d2m_k"] - 273.15, 3)
    if "u10_ms" in row and "v10_ms" in row:
        row["wind_speed_10m_ms"] = round(
            (row["u10_ms"] ** 2 + row["v10_ms"] ** 2) ** 0.5, 4
        )
    if "u100_ms" in row and "v100_ms" in row:
        row["wind_speed_100m_ms"] = round(
            (row["u100_ms"] ** 2 + row["v100_ms"] ** 2) ** 0.5, 4
        )
    if "tp_m" in row:
        row["tp_mm"] = round(row["tp_m"] * 1000.0, 4)
    return row


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=BASE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Wrote {len(rows)} rows -> {path}")


def main() -> int:
    args = parse_args()
    records = collect(args.inputs)
    if not records:
        raise SystemExit("No supported variables were decoded from the inputs.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    points = {}
    for (index, _stamp), row in records.items():
        points.setdefault(index, []).append(row)

    for index in sorted(points):
        rows = sorted(points[index], key=lambda item: item["time"])
        for row in rows:
            derive(row)
        rows = [{column: row.get(column) for column in BASE_COLUMNS} for row in rows]
        write_csv(args.output_dir / f"dataset_{index + 1}.csv", rows)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
