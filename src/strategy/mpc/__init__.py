"""MPC 调度策略子包。

策略列表:
  - EconomicMPCStrategy (mpc_grid13_sb): 经济最优 MPC，13 档网格搜索 + SimpleBalancing 向前模拟
"""

from .economic_mpc import EconomicMPCStrategy

__all__ = ["EconomicMPCStrategy"]
