"""电阻型直流微电网模型和动态 BESS SOC 工具。"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np


@dataclass
class DCBranch:
    from_bus: str
    to_bus: str
    resistance_ohm: float
    current_limit_a: float = 0.0


@dataclass
class DCNetworkConfig:
    buses: Tuple[str, ...] = ("Storage_Bus", "PV_Bus", "Wind_Bus", "Load_Bus")
    reference_bus: str = "Storage_Bus"
    base_voltage_kv: float = 0.4
    base_power_kw: float = 1000.0
    voltage_min_pu: float = 0.95
    voltage_max_pu: float = 1.05
    branches: List[DCBranch] = field(default_factory=lambda: [
        DCBranch("Storage_Bus", "PV_Bus", 0.005),
        DCBranch("Storage_Bus", "Wind_Bus", 0.005),
        DCBranch("Storage_Bus", "Load_Bus", 0.005),
    ])


class DCMicrogridPowerFlow:
    """基于节点电导矩阵的线性化 DC 准静态潮流。"""

    def __init__(self, config=None):
        self.config = config or DCNetworkConfig()
        self._index = {bus: i for i, bus in enumerate(self.config.buses)}
        if self.config.reference_bus not in self._index:
            raise ValueError("reference_bus 不在 buses 中")
        self._reference = self._index[self.config.reference_bus]
        self._g = self._build_gbus()
        self._keep = [i for i in range(len(self.config.buses)) if i != self._reference]
        self._g_reduced = self._g[np.ix_(self._keep, self._keep)]
        if np.linalg.matrix_rank(self._g_reduced) < len(self._keep):
            raise ValueError("去掉参考母线后的电导矩阵不可逆")

    def _build_gbus(self):
        g = np.zeros((len(self.config.buses), len(self.config.buses)))
        for branch in self.config.branches:
            if branch.resistance_ohm <= 0:
                raise ValueError("直流线路电阻必须为正")
            i = self._index[branch.from_bus]
            j = self._index[branch.to_bus]
            y = 1.0 / branch.resistance_ohm
            g[i, i] += y
            g[j, j] += y
            g[i, j] -= y
            g[j, i] -= y
        return g

    def solve(self, injections_kw: Dict[str, float], reference_pu=1.0):
        vector = np.zeros(len(self.config.buses))
        for bus, value in injections_kw.items():
            vector[self._index[bus]] = float(value)
        delta = np.linalg.solve(
            self._g_reduced,
            vector[self._keep] / self.config.base_power_kw,
        )
        voltage = np.full(len(self.config.buses), float(reference_pu))
        voltage[self._keep] += delta

        branches = []
        losses_kw = 0.0
        for branch in self.config.branches:
            i = self._index[branch.from_bus]
            j = self._index[branch.to_bus]
            drop = voltage[i] - voltage[j]
            current_a = abs(drop * self.config.base_voltage_kv * 1000 / branch.resistance_ohm)
            loss_kw = (current_a / 1000) ** 2 * branch.resistance_ohm
            losses_kw += loss_kw
            branches.append({
                "from_bus": branch.from_bus,
                "to_bus": branch.to_bus,
                "current_a": current_a,
                "loss_kw": loss_kw,
                "rating_a": branch.current_limit_a or None,
                "thermal_verified": branch.current_limit_a > 0,
                "loading_pu": current_a / branch.current_limit_a
                if branch.current_limit_a > 0 else None,
            })
        return {
            "voltage_pu": {bus: float(voltage[i]) for bus, i in self._index.items()},
            "vmin_pu": float(voltage.min()),
            "vmax_pu": float(voltage.max()),
            "voltage_feasible": bool(
                voltage.min() >= self.config.voltage_min_pu
                and voltage.max() <= self.config.voltage_max_pu
            ),
            "branches": branches,
            "thermal_limits_verified": all(x["thermal_verified"] for x in branches),
            "losses_kw": float(losses_kw),
        }


@dataclass
class DCSOCConfig:
    capacity_kwh: float = 2000.0
    power_kw: float = 800.0
    soc_min: float = 0.10
    soc_reserve: float = 0.20
    soc_max: float = 0.90
    eta_charge: float = 0.95
    eta_discharge: float = 0.95


class DCSOCState:
    def __init__(self, config=None, soc=0.5):
        self.config = config or DCSOCConfig()
        self.soc = float(soc)
        self._validate()

    def _validate(self):
        if not self.config.soc_min <= self.soc <= self.config.soc_max:
            raise ValueError("SOC 超出物理上下限")

    def limit_power(self, requested_kw, dt_hours):
        c = self.config
        p = max(-c.power_kw, min(c.power_kw, float(requested_kw)))
        if p >= 0:
            available = max(0, (self.soc - c.soc_reserve)
                            * c.capacity_kwh * c.eta_discharge / dt_hours)
            return min(p, available)
        room = max(0, (c.soc_max - self.soc)
                   * c.capacity_kwh / (c.eta_charge * dt_hours))
        return max(p, -room)

    def advance(self, p_kw, dt_hours):
        c = self.config
        if p_kw >= 0:
            self.soc -= p_kw * dt_hours / (c.eta_discharge * c.capacity_kwh)
        else:
            self.soc += -p_kw * dt_hours * c.eta_charge / c.capacity_kwh
        self._validate()


def balance_dc_step(power_flow, soc, pv_kw, wind_kw, ev_request_kw,
                    non_ev_kw, p_schedule_kw, dt_hours=1.0):
    p_bess = soc.limit_power(p_schedule_kw, dt_hours)
    pv_used = max(0.0, float(pv_kw))
    wind_used = max(0.0, float(wind_kw))
    ev_served = max(0.0, float(ev_request_kw))
    result = power_flow.solve({
        "PV_Bus": pv_used,
        "Wind_Bus": wind_used,
        "Load_Bus": -(ev_served + non_ev_kw),
    })
    required = ev_served + non_ev_kw + result["losses_kw"] - pv_used - wind_used
    residual = required - p_bess
    ev_shed = pv_curtail = wind_curtail = 0.0
    if residual > 0:
        ev_shed = min(ev_served, residual)
        ev_served -= ev_shed
    else:
        surplus = -residual
        cut = min(pv_used, surplus)
        pv_used -= cut
        pv_curtail += cut
        surplus -= cut
        cut = min(wind_used, surplus)
        wind_used -= cut
        wind_curtail += cut
    flow = power_flow.solve({
        "PV_Bus": pv_used,
        "Wind_Bus": wind_used,
        "Load_Bus": -(ev_served + non_ev_kw),
    })
    soc.advance(p_bess, dt_hours)
    return {
        "p_bess_kw": p_bess,
        "soc": soc.soc,
        "ev_requested_kw": ev_request_kw,
        "ev_served_kw": ev_served,
        "ev_shed_kw": ev_shed,
        "pv_used_kw": pv_used,
        "wind_used_kw": wind_used,
        "pv_curtailed_kw": pv_curtail,
        "wind_curtailed_kw": wind_curtail,
        "external_supply_kw": 0.0,
        "power_flow": flow,
    }
