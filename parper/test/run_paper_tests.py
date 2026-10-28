"""论文阶段测试入口。

根目录 tests/ 保存唯一测试实现；本目录提供论文复现实验入口，避免
复制测试逻辑后发生版本漂移。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_calibration_loader import test_calibration_loader_requires_processed_output
from tests.test_dynamic_dc_soc import test_dynamic_dc_controller_updates_soc_and_returns_voltage
from tests.test_rolling_sfse import (
    test_forecast_bundle_validates_horizon,
    test_rolling_controller_returns_safe_current_action,
)


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
    print(f"{len(tests)} paper tests passed")


if __name__ == "__main__":
    main()
