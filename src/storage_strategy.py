"""
Energy storage dispatch strategies for the microgrid digital twin.

All strategies implement the same interface: compute() returns
(desired_storage_kW, new_soc_pct) given generation, load, and current state.
Positive storage_kW = discharging, negative = charging.
"""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class StorageStrategy(ABC):
    """Abstract base class for storage dispatch strategies."""

    @abstractmethod
    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        """
        Compute storage dispatch for one time step.

        Args:
            pv_kw: PV generation power (kW)
            wind_kw: Wind generation power (kW)
            load_kw: Load consumption power (kW)
            soc_pct: Current state of charge (%)
            rated_capacity_kwh: Storage rated capacity (kWh)
            rated_power_kw: Storage rated PCS power (kW)
            time_hour: Current hour of day (0-23)

        Returns:
            tuple: (storage_kW, new_soc_pct)
                storage_kW > 0: discharging, < 0: charging
        """
        pass


class SimpleBalancing(StorageStrategy):
    """
    Simple supply-demand balancing strategy.

    - When generation (PV + wind) > load: charge storage with excess
    - When generation < load: discharge storage to cover deficit
    - Respects SOC limits (soc_min / soc_max) and PCS power limit
    """

    def __init__(self, soc_min=10.0, soc_max=90.0,
                 charge_eff=0.95, discharge_eff=0.95):
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.charge_eff = charge_eff
        self.discharge_eff = discharge_eff

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        net_kw = pv_kw + wind_kw - load_kw

        if abs(net_kw) < 0.1:
            return 0.0, soc_pct

        if net_kw > 0:
            return self._charge(net_kw, soc_pct, rated_capacity_kwh,
                                rated_power_kw)
        else:
            return self._discharge(-net_kw, soc_pct, rated_capacity_kwh,
                                   rated_power_kw)

    def _charge(self, excess_kw, soc_pct, rated_capacity_kwh,
                rated_power_kw):
        charge_power = min(excess_kw, rated_power_kw)
        max_charge_energy = (self.soc_max - soc_pct) / 100.0 * rated_capacity_kwh
        if charge_power > max_charge_energy:
            charge_power = float(max_charge_energy)
        delta_soc = charge_power * self.charge_eff / rated_capacity_kwh * 100.0
        new_soc = min(soc_pct + delta_soc, self.soc_max)
        return -charge_power, new_soc

    def _discharge(self, deficit_kw, soc_pct, rated_capacity_kwh,
                   rated_power_kw):
        discharge_power = min(deficit_kw, rated_power_kw)
        avail_energy = (soc_pct - self.soc_min) / 100.0 * rated_capacity_kwh
        max_discharge_power = avail_energy * self.discharge_eff
        if discharge_power > max_discharge_power:
            discharge_power = float(max_discharge_power)
        delta_soc = -(discharge_power / self.discharge_eff) / rated_capacity_kwh * 100.0
        new_soc = max(soc_pct + delta_soc, self.soc_min)
        return discharge_power, new_soc


STRATEGY_REGISTRY = {
    "simple_balancing": SimpleBalancing,
}


def get_strategy(name="simple_balancing", **kwargs):
    """
    Factory function to create a storage strategy instance.

    Args:
        name: Strategy name (key in STRATEGY_REGISTRY)
        **kwargs: Passed to strategy constructor

    Returns:
        StorageStrategy instance
    """
    if name not in STRATEGY_REGISTRY:
        available = list(STRATEGY_REGISTRY.keys())
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {available}"
        )
    return STRATEGY_REGISTRY[name](**kwargs)
