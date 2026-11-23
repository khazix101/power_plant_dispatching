"""C's coordinator adapter for A-owned src.multisite.contract v1.

No copy or replacement of the shared schema. Install/integrate A's contract
before using this entry point. The optimizer can run standalone synthetic tests.
"""
from dataclasses import asdict
import hashlib
import json
import math
import subprocess
from pathlib import Path

from src.game.multisite import NetworkInput, SiteInput, TieInput, solve_centralized, solve_independent

MODEL_VERSION = "centralized-transport-v1"


def _number(value, name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f"{name}: known finite numeric value required")
    return float(value)


def _required(config,name):
    if name not in config:
        raise ValueError(f"config missing {name}")
    return config[name]


def adapt_request(request):
    from src.multisite.contract import CONTRACT_VERSION, MultiSiteInput
    if request.contract_version != CONTRACT_VERSION:
        raise ValueError("request contract version mismatch")
    # Revalidate direct dataclass construction through the shared wire boundary.
    data = MultiSiteInput.from_dict(request.input.to_dict())
    cfg = request.config
    provenance = _required(cfg,"provenance")
    policy = _required(cfg,"sites")
    levels = tuple(_number(v,"price_levels") for v in _required(cfg,"price_levels"))
    if not levels:
        raise ValueError("price_levels must be nonempty")
    if set(policy) != {s.site_id for s in data.sites}:
        raise ValueError("config site IDs must exactly match input")
    series = {s.site_id:s.intervals for s in data.series}
    inputs = []
    for site in data.sites:
        p = policy[site.site_id]
        intervals = series[site.site_id]
        def signal(name):
            values = []
            for row in intervals:
                sig = row.signals.get(name)
                if sig is None or sig.status in ("missing","stale","invalid"):
                    raise ValueError(f"{site.site_id}.{name}: missing/stale/invalid signal")
                values.append(_number(sig.value,name))
            return tuple(values)
        def static(name):
            return _number(getattr(site,name),f"{site.site_id}.{name}")
        capacity = static("battery_capacity_kwh")
        minimum = static("soc_min")
        soh = signal("soh")
        if any(not static("soh_min") <= x <= 1 for x in soh):
            raise ValueError("SOH outside asset bounds")
        soc = signal("soc")
        if any(not minimum <= x <= static("soc_max") for x in soc):
            raise ValueError("SOC observation outside asset bounds")
        margin = signal("reserve_margin_kwh")
        if any(abs(m-(x-minimum)*capacity)>1e-6 for m,x in zip(margin,soc)):
            raise ValueError("reserve_margin_kwh inconsistent with nominal capacity/SOC minimum")
        if any(not any(abs(v-level)<1e-9 for level in levels) for v in signal("price_yuan_per_kwh")):
            raise ValueError("response price outside configured price_levels")
        pv,wind = signal("pv_forecast_kw"),signal("wind_forecast_kw")
        if max(pv)>static("pv_rated_kw")+1e-6 or max(wind)>static("wind_rated_kw")+1e-6:
            raise ValueError("renewable forecast exceeds device rating")
        reserve = _number(_required(p,"reserve_requirement_kwh"),"reserve_requirement_kwh")
        if reserve < 0:
            raise ValueError("reserve_requirement_kwh must be nonnegative")
        inputs.append(SiteInput(
            site_id=site.site_id,pv_kw=pv,wind_kw=wind,base_kw=signal("load_forecast_kw"),ev_kw=signal("ev_forecast_kw"),
            buy_price=signal("grid_buy_price_yuan_per_kwh"),sell_price=signal("grid_sell_price_yuan_per_kwh"),
            ev_price=signal("ev_delivery_price_yuan_per_kwh"),import_kw=static("transformer_import_limit_kw"),
            export_kw=static("transformer_export_limit_kw"),capacity_kwh=capacity,battery_kw=static("battery_power_kw"),
            soc_initial=soc[0],soc_min=minimum+reserve/capacity,soc_max=static("soc_max"),
            soc_terminal=_number(_required(p,"soc_terminal"),"soc_terminal"),
            eta_charge=static("battery_charge_efficiency"),eta_discharge=static("battery_discharge_efficiency"),
            degradation_per_kwh=_number(_required(p,"degradation_per_kwh"),"degradation_per_kwh"),
            base_min_fraction=_number(_required(p,"base_min_fraction"),"base_min_fraction"),
            ev_min_fraction=static("minimum_ev_service_fraction"),ev_power_kw=static("ev_power_limit_kw"),
            base_unserved_cost=_number(_required(p,"base_unserved_cost"),"base_unserved_cost"),
            ev_unserved_cost=_number(_required(p,"ev_unserved_cost"),"ev_unserved_cost")))
    ties = tuple(TieInput(x.line_id,x.from_site,x.to_site,
                         _number(x.export_limit_kw,"tie export"),_number(x.import_limit_kw,"tie import"),
                         1-_number(x.loss_fraction,"tie loss_fraction")) for x in data.tie_lines)
    n = data.prediction_horizon_steps
    return NetworkInput(tuple(inputs),ties,(_number(data.upstream_import_limit_kw,"upstream import"),)*n,
                        (_number(data.upstream_export_limit_kw,"upstream export"),)*n,
                        dt_hours=data.interval_minutes/60,
                        service_gap=_number(_required(cfg,"service_gap"),"service_gap"),provenance=provenance),data


