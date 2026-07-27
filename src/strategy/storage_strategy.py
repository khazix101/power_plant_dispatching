"""
微电网数字孪生储能调度策略。

所有策略实现统一接口：
  - compute(): 基策略入口，根据发电、负荷及当前状态返回调度决策
  - adjust():  修正策略入口，在流水线中对上游决策进行修正

储能功率为正 = 放电，为负 = 充电。
"""

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


def _apply_power_to_soc(kw, soc_start_pct, rated_capacity_kwh,
                        charge_eff=0.95, discharge_eff=0.95,
                        soc_min=10.0, soc_max=90.0):
    """
    根据储能功率计算操作后的新 SoC。

    Args:
        kw: 储能功率 (正=放电, 负=充电)
        soc_start_pct: 起始 SoC (%)
        rated_capacity_kwh: 储能额定容量 (kWh)
        charge_eff: 充电效率
        discharge_eff: 放电效率
        soc_min: SoC 下限
        soc_max: SoC 上限

    Returns:
        float: 操作后 SoC (%)
    """
    if kw < 0:
        delta = abs(kw) * charge_eff / rated_capacity_kwh * 100.0
        return min(soc_start_pct + delta, soc_max)
    elif kw > 0:
        delta = kw / discharge_eff / rated_capacity_kwh * 100.0
        return max(soc_start_pct - delta, soc_min)
    else:
        return soc_start_pct


class StorageStrategy(ABC):
    """储能调度策略抽象基类。

    基策略重写 compute()，修正策略重写 adjust()。
    """

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

    def adjust(self, prev_kw, prev_soc, context):
        """
        修正策略入口。

        在流水线中对上游策略的调度结果进行修正。
        默认直接透传，修正策略应重写此方法。

        Args:
            prev_kw: 上游策略给出的储能功率 (kW)
            prev_soc: 上游策略给出的操作后 SoC (%)
            context: DispatchContext 对象，包含当前小时完整上下文

        Returns:
            tuple: (修正后储能功率_kW, 修正后SoC百分比)
        """
        return prev_kw, prev_soc


# ============================================================
#  基策略 (重写 compute)
# ============================================================

class SimpleBalancing(StorageStrategy):
    """
    简单供需平衡策略（默认兜底策略）。

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


class PeakShaving(StorageStrategy):
    """
    削峰策略。

    在负荷高峰时段主动放电，削减从外部电网的取电峰值。
    设定目标峰值限制 peak_limit_kW，当电网需供功率超过该限制时
    储能放电将外购功率压低至限制线以下。

    - 盈余时段 (发电 > 负荷): 正常充电消纳
    - 缺额时段 (发电 < 负荷): 若缺额超出 peak_limit 则放电削峰
    - 缺额未超峰值限制: 不动作，由电网直接供应
    """

    def __init__(self, peak_limit_kw=6000.0, soc_min=10.0, soc_max=90.0,
                 charge_eff=0.95, discharge_eff=0.95):
        self.peak_limit_kw = peak_limit_kw
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
            return self._discharge_peak(-net_kw, soc_pct, rated_capacity_kwh,
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

    def _discharge_peak(self, deficit_kw, soc_pct, rated_capacity_kwh,
                        rated_power_kw):
        exceed = deficit_kw - self.peak_limit_kw
        if exceed <= 0:
            return 0.0, soc_pct

        discharge_power = min(exceed, rated_power_kw)
        avail_energy = (soc_pct - self.soc_min) / 100.0 * rated_capacity_kwh
        max_discharge_power = avail_energy * self.discharge_eff
        if discharge_power > max_discharge_power:
            discharge_power = float(max_discharge_power)
        delta_soc = -(discharge_power / self.discharge_eff) / rated_capacity_kwh * 100.0
        new_soc = max(soc_pct + delta_soc, self.soc_min)
        return discharge_power, new_soc


class ValleyFilling(StorageStrategy):
    """
    填谷策略。

    在负荷低谷时段优先充电吸收新能源余量，不进行放电。
    将储能能量保留给后续的峰时段使用。

    - 盈余时段 (发电 > 负荷): 充电消纳
    - 缺额时段 (发电 < 负荷): 不放电，由电网直供
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

        if net_kw > 0.1:
            charge_power = min(net_kw, rated_power_kw)
            max_charge_energy = (self.soc_max - soc_pct) / 100.0 * rated_capacity_kwh
            if charge_power > max_charge_energy:
                charge_power = float(max_charge_energy)
            delta_soc = charge_power * self.charge_eff / rated_capacity_kwh * 100.0
            new_soc = min(soc_pct + delta_soc, self.soc_max)
            return -charge_power, new_soc
        else:
            return 0.0, soc_pct


