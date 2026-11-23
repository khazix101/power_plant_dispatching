"""Price-only policy adapters. Physics and settlement belong to shared replay.

Forecasts are explicit ex-ante inputs, never sliced from future actual rows.
The demand response is a simulation assumption, not a market causal estimate.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch

from src.game.case import BatteryParams, EVUser, GameCase
from src.game.env import PricingEnv, Scenario
from src.game.forecast import ForecastBundle
from src.game.rl import DiscretePolicyNet
from src.game.rl_env import RollingMDPState
from src.game.bri import solve_equilibrium_bri

SEEDS = (11, 23, 37, 53, 71)
SCHEMA = "b-price-agent-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_split(partitions):
    """Entire local days (including forecast coverage) are indivisible."""
    if set(partitions) != {"train", "validation", "test"}:
        raise ValueError("train/validation/test partitions required")
    seen = set()
    for group in partitions.values():
        if not group:
            raise ValueError("empty partition")
        for trace in group:
            days = set()
            for step in trace["steps"]:
                stamps = [step["timestamp"], *step["forecast"]["timestamps"]]
                for stamp in stamps:
                    dt = datetime.fromisoformat(stamp)
                    if dt.utcoffset() is None or dt.utcoffset().total_seconds() != 28800:
                        raise ValueError("explicit Asia/Shanghai +08:00 timestamps required")
                    days.add(dt.date().isoformat())
            if days & seen:
                raise ValueError("date leakage or duplicate episode")
            seen.update(days)
    return digest(partitions)


def observation(state: RollingMDPState, *, previous_storage_kw, congestion):
    b = state.forecast_window
    hour = b.timestamp.hour + b.timestamp.minute / 60
    values = [*np.asarray(b.pv_forecast_kw) / 1000,
              *np.asarray(b.wind_forecast_kw) / 1000,
              *np.asarray(b.base_ev_load_forecast_kw) / 500,
              *np.asarray(b.non_ev_load_forecast_kw) / 500,
              state.soc, state.soh, state.usable_capacity_kwh / 2000,
              state.reserve_kwh / 2000,
              (state.soc * state.usable_capacity_kwh - state.reserve_kwh) / 2000,
              state.backlog_kwh / 500, b.current_ev_load_kw / 500,
              0 if state.previous_price is None else state.previous_price,
              float(state.previous_price is not None), previous_storage_kw / 800,
              np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24),
              b.timestamp.weekday() / 6, state.time_index / 48, *congestion]
    result = np.asarray(values, dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError("nonfinite observation")
    return result


@dataclass(frozen=True)
class RewardConfig:
    unserved: float = 2.0
    curtailment: float = 0.2
    storage_loss: float = 0.1
    network_loss: float = 0.1
    degradation: float = 0.05
    smoothing: float = 0.1
    violation: float = 1000.0

    def __post_init__(self):
        if any(not np.isfinite(v) or v < 0 for v in vars(self).values()):
            raise ValueError("reward coefficients must be finite and nonnegative")


def executed_metrics(replay, actions, manifest, config=RewardConfig()):
    """Consume D's executed ledger without changing its settlement fields."""
    if len(replay["steps"]) != len(actions) or not actions:
        raise ValueError("nonempty one-to-one action/execution ledger required")
    rows = []
    e = manifest["execution_config"]
    previous = None
    for record, action in zip(replay["steps"], actions):
        if not record["included_in_performance"]:
            raise RuntimeError("nonconverged execution retained by runner; cannot train on missing reward")
        requested, served = record["requested_kwh"], record["served_kwh"]
        unserved = max(0.0, requested - served)
        violations = {
            "voltage": int(not record["voltage_feasible"]),
            "energy_balance": int(abs(record["energy_residual_kwh"]) > 1e-6),
            "soc_transition": int(abs(record["soc_residual"]) > 1e-6),
            "soc_bounds": int(not e["soc_min"] - 1e-6 <= record["soc"] <= e["soc_max"] + 1e-6),
            "power": int(abs(record["p_bess_kw"]) > e["battery_power_kw"] + 1e-6),
            "service_cap": int(record["served_kw"] > manifest["ev_service_cap_kw"] + 1e-6),
            "safe_plan": int(not action["safe_feasible"]),
        }
        revenue = record["served_revenue"]
        degradation = config.degradation * abs(record["p_bess_kw"]) * record["dt_hours"]
        smoothing = 0 if previous is None else config.smoothing * (record["price"] - previous) ** 2
        cost = (config.unserved * unserved + config.curtailment * record["curtailment_kwh"]
                + config.storage_loss * record["battery_loss_kwh"]
                + config.network_loss * record["network_loss_kwh"] + degradation)
        value = revenue - cost
        rows.append({"requested_kwh": requested, "served_kwh": served,
                     "unserved_kwh": unserved, "user_cost_yuan": revenue,
                     "delivered_revenue_yuan": revenue,
                     "curtailment_kwh": record["curtailment_kwh"],
                     "storage_loss_kwh": record["battery_loss_kwh"],
                     "network_loss_kwh": record["network_loss_kwh"],
                     "degradation_cost_yuan": degradation, "operating_value_yuan": value,
                     "reward": value - smoothing - config.violation * sum(violations.values()),
                     "hard_violations": violations,
                     "projected": bool(action["projected"] or abs(record["p_bess_kw"] - action["p_schedule_kw"]) > 1e-6),
                     "thermal_limits_verified": record["thermal_limits_verified"]})
        previous = record["price"]
    summary = {key: float(sum(r[key] for r in rows)) for key in rows[0]
               if key not in {"hard_violations", "projected", "thermal_limits_verified"}}
    summary.update(hard_violation_counts={k: sum(r["hard_violations"][k] for r in rows)
                                          for k in rows[0]["hard_violations"]},
                   hard_violation_rate=sum(any(r["hard_violations"].values()) for r in rows) / len(rows),
                   safety_projection_rate=sum(r["projected"] for r in rows) / len(rows),
                   thermal_limits_verified=all(r["thermal_limits_verified"] for r in rows))
    # Terminal constraints are checked separately and never averaged away.
    summary["terminal_violation_count"] = int(not replay["summary"]["terminal_soc_feasible"])
    summary["terminal_violation_count"] += int(replay["summary"]["terminal_soh"] < manifest["terminal_states"]["soh_min"])
    terminal_penalty = config.violation * summary["terminal_violation_count"]
    rows[-1]["reward"] -= terminal_penalty
    summary["reward"] -= terminal_penalty
    return rows, summary


