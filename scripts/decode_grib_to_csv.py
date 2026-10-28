"""Decode a GRIB weather file into CSV files.

The default output is a time series for the grid point nearest to the
configured site. Use ``--all-grid-points`` when a complete latitude/longitude
grid is required.

Typical usage from the project root::

    D:\\miniconda3\\python.exe scripts\\decode_grib_to_csv.py

The script intentionally uses xarray/cfgrib instead of parsing GRIB bytes
directly, so it can handle the different coordinate layouts used by ERA5 and
similar products.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


VARIABLE_ALIASES = {
    "temperature_2m_k": ("t2m", "2t", "temperature_2m"),
    "relative_humidity_percent": ("r", "rh", "relative_humidity"),
    "precipitation_m": ("tp", "total_precipitation", "precipitation"),
    "wind_u_10m_ms": ("u10", "10u", "wind_u_10m"),
    "wind_v_10m_ms": ("v10", "10v", "wind_v_10m"),
    "surface_solar_radiation_j_m2": ("ssrd", "surface_solar_radiation"),
    "surface_pressure_pa": ("sp", "surface_pressure"),
}


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=root / "Dataset" / "d069f8bdd28e210ad8ec0d15d3d86316.grib",
        help="Input GRIB/GRIB2 file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "Dataset" / "weather_grib_point.csv",
        help="Output CSV path for the nearest grid point.",
    )
    parser.add_argument(
        "--all-grid-points",
        action="store_true",
        help="Export every grid point instead of one nearest point.",
    )
    parser.add_argument("--latitude", type=float, default=30.75)
    parser.add_argument("--longitude", type=float, default=120.75)
    parser.add_argument(
        "--dataset-index",
        type=int,
        default=0,
        help="Dataset index when the GRIB contains several hypercubes.",
    )
    return parser.parse_args()


def load_dataset(path: Path, dataset_index: int):
    try:
        import xarray as xr
    except ImportError as exc:
        raise SystemExit("xarray is required. Install it in the Miniconda environment.") from exc

    try:
        datasets = xr.backends.list_engines()
    except Exception:
        datasets = {}
    if "cfgrib" not in datasets:
        raise SystemExit(
            "The cfgrib xarray engine is unavailable. Run:\n"
            "  D:\\miniconda3\\Scripts\\conda.exe install -n base -c conda-forge cfgrib eccodes"
        )

    try:
        import cfgrib  # noqa: F401  # Registers the xarray engine.
    except ImportError as exc:
        raise SystemExit(
            "cfgrib is not installed. Run:\n"
            "  D:\\miniconda3\\Scripts\\conda.exe install -n base -c conda-forge cfgrib eccodes"
        ) from exc

    try:
        import cfgrib

        paths = cfgrib.open_datasets(str(path), indexpath="")
    except Exception as exc:
        raise SystemExit(f"Unable to decode GRIB file {path}: {exc}") from exc

    if not paths:
        raise SystemExit(f"No datasets found in {path}")
    if not 0 <= dataset_index < len(paths):
        raise SystemExit(f"--dataset-index must be between 0 and {len(paths) - 1}")
    return paths[dataset_index], len(paths)


def find_coord(dataset, names):
    for name in names:
        if name in dataset.coords:
            return name
        if name in dataset.dims:
            return name
    return None


def canonical_variables(dataset):
    selected = {}
    available = set(dataset.data_vars)
    for canonical, aliases in VARIABLE_ALIASES.items():
        for alias in aliases:
            if alias in available:
                selected[canonical] = alias
                break
    return selected


def convert_units(frame):
    """Add practical units while retaining raw values in separate columns."""
    import numpy as np

    if "temperature_2m_k" in frame:
        frame["temperature_2m_c"] = frame["temperature_2m_k"] - 273.15
    if "precipitation_m" in frame:
        frame["precipitation_mm"] = frame["precipitation_m"] * 1000.0
    if "wind_u_10m_ms" in frame and "wind_v_10m_ms" in frame:
        frame["wind_speed_10m_ms"] = np.hypot(
            frame["wind_u_10m_ms"], frame["wind_v_10m_ms"]
        )
        frame["wind_direction_10m_deg"] = (
            np.degrees(np.arctan2(-frame["wind_u_10m_ms"], -frame["wind_v_10m_ms"]))
            + 360.0
        ) % 360.0
    return frame


def main() -> int:
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"Input file does not exist: {args.input}")

    dataset, dataset_count = load_dataset(args.input, args.dataset_index)
    latitude = find_coord(dataset, ("latitude", "lat"))
    longitude = find_coord(dataset, ("longitude", "lon"))
    if latitude is None or longitude is None:
        raise SystemExit(
            "The GRIB dataset has no latitude/longitude coordinates. "
            f"Coordinates found: {list(dataset.coords)}"
        )

    if not args.all_grid_points:
        dataset = dataset.sel(
            {latitude: args.latitude, longitude: args.longitude}, method="nearest"
        )

    selected = canonical_variables(dataset)
    if not selected:
        raise SystemExit(
            "No recognised weather variables found. "
            f"Variables found: {list(dataset.data_vars)}"
        )

    frame = dataset[list(selected.values())].to_dataframe().reset_index()
    frame = frame.rename(columns={source: target for target, source in selected.items()})
    frame = convert_units(frame)

    if latitude in frame.columns:
        frame = frame.rename(columns={latitude: "latitude"})
    if longitude in frame.columns:
        frame = frame.rename(columns={longitude: "longitude"})
    frame.insert(0, "source_file", args.input.name)
    frame.to_csv(args.output, index=False, encoding="utf-8-sig")

    print(f"Decoded datasets: {dataset_count}; selected dataset: {args.dataset_index}")
    print(f"Variables: {', '.join(selected)}")
    print(f"Rows: {len(frame)}")
    print(f"Output: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
