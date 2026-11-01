"""论文阶段测试入口。

根目录 tests/ 保存唯一测试实现；本目录提供论文复现实验入口，避免
复制测试逻辑后发生版本漂移。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = ROOT / "local" / "tests" / "tests"
PAPER_TEST_ROOT = ROOT / "local" / "论文" / "tests"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))
if str(PAPER_TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(PAPER_TEST_ROOT))

from test_calibration_loader import test_calibration_loader_requires_processed_output
from test_dynamic_dc_soc import test_dynamic_dc_controller_updates_soc_and_returns_voltage
from test_rolling_sfse import (
    test_forecast_bundle_validates_horizon,
    test_rolling_controller_returns_safe_current_action,
)
from test_site_a_inputs import SiteAInputTests
from test_long_horizon_ledger import LongHorizonLedgerTests
from test_soh_game_integration import SOHGameIntegrationTests
import test_dc_physics


def main():
    tests = [
        test_calibration_loader_requires_processed_output,
        test_dynamic_dc_controller_updates_soc_and_returns_voltage,
        test_forecast_bundle_validates_horizon,
        test_rolling_controller_returns_safe_current_action,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    SiteAInputTests("test_peak_normalization_respects_paper_ratings").run()
    print("PASS test_peak_normalization_respects_paper_ratings")
    SiteAInputTests("test_site_a_request_load_keeps_design_peak_as_execution_limit").run()
    print("PASS test_site_a_request_load_keeps_design_peak_as_execution_limit")
    LongHorizonLedgerTests(
        "test_exact_aging_cost_is_charged_once_in_reported_long_term_profit"
    ).run()
    print("PASS test_exact_aging_cost_is_charged_once_in_reported_long_term_profit")
    SOHGameIntegrationTests(
        "test_soh_changes_game_capacity_and_marginal_degradation_cost"
    ).run()
    print("PASS test_soh_changes_game_capacity_and_marginal_degradation_cost")
    physics_tests = [n for n in dir(test_dc_physics) if n.startswith("test_")]
    for name in sorted(physics_tests):
        getattr(test_dc_physics, name)()
        print(f"PASS {name}")
    print(f"{len(tests) + 4 + len(physics_tests)} paper tests passed")


if __name__ == "__main__":
    main()
