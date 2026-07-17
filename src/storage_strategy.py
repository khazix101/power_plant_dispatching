"""
微电网数字孪生储能调度策略。

所有策略实现统一接口: compute() 根据发电、负荷及当前状态
返回 (期望储能功率_kW, 新SoC百分比)。
储能功率为正 = 放电, 为负 = 充电。
"""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class StorageStrategy(ABC):
    """储能调度策略抽象基类。"""

    @abstractmethod
    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        """
        计算单个时间步长的储能调度。

        Args:
            pv_kw: 光伏发电功率 (kW)
            wind_kw: 风电发电功率 (kW)
            load_kw: 负荷消耗功率 (kW)
            soc_pct: 当前荷电状态 (%)
            rated_capacity_kwh: 储能额定容量 (kWh)
            rated_power_kw: 储能变流器额定功率 (kW)
            time_hour: 当前小时 (0-23)

        Returns:
            tuple: (储能功率_kW, 新SoC百分比)
                储能功率 > 0: 放电, < 0: 充电
        """
        pass


class SimpleBalancing(StorageStrategy):
    """
    简单供需平衡策略。

    - 当发电 (光伏 + 风电) > 负荷时: 用多余电力充电储能
    - 当发电 < 负荷时: 储能放电以弥补缺口
    - 遵循 SoC 上下限限制 (soc_min / soc_max) 及变流器功率限制
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
    创建储能策略实例的工厂函数。

    Args:
        name: 策略名称 (STRATEGY_REGISTRY 中的键)
        **kwargs: 传递给策略构造函数的参数

    Returns:
        StorageStrategy 实例
    """
    if name not in STRATEGY_REGISTRY:
        available = list(STRATEGY_REGISTRY.keys())
        raise ValueError(
            f"未知策略 '{name}'。可用策略: {available}"
        )
    return STRATEGY_REGISTRY[name](**kwargs)
