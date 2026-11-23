"""Centralized transport-power scheduling; internal inputs, not a shared schema.

All powers are kW, energy kWh, prices currency/kWh. No voltage or AC/DC
load-flow feasibility is implied. Public contract adaptation lives separately.
"""
from dataclasses import dataclass, replace
from itertools import combinations
from time import perf_counter
from collections.abc import Mapping

import numpy as np
import pyomo.environ as pyo

from src.game.solvers import get_solver_name


@dataclass(frozen=True)
class SiteInput:
    site_id: str
    pv_kw: tuple
    wind_kw: tuple
    base_kw: tuple
    ev_kw: tuple
    buy_price: tuple
    sell_price: tuple
    ev_price: tuple
    import_kw: float
    export_kw: float
    capacity_kwh: float
    battery_kw: float
    soc_initial: float = 0.5
    soc_min: float = 0.1
    soc_max: float = 0.9
    soc_terminal: float = 0.5
    eta_charge: float = 0.95
    eta_discharge: float = 0.95
    degradation_per_kwh: float = 0.01
    base_min_fraction: float = 1.0
    ev_min_fraction: float = 0.0
    base_unserved_cost: float = 100.0
    ev_unserved_cost: float = 10.0
    ev_power_kw: float = float("inf")


@dataclass(frozen=True)
class TieInput:
    line_id: str
    source: str
    target: str
    forward_kw: float
    reverse_kw: float
    efficiency: float = 1.0


@dataclass(frozen=True)
class NetworkInput:
    sites: tuple
    ties: tuple
    upstream_import_kw: tuple
    upstream_export_kw: tuple
    dt_hours: float = 0.5
    service_gap: float = 1.0
    provenance: object = "synthetic"


def validate(case):
    n = len(case.upstream_import_kw)
    ids = [s.site_id for s in case.sites]
    if not n or not ids or len(set(ids)) != len(ids):
        raise ValueError("nonempty horizon and unique site IDs required")
    if isinstance(case.provenance, str):
        if case.provenance not in ("synthetic", "measured", "anonymized"):
            raise ValueError("unsupported provenance label")
    elif (not isinstance(case.provenance, Mapping)
          or set(case.provenance) != {"data_class", "source"}
          or case.provenance.get("data_class") not in ("synthetic", "measured", "anonymized")
          or not isinstance(case.provenance.get("source"), str)
          or not case.provenance["source"].strip()):
        raise ValueError("provenance must be an explicit label or frozen data_class/source object")
    def finite(values, label, nonnegative=True):
        a = np.asarray(values, dtype=float)
        if not np.isfinite(a).all() or (nonnegative and (a < 0).any()):
            raise ValueError(f"invalid {label}")
    finite([case.dt_hours, case.service_gap], "time/fairness")
    if case.dt_hours <= 0 or case.service_gap > 1:
        raise ValueError("invalid time/fairness")
    for field in ("upstream_import_kw", "upstream_export_kw"):
        a = getattr(case, field)
        if len(a) != n:
            raise ValueError("unaligned upstream horizon")
        finite(a, field)
    for s in case.sites:
        for field in ("pv_kw", "wind_kw", "base_kw", "ev_kw", "buy_price", "sell_price", "ev_price"):
            a = getattr(s, field)
            if len(a) != n:
                raise ValueError(f"unaligned {s.site_id}.{field}")
            finite(a, field, field not in ("buy_price", "sell_price", "ev_price"))
        finite([s.import_kw, s.export_kw, s.capacity_kwh, s.battery_kw,
                s.degradation_per_kwh, s.base_unserved_cost, s.ev_unserved_cost], "ratings/cost")
        if np.isnan(s.ev_power_kw) or s.ev_power_kw < 0:
            raise ValueError("invalid EV device rating")
        finite([s.soc_min, s.soc_initial, s.soc_terminal, s.soc_max,
                s.eta_charge, s.eta_discharge, s.base_min_fraction, s.ev_min_fraction], "fractions")
        if not (s.capacity_kwh > 0 and 0 <= s.soc_min <= s.soc_initial <= s.soc_max <= 1
                and s.soc_min <= s.soc_terminal <= s.soc_max
                and 0 < s.eta_charge <= 1 and 0 < s.eta_discharge <= 1
                and 0 <= s.base_min_fraction <= 1 and 0 <= s.ev_min_fraction <= 1):
            raise ValueError(f"invalid storage/service parameters: {s.site_id}")
    if len({x.line_id for x in case.ties}) != len(case.ties):
        raise ValueError("duplicate tie IDs")
    for x in case.ties:
        finite([x.forward_kw, x.reverse_kw, x.efficiency], "tie ratings")
        if x.source not in ids or x.target not in ids or x.source == x.target or not 0 < x.efficiency <= 1:
            raise ValueError("invalid tie endpoints/efficiency")


