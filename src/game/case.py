"""
SFSE 小算例定义 —— 离网高速充电站安全可行主从博弈。

本模块只承载"算例数据 + 参数"，不含任何优化模型，
便于后续替换为真实数字孪生导出的场景数据。

记号约定:
    T            时段数 (h)
    I            EV 用户数
    pv[t]        光伏出力 (kW)
    wind[t]      风电出力 (kW)
    base_load[t] 非 EV 基础负荷 (kW)
    users[i]     第 i 个 EV 用户 (需求电量 / 可用时段 / 单时最大功率)
    price_levels 领导者可选充电电价档位 (元/kWh)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from src.battery.state import BatteryState


@dataclass
class BatteryParams:
    """储能系统参数 (默认对齐现有数字孪生平台量级)。"""
    capacity_kwh: float = 1000.0     # 额定容量
    power_kw: float = 250.0          # 变流器额定功率
    charge_eff: float = 0.95
    discharge_eff: float = 0.95
    soc0: float = 0.50               # 初始 SOC (0~1)
    soc_min: float = 0.10
    soc_max: float = 0.90
    soc_end_min: float = 0.50        # 终端 SOC 下限 (日终留底)
    deg_cost_per_kwh: float = 0.05   # 线性吞吐退化成本 (元/kWh)


@dataclass
class EVUser:
    """单个 EV 用户 (价格弹性需求)。

    用户对充电有保留效用 v_i (元/kWh): 当 p_t < v_i 时愿意充电,
    当 p_t ≥ v_i 时该时段不充电。需求弹性是使主从博弈非退化的关键,
    否则领导者将始终把电价抬到上限。
    """
    name: str
    value_per_kwh: float             # 保留效用 v_i (元/kWh)
    energy_max_kwh: float            # 当日最大期望充电量
    available: Tuple[int, ...]       # 可用时段集合 (0-based)
    max_power_kw: float              # 单时段最大充电功率
    energy_min_kwh: float = 0.0      # 当日最低服务需求，默认允许完全弹性


@dataclass
class GameCase:
    """离网充电站主从博弈算例。"""
    T: int
    pv: List[float]
    wind: List[float]
    base_load: List[float]
    users: List[EVUser]
    battery: BatteryParams = field(default_factory=BatteryParams)
    price_levels: List[float] = field(
        default_factory=lambda: [1.5, 1.7, 1.9, 2.1, 2.3, 2.5]
    )
    price_ramp_penalty: float = 0.0
    # Normalized EV congestion cost: coefficient times
    # (EV power / station EV limit)^2 per interval.
    ev_congestion_penalty: float = 0.0
    ev_service_limit_kw: Optional[float] = None
    ev_rigid_load_kw: Optional[List[float]] = None
    ev_demand_elasticity: float = 0.0
    demand_reference_price: float = 1.4
    demand_forecast_price: Optional[float] = None
    penalty_shed: float = 2.0        # 失负荷惩罚 (元/kWh)，体现离网可靠性代价
    penalty_curt: float = 0.2        # 弃电惩罚 (元/kWh)
    battery_state: Optional[BatteryState] = None
    life_weight: float = 1.0         # 生命周期成本权重 λ
    interval_hours: Optional[List[float]] = None

    def __post_init__(self):
        if self.interval_hours is None:
            self.interval_hours = [1.0] * self.T
        else:
            self.interval_hours = [float(dt) for dt in self.interval_hours]
            if len(self.interval_hours) != self.T:
                raise ValueError("interval_hours 长度必须等于 T")
            if any(dt <= 0 for dt in self.interval_hours):
                raise ValueError("interval_hours 必须为正数")
        if self.ev_demand_elasticity < 0:
            raise ValueError("ev_demand_elasticity 不允许为负")
        if self.ev_rigid_load_kw is None:
            self.ev_rigid_load_kw = [0.0] * self.T
        else:
            self.ev_rigid_load_kw = [float(value) for value in self.ev_rigid_load_kw]
            if len(self.ev_rigid_load_kw) != self.T:
                raise ValueError("ev_rigid_load_kw 长度必须等于 T")
            if any(value < 0 for value in self.ev_rigid_load_kw):
                raise ValueError("ev_rigid_load_kw 不允许为负")
        if self.demand_reference_price <= 0:
            raise ValueError("demand_reference_price 必须为正数")
        if self.demand_forecast_price is None:
            self.demand_forecast_price = self.demand_reference_price
        elif self.demand_forecast_price <= 0:
            raise ValueError("demand_forecast_price 必须为正数")

    @property
    def I(self) -> int:
        return len(self.users)

    @property
    def effective_capacity_kwh(self) -> float:
        """Current usable capacity when a dynamic SOH state is attached."""
        if self.battery_state is not None:
            return self.battery_state.usable_capacity_kwh
        return self.battery.capacity_kwh

    @property
    def effective_deg_cost_per_kwh(self) -> float:
        """Leader objective coefficient, state-aware when SOH is attached."""
        if self.battery_state is not None:
            from src.battery.degradation import marginal_cycle_cost_per_kwh
            # The replacement-cost basis is the nominal asset, rather than
            # its SOH-reduced usable energy.  Capacity is already represented
            # by ``effective_capacity_kwh``; shrinking both values makes the
            # health signal non-monotone and can make a lower-SOH asset appear
            # cheaper to cycle.
            return self.life_weight * marginal_cycle_cost_per_kwh(
                self.battery_state,
                c_new=self.battery_state.group_manager.eb_kwh * 1000.0,
            )
        return self.battery.deg_cost_per_kwh


def build_small_case() -> GameCase:
    """构造 Stage-1 小算例: 6 时段 / 1 储能 / 3 EV 用户。

    设计意图:
      - 风电夜间较足、光伏正午较足，基础负荷平稳；
      - 3 个 EV 用户可用时段互不相同，形成跨时段负荷转移需求；
      - 离网运行，缺额必须由储能或失负荷承担。
    """
    users = [
        EVUser("U1", value_per_kwh=1.55, energy_max_kwh=200.0,
               available=(1, 2, 3, 4), max_power_kw=80.0),
        EVUser("U2", value_per_kwh=1.35, energy_max_kwh=150.0,
               available=(2, 3, 4, 5), max_power_kw=60.0),
        EVUser("U3", value_per_kwh=1.15, energy_max_kwh=120.0,
               available=(0, 1, 4, 5), max_power_kw=50.0),
    ]
    return GameCase(
        T=6,
        pv=[0.0, 50.0, 300.0, 500.0, 200.0, 0.0],
        wind=[150.0, 120.0, 80.0, 60.0, 100.0, 180.0],
        base_load=[80.0, 80.0, 90.0, 100.0, 120.0, 100.0],
        users=users,
    )


def summarize_case(case: GameCase) -> str:
    """返回算例的可读摘要，便于日志与报告引用。"""
    tot_ev = sum(u.energy_max_kwh for u in case.users)
    tot_re = sum(case.pv) + sum(case.wind)
    tot_load = sum(case.base_load)
    lines = [
        "=" * 56,
        "  SFSE 算例",
        f"  时段数 T = {case.T} h,  EV 用户数 I = {case.I}",
        f"  新能源总量 = {tot_re:.0f} kWh,  基础负荷 = {tot_load:.0f} kWh",
        f"  EV 最大期望充电量 = {tot_ev:.0f} kWh (价格弹性)",
        f"  储能: {case.battery.capacity_kwh:.0f} kWh / "
        f"{case.battery.power_kw:.0f} kW, SOC0={case.battery.soc0:.2f}",
        f"  电价档位 = {case.price_levels} 元/kWh",
        "=" * 56,
    ]
    return "\n".join(lines)


def build_full_case() -> GameCase:
    """构造 legacy 24 时段算例，用于早期交流/平台量级回归检查。

    该函数保留 7500 kWh / 3750 kW 的旧参数，不是当前论文主场景。
    论文实验应通过 DC controller 配置 2000 kWh / 800 kW，并读取
    ``local/论文/inputs`` 中带 manifest 的 Site-A 输入。
    """
    users = [
        EVUser("U1", 1.55, 600.0, tuple(range(0, 7)), 300.0),
        EVUser("U2", 1.45, 500.0, tuple(range(6, 12)), 300.0),
        EVUser("U3", 1.35, 600.0, tuple(range(9, 16)), 400.0),
        EVUser("U4", 1.25, 500.0, tuple(range(12, 19)), 300.0),
        EVUser("U5", 1.15, 700.0, tuple(range(15, 22)), 400.0),
        EVUser("U6", 1.05, 600.0, tuple(range(17, 24)), 300.0),
        EVUser("U7", 1.50, 400.0, tuple(range(7, 14)), 250.0),
        EVUser("U8", 1.30, 500.0, tuple(range(18, 24)), 300.0),
    ]
    pv = [0, 0, 0, 0, 0, 50, 300, 800, 1500, 2200, 2700, 2900,
          2800, 2400, 1800, 1100, 500, 150, 30, 0, 0, 0, 0, 0]
    wind = [900, 950, 1000, 950, 850, 750, 600, 450, 350, 300, 250, 250,
            300, 350, 450, 600, 750, 900, 1000, 1050, 1000, 950, 950, 925]
    base_load = [600, 550, 520, 500, 520, 600, 800, 1000, 1200, 1300, 1250, 1200,
                 1300, 1250, 1200, 1300, 1500, 1700, 1600, 1400, 1200, 1000, 800, 700]
    battery = BatteryParams(
        capacity_kwh=7500.0, power_kw=3750.0,
        soc0=0.50, soc_min=0.10, soc_max=0.90, soc_end_min=0.50,
    )
    return GameCase(
        T=24, pv=[float(v) for v in pv], wind=[float(v) for v in wind],
        base_load=[float(v) for v in base_load], users=users, battery=battery,
    )
