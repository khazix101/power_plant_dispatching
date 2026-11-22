"""
最佳反应迭代 (Best-Response Iteration, BRI) 求解器。

由于领导者必须"预判"跟随者响应, 直接的朴素迭代并不收敛。本模块采用
**电价坐标上升 (coordinate ascent)** 的实用型最佳反应迭代:

    初始化电价 p^0
    repeat:
        for 每个时段 t:
            对每个电价档位 k, 用跟随者预言机计算响应 e(p),
            领导者在该响应下优化储能/可靠性, 得到净利润;
            取使净利润最大的档位更新 p_t
        若一轮无改进则停止

每次评估都调用精确的跟随者 LP (最优反应), 因此得到的解一定是
博弈的一个均衡点 (双方均无单边改进动机), 但可能只是局部最优。
其价值在于: 单轮只需 T×K 次小规模求解, 规模扩展性优于单体 MILP,
可作为 Stage-3 学习型加速的对照与热启动。
"""

import math
import logging

from src.game.baseline import solve_leader_fixed_profile
from src.game.case import GameCase
from src.game.follower import solve_follower_response
from src.game.dc_microgrid import DCMicrogridPowerFlow

logger = logging.getLogger(__name__)


def _network_candidate_metrics(case, sched, ev_profile, network_config):
    """Evaluate one planned price response on the DC snapshot model.

    This is deliberately an outer-loop check: the legacy leader model remains
    unchanged, while BRI can reject or penalize candidates using the same
    network snapshot used by the execution layer. Storage_Bus is the reference
    bus, so its scheduled injection is accounted for in the power-balance
    residual; it does not alter the reduced nodal voltage solution.
    """
    network = DCMicrogridPowerFlow(network_config)
    snapshots = []
    power_balance_residuals = []
    scheduled_bess = []
    used_pv = []
    used_wind = []
    served_load = []
    for t, dt in enumerate(case.interval_hours):
        pv_available = max(0.0, float(case.pv[t]))
        wind_available = max(0.0, float(case.wind[t]))
        curtail_kw = max(0.0, float(sched["curt"][t]))
        pv_used = max(0.0, pv_available - curtail_kw)
        curtail_kw = max(0.0, curtail_kw - pv_available)
        wind_used = max(0.0, wind_available - curtail_kw)
        load_kw = max(
            0.0,
            float(case.base_load[t])
            + float(ev_profile[t]) / max(float(dt), 1e-9)
            - max(0.0, float(sched["shed"][t])),
        )
        p_bess_kw = float(sched["p_dis"][t]) - float(sched["p_ch"][t])
        flow = network.solve({
            "Storage_Bus": p_bess_kw,
            "PV_Bus": pv_used,
            "Wind_Bus": wind_used,
            "Load_Bus": -load_kw,
        })
        snapshots.append(flow)
        used_pv.append(pv_used)
        used_wind.append(wind_used)
        served_load.append(load_kw)
        required_bess_kw = load_kw + flow["losses_kw"] - pv_used - wind_used
        power_balance_residuals.append(required_bess_kw - p_bess_kw)
        scheduled_bess.append(p_bess_kw)

    thermal_verified = all(x["thermal_limits_verified"] for x in snapshots)
    max_loading = (
        max((x["max_loading_pu"] for x in snapshots), default=0.0)
        if thermal_verified else None
    )
    voltage_violation = sum(
        max(0.0, -float(x["voltage_margin_low_pu"]))
        + max(0.0, -float(x["voltage_margin_high_pu"]))
        for x in snapshots
    )
    thermal_violation = (
        sum(max(0.0, float(x["max_loading_pu"]) - 1.0)
            for x in snapshots)
        if thermal_verified else None
    )
    return {
        "snapshots": snapshots,
        "voltage_feasible": all(x["voltage_feasible"] for x in snapshots),
        "thermal_limits_verified": thermal_verified,
        "thermal_feasible": (
            all(x["thermal_feasible"] for x in snapshots)
            if thermal_verified else None
        ),
        "voltage_violation_pu": float(voltage_violation),
        "thermal_violation_pu": (
            float(thermal_violation) if thermal_violation is not None else None
        ),
        "max_loading_pu": (float(max_loading) if max_loading is not None else None),
        "scheduled_bess_kw": scheduled_bess,
        "pv_used_kw": used_pv,
        "wind_used_kw": used_wind,
        "load_after_shed_kw": served_load,
        "reference_bus_injection_is_slack": (
            network.config.reference_bus == "Storage_Bus"
        ),
        "power_balance_residual_kw": power_balance_residuals,
        "max_abs_power_balance_residual_kw": float(
            max((abs(x) for x in power_balance_residuals), default=0.0)
        ),
        "losses_kwh": float(sum(x["losses_kw"] * dt
                                 for x, dt in zip(snapshots, case.interval_hours))),
    }