def build_model(case):
    validate(case)
    sites = {s.site_id: s for s in case.sites}
    ties = {x.line_id: x for x in case.ties}
    n = len(case.upstream_import_kw)
    m = pyo.ConcreteModel("centralized_multisite")
    m.S = pyo.Set(initialize=list(sites))
    m.T = pyo.RangeSet(0, n - 1)
    m.K = pyo.RangeSet(0, n)
    m.L = pyo.Set(initialize=list(ties))
    m.base = pyo.Var(m.S, m.T, bounds=lambda m,s,t: (sites[s].base_min_fraction*sites[s].base_kw[t], sites[s].base_kw[t]))
    m.ev = pyo.Var(m.S, m.T, bounds=lambda m,s,t: (0, min(sites[s].ev_kw[t], sites[s].ev_power_kw)))
    m.curtail = pyo.Var(m.S, m.T, bounds=lambda m,s,t: (0, sites[s].pv_kw[t]+sites[s].wind_kw[t]))
    m.buy = pyo.Var(m.S, m.T, bounds=lambda m,s,t: (0, sites[s].import_kw))
    m.sell = pyo.Var(m.S, m.T, bounds=lambda m,s,t: (0, sites[s].export_kw))
    m.charge = pyo.Var(m.S, m.T, bounds=lambda m,s,t: (0, sites[s].battery_kw))
    m.discharge = pyo.Var(m.S, m.T, bounds=lambda m,s,t: (0, sites[s].battery_kw))
    m.soc = pyo.Var(m.S, m.K, bounds=lambda m,s,k: (sites[s].soc_min, sites[s].soc_max))
    m.battery_mode = pyo.Var(m.S, m.T, domain=pyo.Binary)
    m.grid_mode = pyo.Var(m.S, m.T, domain=pyo.Binary)
    m.priority = pyo.Var(m.S, m.T, domain=pyo.Binary)
    m.forward = pyo.Var(m.L, m.T, bounds=lambda m,l,t: (0, ties[l].forward_kw))
    m.reverse = pyo.Var(m.L, m.T, bounds=lambda m,l,t: (0, ties[l].reverse_kw))
    m.line_mode = pyo.Var(m.L, m.T, domain=pyo.Binary)
    m.rules = pyo.ConstraintList()
    for s, a in sites.items():
        m.rules.add(m.soc[s,0] == a.soc_initial)
        m.rules.add(m.soc[s,n] >= a.soc_terminal)
        m.rules.add(sum(m.ev[s,t] for t in m.T) >= a.ev_min_fraction*sum(a.ev_kw))
        for t in m.T:
            m.rules.add(m.charge[s,t] <= a.battery_kw*m.battery_mode[s,t])
            m.rules.add(m.discharge[s,t] <= a.battery_kw*(1-m.battery_mode[s,t]))
            m.rules.add(m.buy[s,t] <= a.import_kw*m.grid_mode[s,t])
            m.rules.add(m.sell[s,t] <= a.export_kw*(1-m.grid_mode[s,t]))
            # EV can be served only once all base load in that period is served.
            m.rules.add(m.ev[s,t] <= a.ev_kw[t]*m.priority[s,t])
            m.rules.add(m.base[s,t] >= a.base_kw[t]*m.priority[s,t])
            m.rules.add(m.soc[s,t+1] == m.soc[s,t] + case.dt_hours/a.capacity_kwh*(a.eta_charge*m.charge[s,t]-m.discharge[s,t]/a.eta_discharge))
    def balance(m,s,t):
        a = sites[s]
        exchange = sum((x.efficiency*m.reverse[l,t]-m.forward[l,t] if x.source == s
                        else x.efficiency*m.forward[l,t]-m.reverse[l,t])
                       for l,x in ties.items() if s in (x.source,x.target))
        return (a.pv_kw[t]+a.wind_kw[t]-m.curtail[s,t]+m.buy[s,t]-m.sell[s,t]
                +m.discharge[s,t]-m.charge[s,t]+exchange == m.base[s,t]+m.ev[s,t])
    m.balance = pyo.Constraint(m.S,m.T,rule=balance)
    for t in m.T:
        # Gross limits: cross-site imports and exports cannot cancel congestion.
        m.rules.add(sum(m.buy[s,t] for s in m.S) <= case.upstream_import_kw[t])
        m.rules.add(sum(m.sell[s,t] for s in m.S) <= case.upstream_export_kw[t])
        for l,x in ties.items():
            m.rules.add(m.forward[l,t] <= x.forward_kw*m.line_mode[l,t])
            m.rules.add(m.reverse[l,t] <= x.reverse_kw*(1-m.line_mode[l,t]))
    eligible = [s for s in sites if sum(sites[s].ev_kw) > 0]
    for a,b in combinations(eligible,2):
        gap = sum(m.ev[a,t] for t in m.T)/sum(sites[a].ev_kw)-sum(m.ev[b,t] for t in m.T)/sum(sites[b].ev_kw)
        m.rules.add(gap <= case.service_gap)
        m.rules.add(gap >= -case.service_gap)
    m.cost = pyo.Objective(expr=case.dt_hours*sum(
        a.buy_price[t]*m.buy[s,t]-a.sell_price[t]*m.sell[s,t]-a.ev_price[t]*m.ev[s,t]
        +a.degradation_per_kwh*(m.charge[s,t]+m.discharge[s,t])
        +a.base_unserved_cost*(a.base_kw[t]-m.base[s,t])
        +a.ev_unserved_cost*(a.ev_kw[t]-m.ev[s,t])
        for s,a in sites.items() for t in m.T))
    return m


