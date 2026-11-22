"""Explicit battery state contract for the dynamic SOH model.

The existing :class:`BatteryGroupManager` remains the storage engine.  This
wrapper gives game, rolling and economic modules one stable state interface
without changing the current equilibrium implementation.
"""

from copy import deepcopy
from dataclasses import dataclass

from src.battery.group import BatteryGroupManager


@dataclass
class BatteryState:
    """Current battery state at a decision boundary (normally day boundary)."""

    group_manager: BatteryGroupManager

    @classmethod
    def new(cls, total_batteries=1, eb_kwh=7500.0, init_soh=None):
        return cls(BatteryGroupManager(total_batteries, eb_kwh, init_soh))

    def copy(self):
        return BatteryState(deepcopy(self.group_manager))

    @property
    def soh_system(self):
        return self.group_manager.soh_system

    @property
    def usable_capacity_kwh(self):
        return self.group_manager.usable_capacity_kwh

    @property
    def total_n_batteries(self):
        return self.group_manager.total_n_batteries

    def as_dict(self):
        summary = self.group_manager.summary()
        return {
            "soh_system": self.soh_system,
            "usable_capacity_kwh": self.usable_capacity_kwh,
            "total_n_batteries": self.total_n_batteries,
            "retirement_count": self.group_manager.retirement_count,
            "total_replace_cost_yuan": self.group_manager.total_replace_cost,
            "groups": summary["groups"],
        }
