"""Single entry point for daily SOH degradation and its economic cost."""

from dataclasses import dataclass
from typing import List

from src.battery.asset_eval import (
    AgingResult,
    ALPHA_DEFAULT,
    C_NEW_DEFAULT,
    GAMMA_DEFAULT,
    evaluate_battery_aging,
    k_cal_piecewise,
    residual_value_deriv,
    SOH_RETIRE,
)
from src.battery.state import BatteryState


@dataclass
class DailyDegradation:
    """Degradation result used by the state transition and objective layers."""

    aging_results: List[AgingResult]
    cost_yuan: float
    replacement_cost_yuan: float = 0.0

    @property
    def delta_soh(self):
        if not self.aging_results:
            return 0.0
        # For the aggregate state, use the count-weighted mean loss.
        n = sum(r.n_batteries for r in self.aging_results)
        return sum(r.n_batteries * r.delta_soh_total for r in self.aging_results) / max(n, 1)


def evaluate_daily_degradation(
    state: BatteryState,
    storage_power_kw,
    soc_pct,
    *,
    c_new=None,
    gamma=GAMMA_DEFAULT,
    alpha=ALPHA_DEFAULT,
    use_continuous_kcal=False,
    interval_hours=1.0,
):
    """Evaluate degradation without mutating ``state``."""
    if c_new is None:
        c_new = state.group_manager.eb_kwh * 1000.0
    aging, cost = evaluate_battery_aging(
        state.group_manager,
        list(storage_power_kw),
        list(soc_pct),
        eb_kwh=state.group_manager.eb_kwh,
        c_new=c_new,
        gamma=gamma,
        alpha=alpha,
        use_continuous_kcal=use_continuous_kcal,
        interval_hours=interval_hours,
    )
    return DailyDegradation(aging, cost)


def marginal_cycle_cost_per_kwh(
    state: BatteryState,
    *,
    c_new=None,
    gamma=GAMMA_DEFAULT,
    alpha=ALPHA_DEFAULT,
    reference_dod=0.5,
):
    """Return the SOH-aware marginal cycle cost for leader scheduling.

    The exact daily cycle cost is nonlinear in DOD and trajectory.  This
    marginal value is the convexified coefficient used by the hourly MILP;
    the exact aging evaluator remains the accounting authority at day end.
    """
    if c_new is None:
        c_new = state.group_manager.eb_kwh * 1000.0
    dod = max(0.01, min(1.0, float(reference_dod)))
    factor = gamma * (dod ** (alpha - 1.0))
    def wear_multiplier(soh):
        # A transparent interim reserve for the shorter remaining life of
        # low-SOH groups.  Its coefficient will be calibrated in the Pareto
        # experiments; the exact trajectory evaluator remains authoritative.
        remaining = max(SOH_RETIRE, min(1.0, soh)) - SOH_RETIRE
        return 1.0 + 0.5 * (1.0 - remaining / (1.0 - SOH_RETIRE))

    return sum(
        g.n_batteries * c_new * factor * residual_value_deriv(g.soh_avg)
        * wear_multiplier(g.soh_avg)
        for g in state.group_manager.groups
    )


def calendar_degradation_cost_per_day(
    state: BatteryState,
    average_soc_pct,
    *,
    c_new=None,
):
    """Return the state-dependent calendar aging cost for reporting/objectives."""
    if c_new is None:
        c_new = state.group_manager.eb_kwh * 1000.0
    rate = k_cal_piecewise(float(average_soc_pct)) / 365.0
    return sum(
        g.n_batteries * c_new * rate * residual_value_deriv(g.soh_avg)
        for g in state.group_manager.groups
    )
