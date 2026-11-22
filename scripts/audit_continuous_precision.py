"""Second ledger: original residual identity and isolated precise execution replay."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.continuous_precision_step_audit import balance_dc_step_precise
from src.game.dc_microgrid import DCMicrogridPowerFlow,DCNetworkConfig,DCSOCConfig,DCSOCState
from src.battery.state import BatteryState
from src.game.state_transition import transition_battery_state


def audit(run):
    days=[]
    for day in run["days"]:
        s=day["summary"]
        soc=DCSOCState(DCSOCConfig(capacity_kwh=s["usable_capacity_start_kwh"],power_kw=800,
            soc_min=.1,soc_reserve=.2,soc_max=.9),s["soc_start"])
        network=DCMicrogridPowerFlow(DCNetworkConfig(ev_posts=8,ev_post_kw=60))
        values=[]
        for step in day["steps"]:
            f=balance_dc_step_precise(network,soc,step["pv_kw"],step["wind_kw"],step["ev_requested_kw"],step["base_load_kw"],step["p_bess_kw"],dt_hours=.5,ev_service_limit_kw=480)
            p=f["p_bess_kw"]
            loss=(p*(1/.95-1) if p>=0 else -p*(1-.95))*.5
            values.append({"renewable":(step["pv_kw"]+step["wind_kw"])*.5,
                "useful":(f["ev_served_kw"]+f["non_ev_served_kw"])*.5,
                "curtail":(f["pv_curtailed_kw"]+f["wind_curtailed_kw"])*.5,
                "loss":loss+f["losses_kw"]*.5,"residual":f["residual_kw"]*.5,
                "power_deviation_kw":p-step["p_bess_kw"],
                "ev_delivery_deviation_kwh":f["ev_served_kw"]*.5-step["served_kwh"],
                "base_delivery_deviation_kwh":f["non_ev_served_kw"]*.5-step["non_ev_served_kwh"]})
            values[-1].update({"soc":soc.soc,"power_kw":p,
                "revenue_delta_yuan":step["price"]*values[-1]["ev_delivery_deviation_kwh"],
                "curtailment_delta_kwh":values[-1]["curtail"]-step["curtailment_kwh"],
                "soc_delta":soc.soc-step["soc"]})
        identity=math.fsum(v["renewable"]-v["useful"]-v["curtail"]-v["loss"]for v in values)-(soc.soc-s["soc_start"])*s["usable_capacity_start_kwh"]
        rawsum=math.fsum(step["energy_residual_kwh"]for step in day["steps"])
        if sum(group["n"]>0 for group in day["input_state"]["groups"].values())!=1 or day["input_state"]["total_n_batteries"]!=1:
            raise ValueError("precision aging re-evaluation requires recorded single-group state; do not reconstruct arbitrary groups")
        state=BatteryState.new(total_batteries=1,eb_kwh=2000,init_soh=[s["soh_start"]])
        _,aging=transition_battery_state(state,[v["power_kw"]for v in values],
            [100*s["soc_start"]]+[100*v["soc"]for v in values],c_new=2000000,interval_hours=.5)
        aging_delta=aging.cost_yuan-s["aging_cost_yuan"]
        revenue_delta=math.fsum(v["revenue_delta_yuan"]for v in values)
        base_delivery_delta=math.fsum(v["base_delivery_deviation_kwh"]for v in values)
        ev_delivery_delta=math.fsum(v["ev_delivery_deviation_kwh"]for v in values)
        curt_delta=math.fsum(v["curtailment_delta_kwh"]for v in values)
        days.append({"date":day["date"],"original_status":day["status"],
            "original_daily_identity_kwh":s["energy_identity_residual_kwh"],
            "original_fsum_step_residual_kwh":rawsum,
            "original_identity_plus_step_sum_kwh":s["energy_identity_residual_kwh"]+rawsum,
            "capacity_revaluation_separately_accounted_kwh":s["capacity_revaluation_loss_kwh"],
            "precise_replay_identity_kwh":identity,
            "precise_replay_max_step_residual_kwh":max(abs(v["residual"])for v in values),
            "same_acceptance_gate_kwh":1e-6,
            "precise_replay_gate_passed":abs(identity)<=1e-6 and all(abs(v["residual"])<=1e-6 for v in values),
            "max_abs_power_deviation_kw":max(abs(v["power_deviation_kw"])for v in values),
            "total_ev_delivery_deviation_kwh":math.fsum(v["ev_delivery_deviation_kwh"]for v in values),
            "total_base_delivery_deviation_kwh":math.fsum(v["base_delivery_deviation_kwh"]for v in values)})
        days[-1].update({"max_abs_soc_deviation":max(abs(v["soc_delta"])for v in values),
            "delivered_revenue_delta_yuan":revenue_delta,"aging_cost_delta_yuan":aging_delta,
            "curtailment_delta_kwh":curt_delta,
            "modeled_operating_value_delta_yuan":revenue_delta-aging_delta+100*ev_delivery_delta+1000*base_delivery_delta-.2*curt_delta})
    return {"status":"secondary_numeric_audit_only","original_arm":run["arm"],"days":days,
        "explanation":"Original day identity equals negative sum of small step residuals; original solver stopping tolerance accumulates. Higher-precision isolated execution replay uses original executed actions and original day input state/capacity; it neither re-optimizes nor replaces the original failure.",
        "precision_copy_sha256":hashlib.sha256((ROOT/"scripts/continuous_precision_step_audit.py").read_bytes()).hexdigest()}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=audit(json.loads(args.input.read_text(encoding="utf8")))
    result["source_sha256"]=hashlib.sha256(args.input.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf8")
    print(json.dumps(result["days"],indent=2))
