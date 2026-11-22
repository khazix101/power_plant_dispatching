"""Fresh optimized 1--3 May replay with tightened DC execution tolerance."""
import importlib.util
from pathlib import Path
precise_spec = importlib.util.spec_from_file_location("precision_step", Path(__file__).with_name("continuous_precision_step_audit.py"))
precise_module = importlib.util.module_from_spec(precise_spec)
precise_spec.loader.exec_module(precise_module)

spec = importlib.util.spec_from_file_location("continuous_runner", Path(__file__).with_name("run_continuous_three_day.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.balance_dc_step = precise_module.balance_dc_step_precise
runner.main()