class RenewableAbsorb(StorageStrategy):
    """
    新能源消纳策略。

    当新能源大发 (光伏+风电远超负荷) 时，最大化储能充电功率
    以消纳尽可能多的可再生能源，SoC 上限放宽至 98%。

    - 盈余时段: 充电至接近满电
    - 缺额时段: 不放电，新能源不足不从储能取电
    """

    def __init__(self, charge_eff=0.95):
        self.charge_eff = charge_eff
        self.soc_max = 98.0

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        net_kw = pv_kw + wind_kw - load_kw

        if net_kw > 0.1:
            charge_power = min(net_kw, rated_power_kw)
            max_charge_energy = (self.soc_max - soc_pct) / 100.0 * rated_capacity_kwh
            if charge_power > max_charge_energy:
                charge_power = float(max_charge_energy)
            delta_soc = charge_power * self.charge_eff / rated_capacity_kwh * 100.0
            new_soc = min(soc_pct + delta_soc, self.soc_max)
            return -charge_power, new_soc
        else:
            return 0.0, soc_pct


class SummerPeakStrategy(StorageStrategy):
    """
    夏季调度策略。

    夏季光伏出力大、降温负荷高，需要更激进的削峰调度。
    - 白天 (6-18 点): 按削峰策略运行，主动放电削减外购峰值
    - 夜间 (18-6 点): 按简单供需平衡运行，维持基础平衡
    """

    def __init__(self, peak_limit_kw=5000.0, soc_min=10.0, soc_max=90.0,
                 charge_eff=0.95, discharge_eff=0.95):
        self.peak_limit_kw = peak_limit_kw
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.charge_eff = charge_eff
        self.discharge_eff = discharge_eff

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        net_kw = pv_kw + wind_kw - load_kw

        if 6 <= time_hour <= 18:
            return self._daytime(net_kw, soc_pct, rated_capacity_kwh,
                                 rated_power_kw)
        else:
            return self._nighttime(net_kw, soc_pct, rated_capacity_kwh,
                                   rated_power_kw)

    def _daytime(self, net_kw, soc_pct, rated_capacity_kwh, rated_power_kw):
        if abs(net_kw) < 0.1:
            return 0.0, soc_pct

        if net_kw > 0:
            charge_power = min(net_kw, rated_power_kw)
            max_charge_energy = (self.soc_max - soc_pct) / 100.0 * rated_capacity_kwh
            if charge_power > max_charge_energy:
                charge_power = float(max_charge_energy)
            delta_soc = charge_power * self.charge_eff / rated_capacity_kwh * 100.0
            new_soc = min(soc_pct + delta_soc, self.soc_max)
            return -charge_power, new_soc
        else:
            deficit_kw = -net_kw
            exceed = deficit_kw - self.peak_limit_kw
            if exceed <= 0:
                return 0.0, soc_pct
            discharge_power = min(exceed, rated_power_kw)
            avail_energy = (soc_pct - self.soc_min) / 100.0 * rated_capacity_kwh
            max_discharge_power = avail_energy * self.discharge_eff
            if discharge_power > max_discharge_power:
                discharge_power = float(max_discharge_power)
            delta_soc = -(discharge_power / self.discharge_eff) / rated_capacity_kwh * 100.0
            new_soc = max(soc_pct + delta_soc, self.soc_min)
            return discharge_power, new_soc

    def _nighttime(self, net_kw, soc_pct, rated_capacity_kwh, rated_power_kw):
        if abs(net_kw) < 0.1:
            return 0.0, soc_pct

        if net_kw > 0:
            charge_power = min(net_kw, rated_power_kw)
            max_charge_energy = (self.soc_max - soc_pct) / 100.0 * rated_capacity_kwh
            if charge_power > max_charge_energy:
                charge_power = float(max_charge_energy)
            delta_soc = charge_power * self.charge_eff / rated_capacity_kwh * 100.0
            new_soc = min(soc_pct + delta_soc, self.soc_max)
            return -charge_power, new_soc
        else:
            deficit_kw = -net_kw
            discharge_power = min(deficit_kw, rated_power_kw)
            avail_energy = (soc_pct - self.soc_min) / 100.0 * rated_capacity_kwh
            max_discharge_power = avail_energy * self.discharge_eff
            if discharge_power > max_discharge_power:
                discharge_power = float(max_discharge_power)
            delta_soc = -(discharge_power / self.discharge_eff) / rated_capacity_kwh * 100.0
            new_soc = max(soc_pct + delta_soc, self.soc_min)
            return discharge_power, new_soc


