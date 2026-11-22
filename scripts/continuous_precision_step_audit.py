"""Isolated numeric audit copy of balance_dc_step; shared source untouched.
Only local iteration counts (30) and residual stop (1e-10 kW) differ.
Acceptance gate stays 1e-6 kWh. Never substitutes the original experiment.
"""
def balance_dc_step_precise(power_flow, soc, pv_kw, wind_kw, ev_request_kw,
                    non_ev_kw, p_schedule_kw, dt_hours=1.0,
                    ev_service_limit_kw=None, ev_must_serve_kw=0.0):
    """执行一个直流时步：有限 BESS + 网络损耗 + EV 削减/弃电。

    功率关系：pv + wind + p_bess = ev_served + non_ev + loss。
    ``ev_must_serve_kw`` 表示已承诺、不可由价格或安全层削减的 EV 负荷；
    若物理供能仍不足，剩余的刚性缺额记入 ``unserved_rigid_kw``，
    不会被当作已服务。
    """
    config = power_flow.config
    if ev_service_limit_kw is None:
        ev_service_limit_kw = config.ev_rated_kw
    if ev_service_limit_kw < 0:
        raise ValueError("ev_service_limit_kw must be non-negative")

    p_bess = soc.limit_power(p_schedule_kw, dt_hours)
    pv_used = max(0.0, float(pv_kw))
    wind_used = max(0.0, float(wind_kw))
    requested = max(0.0, float(ev_request_kw))
    must_serve = max(0.0, min(float(ev_must_serve_kw), requested))
    ev_served = min(requested, float(ev_service_limit_kw))
    ev_shed = requested - ev_served
    pv_curtail = wind_curtail = 0.0
    flow = None
    residual = float("nan")

    for _ in range(30):
        flow = power_flow.solve({
            "PV_Bus": pv_used,
            "Wind_Bus": wind_used,
            "Load_Bus": -(ev_served + non_ev_kw),
        })
        required = ev_served + non_ev_kw + flow["losses_kw"] - pv_used - wind_used
        residual = required - p_bess
        if abs(residual) <= 1e-10:
            break
        if residual > 0:
            # 供能不足：先让 BESS 补足（含网络损耗），再削减柔性 EV；
            # 刚性需求不可削减，仍不足则记入 unserved_rigid。
            new_p = soc.limit_power(p_bess + residual, dt_hours)
            if new_p > p_bess + 1e-9:
                p_bess = new_p
                continue
            shed = min(max(0.0, ev_served - must_serve), residual)
            if shed <= 1e-9:
                break
            ev_served -= shed
            ev_shed += shed
        else:
            # 供能盈余：先限发风光，再减少 BESS 充电。
            surplus = -residual
            cut = min(pv_used, surplus)
            pv_used -= cut
            pv_curtail += cut
            surplus -= cut
            cut = min(wind_used, surplus)
            wind_used -= cut
            wind_curtail += cut
            surplus -= cut
            if surplus > 1e-9:
                new_p = soc.limit_power(p_bess + surplus, dt_hours)
                if new_p <= p_bess + 1e-9:
                    break
                p_bess = new_p

    flow = power_flow.solve({
        "PV_Bus": pv_used,
        "Wind_Bus": wind_used,
        "Load_Bus": -(ev_served + non_ev_kw),
    })
    required = ev_served + non_ev_kw + flow["losses_kw"] - pv_used - wind_used
    residual = required - p_bess
    # The final residual is a physical shortage, not delivered load. Reduce
    # rigid demand and re-solve the DC network because losses depend on the
    # actually served load. Keep EV and non-EV shortages separate.
    non_ev_served = max(0.0, float(non_ev_kw))
    ev_unserved_rigid = 0.0
    non_ev_unserved = 0.0
    if residual > 1e-10:
        for _ in range(30):
            if residual <= 1e-10:
                break
            # Preserve the historical priority: flexible EV was shed first;
            # an unmet committed EV request is accounted before non-EV load.
            ev_cut = min(must_serve - ev_unserved_rigid, residual)
            if ev_cut > 0:
                ev_served -= ev_cut
                ev_unserved_rigid += ev_cut
            remaining = max(0.0, residual - max(0.0, ev_cut))
            non_ev_cut = min(non_ev_served, remaining)
            non_ev_served -= non_ev_cut
            non_ev_unserved += non_ev_cut
            flow = power_flow.solve({
                "PV_Bus": pv_used, "Wind_Bus": wind_used,
                "Load_Bus": -(ev_served + non_ev_served),
            })
            required = ev_served + non_ev_served + flow["losses_kw"] - pv_used - wind_used
            residual = required - p_bess
            if ev_cut <= 1e-9 and non_ev_cut <= 1e-9:
                break
    # Lower served load also lowers resistive losses. Reconcile the small
    # resulting surplus against BESS power or curtailment before metering.
    if residual < -1e-10:
        new_p = soc.limit_power(p_bess + residual, dt_hours)
        p_bess = new_p
        for _ in range(30):
            flow = power_flow.solve({
                "PV_Bus": pv_used, "Wind_Bus": wind_used,
                "Load_Bus": -(ev_served + non_ev_served),
            })
            residual = ev_served + non_ev_served + flow["losses_kw"] - pv_used - wind_used - p_bess
            if abs(residual) <= 1e-10:
                break
            if residual < 0:
                cut = min(pv_used, -residual)
                pv_used -= cut
                pv_curtail += cut
                cut = min(wind_used, max(0.0, -residual - cut))
                wind_used -= cut
                wind_curtail += cut
            else:
                p_bess = soc.limit_power(p_bess + residual, dt_hours)
    unserved_rigid = ev_unserved_rigid + non_ev_unserved
    soc.advance(p_bess, dt_hours)
    return {
        "p_bess_kw": p_bess,
        "soc": soc.soc,
        "ev_requested_kw": requested,
        "ev_must_serve_kw": must_serve,
        "ev_served_kw": ev_served,
        "ev_shed_kw": ev_shed,
        "ev_unserved_rigid_kw": ev_unserved_rigid,
        "non_ev_served_kw": non_ev_served,
        "non_ev_unserved_kw": non_ev_unserved,
        "ev_limit_kw": float(ev_service_limit_kw),
        "pv_used_kw": pv_used,
        "wind_used_kw": wind_used,
        "pv_curtailed_kw": pv_curtail,
        "wind_curtailed_kw": wind_curtail,
        "losses_kw": flow["losses_kw"],
        "residual_kw": residual,
        "unserved_rigid_kw": unserved_rigid,
        "voltage_feasible": flow["voltage_feasible"],
        "external_supply_kw": 0.0,
        "power_flow": flow,
    }