class CentralizedCoordinator:
    def coordinate(self,request):
        from src.multisite.contract import CONTRACT_VERSION, CoordinationResult
        base = dict(contract_version=CONTRACT_VERSION,run_id=request.run_id)
        try:
            case,data = adapt_request(request)
            method = request.config.get("method","centralized")
            if method not in ("centralized","independent"):
                raise ValueError("method must be centralized or independent")
            solver = solve_centralized if method=="centralized" else solve_independent
            result = solver(case,time_limit=_number(_required(request.config,"time_limit_seconds"),"time_limit_seconds"))
        except (ValueError,TypeError,KeyError,ZeroDivisionError) as exc:
            return CoordinationResult(**base,status="invalid_input",failure_code="invalid_input_or_config",ledger=[],
                                      metadata=dict(model_version=MODEL_VERSION,reason=str(exc)))
        serialized = json.dumps(dict(input=data.to_dict(),config=request.config),sort_keys=True,allow_nan=False)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        try:
            revision = subprocess.check_output(["git","rev-parse","HEAD"],cwd=Path(__file__).resolve().parents[2],text=True).strip()
        except (OSError,subprocess.CalledProcessError):
            revision = "unavailable"
        metadata = {k:v for k,v in result.items() if k not in ("schedule","ties","site_runs")}
        metadata.update(model_version=MODEL_VERSION,input_config_sha256=digest,source_revision=revision,
                        capacity_basis="nominal",soh_dynamics="constant; no SOH degradation forecast",
                        random_seed=request.config.get("random_seed"),safe_to_dispatch=False)
        metadata["shared_network_feasible"] = result.get("checks",{}).get("passed",False)
        intervals = {s.site_id:s.intervals for s in data.series}
        assets = {s.site_id:s for s in case.sites}
        ledger = []
        for row in result["schedule"]:
            s,t = row["site_id"],row["step"]
            a = assets[s]
            interval = intervals[s][t]
            dt = case.dt_hours
            entry = dict(row,record_type="site",**base,timestamp=interval.timestamp,method=method,
                         model_version=MODEL_VERSION,input_config_sha256=digest,status=result["status"],
                         inputs={name:asdict(sig) for name,sig in interval.signals.items()},
                         requested_action=dict(storage_kw=row["discharge"]-row["charge"],requested_ev_kw=row["ev"],
                                               price_yuan_per_kwh=interval.signals["price_yuan_per_kwh"].value),
                         safe_action=None,safe_to_dispatch=False,
                         ev_requested_kwh=a.ev_kw[t]*dt,base_requested_kwh=a.base_kw[t]*dt,
                         ev_served_kwh=row["ev"]*dt,base_served_kwh=row["base"]*dt,
                         ev_unserved_kwh=(a.ev_kw[t]-row["ev"])*dt,base_unserved_kwh=(a.base_kw[t]-row["base"])*dt,
                         curtailment_kwh=row["curtail"]*dt,
                         storage_loss_kwh=((1-a.eta_charge)*row["charge"]+(1/a.eta_discharge-1)*row["discharge"])*dt,
                         grid_cost=(a.buy_price[t]*row["buy"]-a.sell_price[t]*row["sell"])*dt,
                         delivered_revenue=a.ev_price[t]*row["ev"]*dt,
                         degradation_cost=a.degradation_per_kwh*(row["charge"]+row["discharge"])*dt,
                         soh_start=intervals[s][0].signals["soh"].value,soh_end=intervals[s][0].signals["soh"].value)
            keys = (str((s,t)),s)
            entry["constraint_checks"] = [check for check in result["checks"]["records"]
                                          if check["key"] in keys or check["key"].startswith(str((s,t))[:-1]+",")]
            ledger.append(entry)
        timeline = data.series[0].intervals
        for row in result["ties"]:
            ledger.append(dict(row,record_type="tie",**base,timestamp=timeline[row["step"]].timestamp,
                               method=method,model_version=MODEL_VERSION,input_config_sha256=digest,
                               status=result["status"],tie_flow_kw=row["forward"]-row["reverse"]))
        for t,interval in enumerate(timeline):
            rows = [r for r in result["schedule"] if r["step"]==t]
            if not rows:
                continue
            ledger.append(dict(record_type="system",**base,timestamp=interval.timestamp,step=t,method=method,
                               model_version=MODEL_VERSION,input_config_sha256=digest,status=result["status"],
                               grid_import_kw=sum(r["buy"] for r in rows),grid_export_kw=sum(r["sell"] for r in rows),
                               ev_served_kwh=sum(r["ev"] for r in rows)*case.dt_hours,
                               base_served_kwh=sum(r["base"] for r in rows)*case.dt_hours,
                               constraint_checks=[x for x in result["checks"]["records"] if x["key"]==str(t)]))
        failure = None if result["status"]=="optimal" else result["status"]
        if method=="centralized" and result["status"]=="optimal" and not result["checks"]["passed"]:
            failure = "constraint_violation"
            return CoordinationResult(**base,status="failed",failure_code=failure,ledger=ledger,metadata=metadata)
        return CoordinationResult(**base,status=result["status"],failure_code=failure,ledger=ledger,metadata=metadata)