class HolidayStrategy(StorageStrategy):
    """
    节假日调度策略。

    节假日充电负荷降低、调度压力小，采用宽幅平衡策略。
    - SoC 范围放宽至 5%~95%，允许更大浮动的自然平衡
    - 充放电功率上限降低至变流器额定的 80%，减少频繁动作
    """

    def __init__(self, soc_min=5.0, soc_max=95.0,
                 charge_eff=0.95, discharge_eff=0.95,
                 power_ratio=0.8):
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.charge_eff = charge_eff
        self.discharge_eff = discharge_eff
        self.power_ratio = power_ratio

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        net_kw = pv_kw + wind_kw - load_kw
        capped_power = rated_power_kw * self.power_ratio

        if abs(net_kw) < 0.1:
            return 0.0, soc_pct

        if net_kw > 0:
            charge_power = min(net_kw, capped_power)
            max_charge_energy = (self.soc_max - soc_pct) / 100.0 * rated_capacity_kwh
            if charge_power > max_charge_energy:
                charge_power = float(max_charge_energy)
            delta_soc = charge_power * self.charge_eff / rated_capacity_kwh * 100.0
            new_soc = min(soc_pct + delta_soc, self.soc_max)
            return -charge_power, new_soc
        else:
            deficit_kw = -net_kw
            discharge_power = min(deficit_kw, capped_power)
            avail_energy = (soc_pct - self.soc_min) / 100.0 * rated_capacity_kwh
            max_discharge_power = avail_energy * self.discharge_eff
            if discharge_power > max_discharge_power:
                discharge_power = float(max_discharge_power)
            delta_soc = -(discharge_power / self.discharge_eff) / rated_capacity_kwh * 100.0
            new_soc = max(soc_pct + delta_soc, self.soc_min)
            return discharge_power, new_soc


# ============================================================
#  修正策略 (重写 adjust)
# ============================================================

class SoCSafety(StorageStrategy):
    """
    SoC 安全保护策略（修正策略）。

    根据 SoC 水位对上游调度结果进行限制：
    - critical_low (SoC < 10%):  强制禁止放电 (kw > 0 → 0)
    - critical_high (SoC > 95%): 强制禁止充电 (kw < 0 → 0)
    - soft_low (SoC 10%~20%):    放电功率上限降至 50% 额定
    - soft_high (SoC 80%~95%):  充电功率上限降至 50% 额定
    """

    def __init__(self, block_charge=False, block_discharge=False,
                 limit_charge_ratio=None, limit_discharge_ratio=None):
        self.block_charge = block_charge
        self.block_discharge = block_discharge
        self.limit_charge_ratio = limit_charge_ratio
        self.limit_discharge_ratio = limit_discharge_ratio

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        return 0.0, soc_pct

    def adjust(self, prev_kw, prev_soc, context):
        new_kw = prev_kw

        if self.block_discharge and prev_kw > 0:
            new_kw = 0.0
        elif self.block_charge and prev_kw < 0:
            new_kw = 0.0
        elif self.limit_discharge_ratio is not None and prev_kw > 0:
            limit = context.rated_power_kw * self.limit_discharge_ratio
            new_kw = min(prev_kw, limit)
        elif self.limit_charge_ratio is not None and prev_kw < 0:
            limit = -context.rated_power_kw * self.limit_charge_ratio
            new_kw = max(prev_kw, limit)

        if abs(new_kw - prev_kw) < 0.01:
            return prev_kw, prev_soc

        new_soc = _apply_power_to_soc(
            new_kw, context.soc_pct, context.rated_capacity_kwh,
            charge_eff=getattr(context, 'charge_eff', 0.95),
            discharge_eff=getattr(context, 'discharge_eff', 0.95),
            soc_min=getattr(context, 'soc_min_hard', 10.0),
            soc_max=getattr(context, 'soc_max_hard', 90.0),
        )
        return new_kw, new_soc