def classify_termination(term):
    name = str(term).lower()
    if name == "optimal":
        return "optimal"
    if name in ("infeasible", "locallyinfeasible"):
        return "infeasible"
    if name in ("maxtimelimit", "maxiterations", "maxevaluations", "resourceinterrupt"):
        return "timeout"
    return "solver_error"


def solve_centralized(case, time_limit=60, prefer="appsi_highs"):
    if not np.isfinite(time_limit) or time_limit < 0:
        raise ValueError("time_limit must be finite and nonnegative")
    start = perf_counter()
    m = build_model(case)
    built = perf_counter()
    report = dict(method="centralized", provenance=case.provenance,
                  electrical_feasibility_verified=False,
                  variables=m.nvariables(), constraints=m.nconstraints(),
                  binary_variables=sum(v.is_binary() for v in m.component_data_objects(pyo.Var)),
                  build_seconds=built-start, solver=None, status="solver_error", schedule=[], ties=[])
    try:
        name = get_solver_name(prefer)
        report["solver"] = name
        opt = pyo.SolverFactory(name)
        if name != "appsi_highs":
            opt.options[{"gurobi":"TimeLimit", "cbc":"seconds", "glpk":"tmlim"}.get(name,"time_limit")] = time_limit
        # Legacy appsi solve() overwrites config.time_limit from timelimit.
        result = opt.solve(m, load_solutions=False, **({"timelimit":time_limit} if name == "appsi_highs" else {}))
        report["optimization_seconds"] = perf_counter()-built
        report["termination"] = str(result.solver.termination_condition)
        report["status"] = classify_termination(result.solver.termination_condition)
        if report["status"] == "optimal":
            m.solutions.load_from(result)
            report["objective"] = pyo.value(m.cost)
            for s in m.S:
                for t in m.T:
                    row = {field:pyo.value(getattr(m,field)[s,t]) for field in
                           ("base","ev","curtail","buy","sell","charge","discharge")}
                    row.update(site_id=s, step=t, soc_start=pyo.value(m.soc[s,t]), soc_end=pyo.value(m.soc[s,t+1]))
                    report["schedule"].append(row)
            report["ties"] = [dict(line_id=l,step=t,forward=pyo.value(m.forward[l,t]),reverse=pyo.value(m.reverse[l,t])) for l in m.L for t in m.T]
            report["checks"] = check_schedule(case,report)
            report["metrics"] = summarize(case,report)
            report["validation_status"] = "passed" if report["checks"]["passed"] else "constraint_violation"
        elif report["status"] == "infeasible":
            report["reason"] = "No feasible schedule under base/EV minima, SOC terminal/reserve, grid, tie and fairness constraints; no IIS available from this adapter."
        else:
            report["reason"] = "No proven optimal schedule published; inspect termination."
    except Exception as exc:
        report.update(status="solver_error", reason=f"{type(exc).__name__}: {exc}")
    report["solve_seconds"] = perf_counter()-built
    report["runtime_seconds"] = perf_counter()-start
    report["postprocess_seconds"] = max(0.,report["solve_seconds"]-report.get("optimization_seconds",report["solve_seconds"]))
    report["bottleneck"] = max(("build","optimization","postprocess"),key=lambda phase:report.get(phase+"_seconds",0.))
    return report


