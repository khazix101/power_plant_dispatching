"""Battery state transition used by the future rolling SOH model."""

from src.battery.asset_eval import C_SALVAGE_RATIO, C_NEW_DEFAULT
from src.battery.degradation import evaluate_daily_degradation
from src.battery.state import BatteryState


def transition_battery_state(
    state: BatteryState,
    storage_power_kw,
    soc_pct,
    *,
    c_new=C_NEW_DEFAULT,
    c_salvage_ratio=C_SALVAGE_RATIO,
    gamma=None,
    alpha=None,
    interval_hours=1.0,
):
    """Return ``(next_state, degradation)`` after one daily trajectory.

    The input state is never mutated.  SOH update and group migration happen
    only at the transition boundary, which keeps the hourly game model small.
    """
    next_state = state.copy()
    kwargs = {"c_new": c_new}
    if gamma is not None:
        kwargs["gamma"] = gamma
    if alpha is not None:
        kwargs["alpha"] = alpha
    kwargs["interval_hours"] = interval_hours
    degradation = evaluate_daily_degradation(
        next_state, storage_power_kw, soc_pct, **kwargs)
    from src.battery.asset_eval import update_group_soh_after_day
    update_group_soh_after_day(next_state.group_manager, degradation.aging_results)
    events = next_state.group_manager.check_migration(
        c_new=c_new, c_salvage_ratio=c_salvage_ratio)
    degradation.replacement_cost_yuan = sum(e["cost_yuan"] for e in events)
    return next_state, degradation
