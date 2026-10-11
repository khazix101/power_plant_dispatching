from pathlib import Path

from src.game.calibration import load_hourly_base_ev_load


def test_calibration_loader_requires_processed_output():
    root = Path(__file__).resolve().parents[1] / "missing-calibration-root"
    try:
        load_hourly_base_ev_load("Expressway Service District A", "2020-01-01", root)
    except FileNotFoundError:
        return
    raise AssertionError("missing calibration output should raise FileNotFoundError")
