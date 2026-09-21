"""
求解器工厂 —— 优先 Gurobi，回退到开源 HiGHS。

上层代码只依赖 get_solver_name() / solve()，不直接绑定具体求解器，
便于在有 Gurobi 许可证的机器上无缝切换。
"""

import logging

import pyomo.environ as pyo

logger = logging.getLogger(__name__)

# 优先级从高到低
_SOLVER_PREFERENCE = ("gurobi", "appsi_highs", "cbc", "glpk")

_cached_name = None


def get_solver_name(prefer=None):
    """探测可用求解器名称，结果缓存。

    Args:
        prefer: 指定优先使用的求解器名；不可用时回退。

    Returns:
        str: 可用求解器名。

    Raises:
        RuntimeError: 无任何可用 MILP 求解器。
    """
    global _cached_name
    if _cached_name is not None:
        return _cached_name

    candidates = []
    if prefer:
        candidates.append(prefer)
    candidates.extend(_SOLVER_PREFERENCE)

    for name in candidates:
        try:
            opt = pyo.SolverFactory(name)
            if opt is not None and opt.available(exception_flag=False):
                logger.info("使用求解器: %s", name)
                _cached_name = name
                return name
        except Exception as exc:  # noqa: BLE001
            logger.debug("求解器 %s 不可用: %s", name, exc)

    raise RuntimeError(
        "未找到可用的 MILP 求解器。请安装 Gurobi 许可证，"
        "或 pip install highspy / cbc / glpk。"
    )


def solve(model, prefer=None, tee=False, time_limit=None):
    """求解 Pyomo 模型并返回 (termination, solver_name)。

    Args:
        time_limit: 秒; 超过则返回非最优终止条件 (仅部分求解器支持)。
    """
    name = get_solver_name(prefer)
    opt = pyo.SolverFactory(name)
    if time_limit is not None:
        try:
            opt.config.time_limit = time_limit  # appsi 接口
        except Exception:  # noqa: BLE001
            try:
                opt.options["time_limit"] = time_limit
            except Exception:  # noqa: BLE001
                pass
    results = opt.solve(model, tee=tee)
    return results.solver.termination_condition, name