def solve_independent(case, **kwargs):
    """Separate site optimizations; shared limits/fairness audited, not enforced."""
    validate(case)
    start = perf_counter()
    runs = []
    for s in case.sites:
        local = replace(case, sites=(s,), ties=(), service_gap=1,
                        upstream_import_kw=(s.import_kw,)*len(case.upstream_import_kw),
                        upstream_export_kw=(s.export_kw,)*len(case.upstream_export_kw))
        run = solve_centralized(local,**kwargs)
        run.update(method="independent_site",site_id=s.site_id)
        runs.append(run)
    statuses = [r["status"] for r in runs]
    status = next((x for x in ("solver_error","infeasible","timeout") if x in statuses),"optimal")
    report = dict(method="independent", provenance=case.provenance, status=status,
                  electrical_feasibility_verified=False, site_runs=runs,
                  site_statuses={s.site_id:r["status"] for s,r in zip(case.sites,runs)},
                  runtime_seconds=perf_counter()-start, schedule=[], ties=[])
    report.update(solver=sorted({r["solver"] for r in runs if r["solver"]}),
                  variables=sum(r["variables"] for r in runs),constraints=sum(r["constraints"] for r in runs),
                  binary_variables=sum(r["binary_variables"] for r in runs),
                  optimization_seconds=sum(r.get("optimization_seconds",0.) for r in runs))
    if status == "optimal":
        report["schedule"] = [row for r in runs for row in r["schedule"]]
        report["ties"] = [dict(line_id=x.line_id,step=t,forward=0.,reverse=0.) for x in case.ties for t in range(len(case.upstream_import_kw))]
        report["checks"] = check_schedule(case,report)
        report["metrics"] = summarize(case,report)
        report["objective"] = sum(r["objective"] for r in runs)
    return report