class DeficitControl(StorageStrategy):
    """
    新能源不足控制策略（修正策略）。

    当新能源出力严重不足 (PV+Wind < Load×0.3) 时，
    限制储能放电功率，保留底仓应对后续可能出现的更大缺额。
    """

    def __init__(self, limit_discharge_ratio=0.5):
        self.limit_discharge_ratio = limit_discharge_ratio

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        return 0.0, soc_pct

    def adjust(self, prev_kw, prev_soc, context):
        if prev_kw <= 0:
            return prev_kw, prev_soc

        limit = context.rated_power_kw * self.limit_discharge_ratio
        new_kw = min(prev_kw, limit)

        if abs(new_kw - prev_kw) < 0.01:
            return prev_kw, prev_soc

        new_soc = _apply_power_to_soc(
            new_kw, context.soc_pct, context.rated_capacity_kwh,
            soc_min=getattr(context, 'soc_min_hard', 10.0),
            soc_max=getattr(context, 'soc_max_hard', 90.0),
        )
        return new_kw, new_soc


class ConservativeStrategy(StorageStrategy):
    """
    冬季保守策略（修正策略）。

    冬季供热负荷大、光伏出力低，需要保留更多储能裕度应对突发状况。
    - 充放电功率上限降至变流器额定的 70%
    - SoC 下限抬高至 40%，禁止深度放电
    """

    def __init__(self, power_limit_ratio=0.7, soc_floor_pct=40.0):
        self.power_limit_ratio = power_limit_ratio
        self.soc_floor_pct = soc_floor_pct

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        return 0.0, soc_pct

    def adjust(self, prev_kw, prev_soc, context):
        new_kw = prev_kw
        limit = context.rated_power_kw * self.power_limit_ratio

        if prev_kw > limit:
            new_kw = limit
        elif prev_kw < -limit:
            new_kw = -limit

        if abs(new_kw - prev_kw) < 0.01:
            return prev_kw, prev_soc

        new_soc = _apply_power_to_soc(
            new_kw, context.soc_pct, context.rated_capacity_kwh,
            soc_min=self.soc_floor_pct,
            soc_max=getattr(context, 'soc_max_hard', 90.0),
        )
        return new_kw, new_soc


class StormStrategy(StorageStrategy):
    """
    极端天气策略（修正策略）。

    当天气类型为 storm（暴风雨/台风等极端天气）时，
    储能系统退出运行，功率强制归零，保障设备安全。
    """

    def compute(self, pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw, time_hour):
        return 0.0, soc_pct

    def adjust(self, prev_kw, prev_soc, context):
        return 0.0, context.soc_pct


# ============================================================
#  策略注册表 & 工厂
# ============================================================

STRATEGY_REGISTRY = {
    "simple_balancing": SimpleBalancing,
    "peak_shaving": PeakShaving,
    "valley_filling": ValleyFilling,
    "renewable_absorb": RenewableAbsorb,
    "summer_peak": SummerPeakStrategy,
    "holiday": HolidayStrategy,
    "soc_safety": SoCSafety,
    "deficit_control": DeficitControl,
    "conservative": ConservativeStrategy,
    "storm": StormStrategy,
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
