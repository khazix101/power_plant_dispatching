"""
场景调度策略子包。

对外暴露:
  - ScenarioDispatcher : 场景调度器，评估条件并执行策略流水线
  - DispatchContext     : 单小时调度上下文
  - build_scenarios     : 构建全部场景定义的工厂函数
"""

from .dispatcher import ScenarioDispatcher
from .conditions import DispatchContext
from .scenario_defs import build_scenarios

__all__ = [
    "ScenarioDispatcher",
    "DispatchContext",
    "build_scenarios",
]