def check_schedule(case, report, tolerance=1e-6):
    """Independent numerical audit of each period, device and coupling."""
    rows = {(r["site_id"],r["step"]):r for r in report["schedule"]}
    flows = {(r["line_id"],r["step"]):r for r in report["ties"]}
    records = []
    def check(category, key, violation):
        violation = float(violation)
        records.append(dict(category=category,key=str(key),violation=max(0.,violation) if np.isfinite(violation) else float("inf")))
    fractions = {}
    for s in case.sites:
        served = 0.
        previous = s.soc_initial
        for t in range(len(case.upstream_import_kw)):
            r = rows[s.site_id,t]
            net = 0.
            for x in case.ties:
                f = flows[x.line_id,t]
                if s.site_id == x.source:
                    net += x.efficiency*f["reverse"]-f["forward"]
                elif s.site_id == x.target:
                    net += x.efficiency*f["forward"]-f["reverse"]
            residual = s.pv_kw[t]+s.wind_kw[t]-r["curtail"]+r["buy"]-r["sell"]+r["discharge"]-r["charge"]+net-r["base"]-r["ev"]
            check("site_balance_kwh",(s.site_id,t),abs(residual)*case.dt_hours)
            expected = previous+case.dt_hours/s.capacity_kwh*(s.eta_charge*r["charge"]-r["discharge"]/s.eta_discharge)
            check("soc_transition_kwh",(s.site_id,t),abs(expected-r["soc_end"])*s.capacity_kwh)
            check("soc_continuity",(s.site_id,t),abs(previous-r["soc_start"]))
            previous = r["soc_end"]
            check("soc_bounds",(s.site_id,t),max(s.soc_min-previous,previous-s.soc_max))
            for field,lo,hi in (("base",s.base_min_fraction*s.base_kw[t],s.base_kw[t]),("ev",0,min(s.ev_kw[t],s.ev_power_kw)),("curtail",0,s.pv_kw[t]+s.wind_kw[t]),("buy",0,s.import_kw),("sell",0,s.export_kw),("charge",0,s.battery_kw),("discharge",0,s.battery_kw)):
                check("power_bounds",(s.site_id,t,field),max(lo-r[field],r[field]-hi))
            check("battery_exclusivity",(s.site_id,t),min(r["charge"],r["discharge"]))
            check("grid_exclusivity",(s.site_id,t),min(r["buy"],r["sell"]))
            check("base_priority",(s.site_id,t),s.base_kw[t]-r["base"] if r["ev"]>tolerance else 0.)
            served += r["ev"]
        check("terminal_soc",s.site_id,s.soc_terminal-previous)
        check("ev_minimum",s.site_id,s.ev_min_fraction*sum(s.ev_kw)-served)
        if sum(s.ev_kw)>0:
            fractions[s.site_id] = served/sum(s.ev_kw)
    for a,b in combinations(fractions,2):
        check("fairness",(a,b),abs(fractions[a]-fractions[b])-case.service_gap)
    for t in range(len(case.upstream_import_kw)):
        loss = 0.
        for x in case.ties:
            f = flows[x.line_id,t]
            check("tie_bounds",(x.line_id,t),max(-f["forward"],-f["reverse"],f["forward"]-x.forward_kw,f["reverse"]-x.reverse_kw))
            check("tie_exclusivity",(x.line_id,t),min(f["forward"],f["reverse"]))
            loss += (1-x.efficiency)*(f["forward"]+f["reverse"])
        check("upstream_import",t,sum(rows[s.site_id,t]["buy"] for s in case.sites)-case.upstream_import_kw[t])
        check("upstream_export",t,sum(rows[s.site_id,t]["sell"] for s in case.sites)-case.upstream_export_kw[t])
        residual = sum(s.pv_kw[t]+s.wind_kw[t]-rows[s.site_id,t]["curtail"]+rows[s.site_id,t]["buy"]-rows[s.site_id,t]["sell"]+rows[s.site_id,t]["discharge"]-rows[s.site_id,t]["charge"]-rows[s.site_id,t]["base"]-rows[s.site_id,t]["ev"] for s in case.sites)-loss
        check("system_balance_kwh",t,abs(residual)*case.dt_hours)
    maxima = {category:max(r["violation"] for r in records if r["category"]==category) for category in {r["category"] for r in records}}
    return dict(passed=all(v<=tolerance for v in maxima.values()), tolerance=tolerance,
                max_violation=max(maxima.values(),default=0.), by_category=maxima,
                violations=[r for r in records if r["violation"]>tolerance], records=records)


