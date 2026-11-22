"""Small multi-day evaluator for the dynamic SOH game.

This is an evaluation loop, not yet a long-horizon optimizer.  Each day solves
the existing hourly game, applies the exact daily aging model, and carries the
SOH/group state to the next day.  It provides the accounting needed before a
Pareto sweep or a terminal-value MPC is introduced.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import List

from src.battery.asset_eval import C_NEW_PER_KWH
from src.battery.degradation import (
    calendar_degradation_cost_per_day,
)
from src.battery.state import BatteryState
from src.game.equilibrium import solve_equilibrium
from src.game.case import GameCase
from src.game.state_transition import transition_battery_state


@dataclass
class DailyHorizonResult:
    day: int
    game_objective_yuan: float
    operating_profit_yuan: float
    marginal_degradation_proxy_yuan: float
    aging_cost_yuan: float
    calendar_cost_yuan: float
    replacement_cost_yuan: float
    long_term_profit_yuan: float
    soh_start: float
    soh_end: float
    usable_capacity_kwh: float
    throughput_kwh: float
    shed_kwh: float
    curt_kwh: float


@dataclass
class MultiDayResult:
    days: List[DailyHorizonResult] = field(default_factory=list)

    @property
    def cumulative_profit_yuan(self):
        return sum(d.long_term_profit_yuan for d in self.days)

    @property
    def final_soh(self):
        return self.days[-1].soh_end if self.days else None


def calculate_daily_ledger(case: GameCase, result: dict, aging_cost_yuan: float,
                           replacement_cost_yuan: float) -> dict:
    """Separate the game objective proxy from ex-post economic accounting.

    The game objective includes a marginal degradation proxy used to shape
    dispatch. The reported long-term ledger instead charges the trajectory-
    based aging and replacement costs once.
    """
    energy_by_hour = [0.0] * case.T
    for profile in result["response"].values():
        for hour, energy in enumerate(profile):
            energy_by_hour[hour] += float(energy)
    revenue = sum(float(price) * energy
                  for price, energy in zip(result["prices"], energy_by_hour))
    shed_cost = case.penalty_shed * sum(float(x) for x in result["shed"])
    curtailment_cost = case.penalty_curt * sum(float(x) for x in result["curt"])
    storage_throughput = sum(
        float(discharge) + float(charge)
        for discharge, charge in zip(result["p_dis"], result["p_ch"])
    )
    proxy_cost = case.effective_deg_cost_per_kwh * storage_throughput
    operating_profit = revenue - shed_cost - curtailment_cost
    long_term_profit = operating_profit - case.life_weight * (
        aging_cost_yuan + replacement_cost_yuan
    )
    return {
        "game_objective_yuan": float(result["profit"]),
        "operating_profit_yuan": float(operating_profit),
        "marginal_degradation_proxy_yuan": float(proxy_cost),
        "long_term_profit_yuan": float(long_term_profit),
    }


def simulate_multiday(
    case: GameCase,
    *,
    days=7,
    initial_soh=1.0,
    dynamic_soh=True,
    life_weight=1.0,
    prefer=None,
):
    """Evaluate repeated daily games with consistent SOH accounting."""
    if days <= 0:
        raise ValueError("days 必须为正数")
    state = BatteryState.new(
        total_batteries=1,
        eb_kwh=case.battery.capacity_kwh,
        init_soh=[initial_soh],
    )
    daily_c_new = case.battery.capacity_kwh * C_NEW_PER_KWH
    soc = case.battery.soc0
    output = MultiDayResult()
    for day in range(1, days + 1):
        daily_case = deepcopy(case)
        daily_case.battery.soc0 = soc
        daily_case.battery_state = state if dynamic_soh else None
        daily_case.life_weight = life_weight
        result = solve_equilibrium(daily_case, prefer=prefer)
        storage = [result["p_dis"][t] - result["p_ch"][t]
                   for t in range(daily_case.T)]
        soc_fraction = result["soc"]
        effective_c_new = state.usable_capacity_kwh * C_NEW_PER_KWH
        degradation_state, aging = transition_battery_state(
            state, storage, [100.0 * x for x in soc_fraction],
            c_new=effective_c_new)
        calendar = calendar_degradation_cost_per_day(
            state, sum(100.0 * x for x in soc_fraction) / len(soc_fraction),
            c_new=effective_c_new)
        # Aging cost is already returned by the exact trajectory evaluator.
        # Do not add calendar cost a second time to the objective; keep the
        # component separately in the ledger for diagnosis.
        replacement = aging.replacement_cost_yuan
        ledger = calculate_daily_ledger(
            daily_case, result, aging.cost_yuan, replacement
        )
        output.days.append(DailyHorizonResult(
            day=day,
            game_objective_yuan=ledger["game_objective_yuan"],
            operating_profit_yuan=ledger["operating_profit_yuan"],
            marginal_degradation_proxy_yuan=ledger["marginal_degradation_proxy_yuan"],
            aging_cost_yuan=aging.cost_yuan,
            calendar_cost_yuan=calendar,
            replacement_cost_yuan=replacement,
            long_term_profit_yuan=ledger["long_term_profit_yuan"],
            soh_start=state.soh_system,
            soh_end=degradation_state.soh_system,
            usable_capacity_kwh=state.usable_capacity_kwh,
            throughput_kwh=aging.aging_results[0].energy_throughput_kwh
            if aging.aging_results else 0.0,
            shed_kwh=sum(result["shed"]),
            curt_kwh=sum(result["curt"]),
        ))
        state = degradation_state
        soc = result["soc"][-1]
    return output