def evaluate_price_vector(case: GameCase, prices, prefer=None,
                          network_config=None, network_filter=False,
                          network_penalty=0.0, network_loss_penalty=0.0,
                          network_hard_voltage=True,
                          network_hard_thermal=False):
    """给定电价, 返回 (净利润, 跟随者响应, 领导者调度)。

    领导者在该电价诱导的 EV 负荷下做最优储能/可靠性调度。
    """
    response = solve_follower_response(case, prices, prefer=prefer)
    ev_profile = [sum(response[u.name][t] for u in case.users) for t in range(case.T)]
    revenue = sum(prices[t] * ev_profile[t] for t in range(case.T))
    sched = solve_leader_fixed_profile(case, ev_profile, revenue,
                                       prefer=prefer, prices=prices)
    network_metrics = None
    if network_filter:
        if network_penalty < 0 or network_loss_penalty < 0:
            raise ValueError("network penalties must be non-negative")
        network_metrics = _network_candidate_metrics(
            case, sched, ev_profile, network_config
        )
        if network_hard_thermal and not network_metrics["thermal_limits_verified"]:
            raise ValueError(
                "network_hard_thermal requires a finite positive current_limit_a "
                "for every branch"
            )
        hard_violation = (
            (network_hard_voltage and not network_metrics["voltage_feasible"])
            or (
                network_hard_thermal
                and not network_metrics["thermal_feasible"]
            )
        )
        network_violation = (
            network_metrics["voltage_violation_pu"]
            + (network_metrics["thermal_violation_pu"] or 0.0)
        )
        network_penalty_cost = (
            network_penalty * network_violation
            + network_loss_penalty * network_metrics["losses_kwh"]
        )
        sched["network_metrics"] = network_metrics
        sched["network_penalty"] = float(network_penalty_cost)
        sched["network_feasible"] = not hard_violation
        sched["network_violation"] = float(network_violation)
    else:
        sched["network_metrics"] = None
        sched["network_penalty"] = 0.0
        sched["network_feasible"] = None
        sched["network_violation"] = 0.0
    congestion_penalty = 0.0
    if case.ev_congestion_penalty > 0 and case.ev_service_limit_kw:
        limit = float(case.ev_service_limit_kw)
        congestion_penalty = case.ev_congestion_penalty * sum(
            (max(0.0, ev_profile[t] / max(case.interval_hours[t], 1e-9)) / limit) ** 2
            * case.interval_hours[t]
            for t in range(case.T)
        )
    ramp_penalty = case.price_ramp_penalty * sum(
        (float(prices[t]) - float(prices[t - 1])) ** 2
        for t in range(1, case.T)
    )
    sched["profit_before_price_ramp"] = sched["profit"]
    sched["price_ramp_penalty"] = ramp_penalty
    sched["ev_congestion_penalty"] = congestion_penalty
    sched["profit"] -= (ramp_penalty + congestion_penalty
                         + sched["network_penalty"])
    if network_filter and not sched["network_feasible"]:
        sched["profit"] = -math.inf
    return sched["profit"], response, sched


def solve_equilibrium_bri(case: GameCase, init_prices=None,
                          max_sweeps=30, tol=1e-6, prefer=None,
                          verbose=False, network_config=None,
                          network_filter=False, network_penalty=0.0,
                          network_loss_penalty=0.0,
                          network_hard_voltage=True,
                          network_hard_thermal=False):
    """坐标上升式最佳反应迭代求均衡。

    Args:
        case: 博弈算例。
        init_prices: 初始电价向量, 默认全为最低档。
        max_sweeps: 最大扫描轮数。
        tol: 改进判定阈值。
        prefer: 求解器。
        verbose: 是否打印每轮收益。

    Returns:
        dict: 与 solve_equilibrium 相同结构, 附加 sweeps 字段。
    """
    T = case.T
    levels = case.price_levels
    if init_prices:
        # The initial point must be a legal discrete price vector.  An earlier
        # default of 1.2 remained outside the revised [1.5, 2.5] grid when
        # max_sweeps=0, so the run was not actually a six-level experiment.
        p = [min(levels, key=lambda level: abs(level - float(value)))
             for value in init_prices]
    else:
        p = [min(levels)] * T

    evaluate_kwargs = dict(
        prefer=prefer, network_config=network_config,
        network_filter=network_filter, network_penalty=network_penalty,
        network_loss_penalty=network_loss_penalty,
        network_hard_voltage=network_hard_voltage,
        network_hard_thermal=network_hard_thermal,
    )
    profit, response, sched = evaluate_price_vector(case, p, **evaluate_kwargs)
    history = [profit]

    for sweep in range(max_sweeps):
        improved = False
        for t in range(T):
            best_k = p[t]
            best_profit = profit
            best_pack = (response, sched)
            for lv in levels:
                if abs(lv - p[t]) < 1e-12:
                    continue
                cand = list(p)
                cand[t] = lv
                cp, cresp, csched = evaluate_price_vector(
                    case, cand, **evaluate_kwargs
                )
                if cp > best_profit + tol:
                    best_profit = cp
                    best_k = lv
                    best_pack = (cresp, csched)
            if best_k != p[t]:
                p[t] = best_k
                profit = best_profit
                response, sched = best_pack
                improved = True
        history.append(profit)
        if verbose:
            logger.info("BRI sweep %d: profit=%.4f, prices=%s", sweep, profit, p)
        if not improved:
            break

    result = dict(sched)
    result.update({
        "profit": profit,
        "prices": p,
        "response": response,
        "sweeps": len(history) - 1,
        "history": history,
        "method": "BRI",
    })
    logger.info("BRI 收敛: profit=%.2f, sweeps=%d", profit, result["sweeps"])
    return result


def solve_bri_multistart(case: GameCase, n_restarts=8, seed=0,
                         prefer=None, verbose=False):
    """多起点 BRI: 从多个初始电价出发, 取收益最高者, 缓解局部最优。"""
    import random
    rng = random.Random(seed)
    levels = case.price_levels
    best = None
    total_sweeps = 0
    for r in range(n_restarts):
        if r == 0:
            init = [min(levels)] * case.T
        elif r == 1:
            init = [max(levels)] * case.T
        else:
            init = [rng.choice(levels) for _ in range(case.T)]
        res = solve_equilibrium_bri(case, init_prices=init, prefer=prefer,
                                    verbose=verbose)
        total_sweeps += res["sweeps"]
        if best is None or res["profit"] > best["profit"]:
            best = res
    best["method"] = "BRI-multistart"
    best["n_restarts"] = n_restarts
    best["total_sweeps"] = total_sweeps
    logger.info("BRI-multistart 最优: profit=%.2f", best["profit"])
    return best