class PriceAdapter:
    """D callback; all methods use identical forecasts and projected schedules."""
    def __init__(self, manifest, price_levels, method="fixed", policy=None, seed=11,
                 stochastic=False, response_value=1.9):
        self.manifest = copy.deepcopy(manifest)
        self.levels = tuple(float(x) for x in price_levels)
        if not self.levels or len(set(self.levels)) != len(self.levels) or not all(np.isfinite(self.levels)):
            raise ValueError("unique finite price_levels required")
        if method not in {"fixed", "rule", "optimization", "agent"}:
            raise ValueError("unknown method")
        if method == "agent" and (policy is None or tuple(policy.price_levels.tolist()) != self.levels):
            raise ValueError("policy and adapter must share exact price_levels")
        self.method, self.policy, self.seed = method, policy, seed
        self.stochastic, self.response_value = stochastic, response_value
        self.actions, self.log_probs, self.features = [], [], []
        self.runtime_s = 0.0

    def inputs(self, step, state):
        f = step["forecast"]
        h = len(f["pv_kw"])
        if any(len(f[k]) != h for k in ("wind_kw", "ev_kw", "base_kw", "timestamps")):
            raise ValueError("forecast shape mismatch")
        if datetime.fromisoformat(f["issued_at"]) > datetime.fromisoformat(step["timestamp"]):
            raise ValueError("forecast issued after decision")
        if f["timestamps"][0] != step["timestamp"]:
            raise ValueError("forecast must start at decision timestamp")
        stamps = [datetime.fromisoformat(ts) for ts in f["timestamps"]]
        if any((b - a).total_seconds() != step["dt_hours"] * 3600 for a, b in zip(stamps, stamps[1:])):
            raise ValueError("forecast cadence must equal execution cadence")
        e = self.manifest["execution_config"]
        # D v1 executes nominal capacity with frozen SOH. Mirror that exactly.
        capacity = e["battery_capacity_kwh"]
        reserve = capacity * e["soc_reserve"]
        bundle = ForecastBundle(datetime.fromisoformat(step["timestamp"]), h,
            f["pv_kw"], f["wind_kw"], f["ev_kw"], f["base_kw"],
            current_ev_load_kw=step["ev_requested_kwh"] / step["dt_hours"],
            soc_current=state["soc"], soh_current=state["soh"], reserve_kwh=reserve)
        observed = RollingMDPState(bundle, state["soc"], state["soh"], capacity, reserve,
                                   step.get("backlog_kwh", 0),
                                   self.actions[-1]["price"] if self.actions else None, step["step_index"])
        congestion = f["congestion"]  # explicit observable summary, no future power flow
        if len(congestion) != 3:
            raise ValueError("congestion requires loading, voltage margin, availability flag")
        feat = observation(observed, previous_storage_kw=self.actions[-1]["p_schedule_kw"] if self.actions else 0,
                           congestion=congestion)
        dt = step["dt_hours"]
        users = [EVUser(f"forecast_{i}", self.response_value, kw * dt, (i,), max(kw, 1), 0)
                 for i, kw in enumerate(f["ev_kw"])]
        battery = BatteryParams(capacity, e["battery_power_kw"], e["eta_charge"], e["eta_discharge"],
                                state["soc"], e["soc_min"], e["soc_max"],
                                self.manifest["terminal_states"]["soc_min"])
        case = GameCase(h, list(f["pv_kw"]), list(f["wind_kw"]), list(f["base_kw"]), users,
                        battery=battery, price_levels=list(self.levels), interval_hours=[dt] * h,
                        ev_service_limit_kw=self.manifest["ev_service_cap_kw"])
        return feat, case

    def __call__(self, step, state):
        start = time.perf_counter()
        feat, case = self.inputs(step, state)
        self.features.append(feat.tolist())
        if self.method == "agent":
            x = torch.tensor(feat)
            if self.stochastic:
                sample = self.policy.sample_action(x, seed=self.seed + step["step_index"])
                prices = sample["prices"].detach().numpy()
                self.log_probs.append(sample["log_prob"])
            else:
                with torch.no_grad():
                    prices = self.policy.act_deterministic(x).numpy()
        elif self.method == "optimization":
            prices = solve_equilibrium_bri(case, max_sweeps=2, prefer="appsi_highs")["prices"]
        elif self.method == "rule":
            prices = [self.levels[0] if case.pv[t] + case.wind[t] >= case.base_load[t] +
                      case.users[t].max_power_kw else self.levels[-1] for t in range(case.T)]
        else:
            prices = [self.levels[len(self.levels) // 2]] * case.T
        if any(float(p) not in self.levels for p in prices):
            raise ValueError("illegal price")
        # PricingEnv already calls follower -> leader -> project_action.
        result = PricingEnv(case, prefer="appsi_highs").evaluate_action(prices, Scenario(case, feat))
        safe = result.safe_action
        if not result.feasible:
            raise RuntimeError("safety projection failed validation; dispatch forbidden")
        accepted = sum(profile[0] for profile in result.response.values()) / step["dt_hours"]
        requested = step["ev_requested_kwh"] / step["dt_hours"]
        rigid = step["ev_rigid_kwh"] / step["dt_hours"]
        action = {"price": float(prices[0]), "prices": list(map(float, prices)),
                  "indices": [self.levels.index(float(p)) for p in prices],
                  "accepted_ev_kw": max(rigid, min(requested, accepted)),
                  "p_schedule_kw": float(safe["p_dis"][0] - safe["p_ch"][0]),
                  "safe_feasible": result.feasible,
                  "projected": bool(safe["dev_l1"] > 1e-6 or result.ev_unserved_kwh > 1e-6),
                  "converged": True}
        self.actions.append(action)
        self.runtime_s += time.perf_counter() - start
        return action


class ContractPriceStrategy:
    """Bridge A's SiteStrategy v1 to the single-site price callback.

    ``forecast`` is an explicit common-trace step including its forecast.
    ``state`` carries current soc/soh. This returns a candidate SiteAction;
    the shared executor still owns physical projection and settlement.
    Import A's dataclass lazily so B can be tested before integration.
    """
    def __init__(self, site_id, adapter, action_factory=None):
        self.site_id, self.adapter, self.action_factory = site_id, adapter, action_factory

    def decide(self, site_id, forecast, state, constraints):
        if site_id != self.site_id:
            raise ValueError("site ID mismatch")
        if constraints["contract_version"] != "1.0.0":
            raise ValueError("unsupported A contract version")
        if list(constraints["price_levels"]) != list(self.adapter.levels):
            raise ValueError("price_levels mismatch")
        if constraints["trace_sha256"] != self.adapter.manifest["trace_sha256"]:
            raise ValueError("trace mismatch")
        factory = self.action_factory
        if factory is None:
            from src.multisite.contract import SiteAction
            factory = SiteAction
        action = self.adapter(forecast, state)
        return factory(site_id=site_id, timestamp=forecast["timestamp"],
                       price_yuan_per_kwh=action["price"], storage_kw=action["p_schedule_kw"],
                       requested_ev_kw=action["accepted_ev_kw"])


def replay_policy(manifest, levels, method, policy=None, seed=11, stochastic=False, config=RewardConfig()):
    from scripts.run_fair_replay_experiments import default_method_configs, execute_common_trace
    adapter = PriceAdapter(manifest, levels, method, policy, seed, stochastic)
    # D v1 requires exactly two callbacks. An identical paired control also
    # verifies initial-state isolation; it does not generate teacher labels.
    control = PriceAdapter(manifest, levels, "fixed")
    started = time.perf_counter()
    raw = execute_common_trace(manifest, default_method_configs(manifest),
                               {"method_a": adapter, "method_b": control})
    rows, summary = executed_metrics(raw["results"]["method_a"], adapter.actions, manifest, config)
    summary.update(policy_runtime_s=adapter.runtime_s, paired_replay_runtime_s=time.perf_counter() - started)
    return adapter, {"steps": rows, "summary": summary, "actions": adapter.actions,
                     "executed_ledger": raw["results"]["method_a"], "trace_sha256": manifest["trace_sha256"]}


def save_policy(path, policy):
    torch.save({"schema": SCHEMA, "features": policy.body[0].in_features,
                "horizon": policy.T, "levels": policy.price_levels.tolist(),
                "hidden": policy.body[0].out_features, "state_dict": policy.state_dict()}, path)


def load_policy(path, expected_levels):
    checkpoint = torch.load(Path(path), weights_only=True, map_location="cpu")
    if checkpoint["schema"] != SCHEMA or checkpoint["levels"] != list(expected_levels):
        raise ValueError("checkpoint schema/price_levels mismatch")
    policy = DiscretePolicyNet(checkpoint["features"], checkpoint["horizon"], checkpoint["levels"],
                               hidden=checkpoint["hidden"])
    policy.load_state_dict(checkpoint["state_dict"])
    policy.eval()
    return policy


def train(partitions, levels, seed, episodes=20, bc_epochs=0, config=RewardConfig()):
    validate_split(partitions)
    if episodes < 1 or bc_epochs < 0:
        raise ValueError("positive episodes and nonnegative bc_epochs required")
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    first = partitions["train"][0]
    adapter = PriceAdapter(first, levels)
    feat, case = adapter.inputs(first["steps"][0], first["initial_states"])
    policy = DiscretePolicyNet(len(feat), case.T, levels, hidden=64, seed=seed)
    optimizer = torch.optim.Adam(policy.parameters(), lr=0.001)
    if bc_epochs:
        examples = []
        for trace in partitions["train"]:
            teacher, _ = replay_policy(trace, levels, "optimization", config=config)
            examples.extend(zip(teacher.features, [a["indices"] for a in teacher.actions]))
        x = torch.tensor([x for x, _ in examples], dtype=torch.float32)
        y = torch.tensor([y for _, y in examples], dtype=torch.long)
        for _ in range(bc_epochs):
            optimizer.zero_grad()
            loss = -policy.log_prob(x, y).mean()
            loss.backward()
            optimizer.step()
    history, best, best_value, baseline = [], None, -float("inf"), 0.0
    for episode in range(episodes):
        trace = partitions["train"][int(rng.integers(len(partitions["train"])))]
        actor, result = replay_policy(trace, levels, "agent", policy, seed + episode * 10000, True, config)
        rewards = [r["reward"] for r in result["steps"]]
        returns = np.cumsum(rewards[::-1])[::-1].copy()
        loss = -(torch.stack(actor.log_probs) * torch.tensor((returns - baseline) / 100,
                                                             dtype=torch.float32)).mean()
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0)
        optimizer.step()
        baseline = 0.9 * baseline + 0.1 * float(np.mean(returns))
        validation = np.mean([replay_policy(t, levels, "agent", policy, config=config)[1]["summary"]["reward"]
                              for t in partitions["validation"]])
        history.append({"episode": episode, "reward": sum(rewards), "validation_reward": float(validation),
                        "actions": actor.actions, "execution": result})
        if validation > best_value:
            best_value, best = validation, copy.deepcopy(policy.state_dict())
    policy.load_state_dict(best)
    return policy, history