def summarize(case, report):
    dt = case.dt_hours
    rows = report["schedule"]
    sites = {s.site_id:s for s in case.sites}
    total = lambda field: dt*sum(r[field] for r in rows)
    service = {s.site_id:dt*sum(r["ev"] for r in rows if r["site_id"]==s.site_id) for s in case.sites}
    fractions = [service[s.site_id]/(dt*sum(s.ev_kw)) for s in case.sites if sum(s.ev_kw)>0]
    grid_cost = dt*sum(sites[r["site_id"]].buy_price[r["step"]]*r["buy"]-sites[r["site_id"]].sell_price[r["step"]]*r["sell"] for r in rows)
    revenue = dt*sum(sites[r["site_id"]].ev_price[r["step"]]*r["ev"] for r in rows)
    degradation = dt*sum(sites[r["site_id"]].degradation_per_kwh*(r["charge"]+r["discharge"]) for r in rows)
    storage_loss = dt*sum((1-sites[r["site_id"]].eta_charge)*r["charge"]+(1/sites[r["site_id"]].eta_discharge-1)*r["discharge"] for r in rows)
    ties = {x.line_id:x for x in case.ties}
    per_site = {}
    for s in case.sites:
        local = [r for r in rows if r["site_id"]==s.site_id]
        cost = dt*sum(s.buy_price[r["step"]]*r["buy"]-s.sell_price[r["step"]]*r["sell"] for r in local)
        income = dt*sum(s.ev_price[r["step"]]*r["ev"] for r in local)
        wear = dt*sum(s.degradation_per_kwh*(r["charge"]+r["discharge"]) for r in local)
        per_site[s.site_id] = dict(ev_served_kwh=service[s.site_id],grid_cost=cost,delivered_revenue=income,
                                   degradation_cost=wear,operating_value=income-cost-wear)
    return dict(base_served_kwh=total("base"),ev_served_kwh=total("ev"),
                ev_requested_kwh=dt*sum(sum(s.ev_kw) for s in case.sites),
                base_unserved_kwh=dt*sum(sum(s.base_kw) for s in case.sites)-total("base"),
                ev_unserved_kwh=dt*sum(sum(s.ev_kw) for s in case.sites)-total("ev"),
                curtailment_kwh=total("curtail"), storage_loss_kwh=storage_loss,
                network_loss_kwh=dt*sum((1-ties[r["line_id"]].efficiency)*(r["forward"]+r["reverse"]) for r in report["ties"]),
                grid_cost=grid_cost, delivered_revenue=revenue,user_cost=revenue,
                degradation_cost=degradation,operating_value=revenue-grid_cost-degradation,
                ev_service_by_site_kwh=service,per_site=per_site,
                ev_service_fraction_gap=max(fractions,default=0)-min(fractions,default=0))


def synthetic_case(count=2, periods=4):
    if count not in (2,4) or periods<1:
        raise ValueError("synthetic examples support 2/4 sites and positive periods")
    sites = tuple(SiteInput(f"site{i+1}", (12. if i%2==0 else 0.,)*periods,
                            (0.,)*periods,(2.,)*periods,(8.,)*periods,
                            (1.,)*periods,(0.,)*periods,(2.,)*periods,
                            10.,0.,10.,2.,ev_power_kw=8.) for i in range(count))
    ties = tuple(TieInput(f"tie{i+1}",sites[i].site_id,sites[i+1].site_id,6.,6.,0.98) for i in range(count-1))
    return NetworkInput(sites,ties,(4.*count,)*periods,(0.,)*periods,service_gap=0.5)
