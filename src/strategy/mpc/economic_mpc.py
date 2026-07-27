"""
经济最优 MPC 调度策略 —— 网格搜索 + SimpleBalancing 向前模拟。

在每个决策时步，枚举 13 档候选充放电功率，用 SimpleBalancing 模拟
剩余小时的 SOC 轨迹，计算剩余小时的电池老化经济成本与购售电收支，
选出累计净利润最高的候选功率。

策略注册名: mpc_grid13_sb

构造参数:
    forecast_pv:   list[float]  24h 光伏预测 (kW)
    forecast_wind: list[float]  24h 风电预测 (kW)
    forecast_load: list[float]  24h 负荷预测 (kW)
    group_mgr:     BatteryGroupManager  读取当前 SOH 用于老化成本
    eb_kwh:        float        单块电池标称容量 (kWh)
    c_new:         float        新电池成本 (元)
    gamma:         float        循环退化系数 (1/kWh)
    alpha:         float        DOD 非线性指数

原理:
    soc_health_v3.pdf 的全系统经济评价公式嵌入 MPC 目标函数:
        score = R_sell − C_buy − C_battery (剩余小时)
    其中 C_battery = C_cal + C_cyc，按小时累加。
"""

import logging
import math
from typing import List, Tuple

from src.strategy.storage_strategy import StorageStrategy

logger = logging.getLogger(__name__)

# ── 默认参数 ────────────────────────────────────────────
C_NEW_DEFAULT = 7_500_000.0
GAMMA_DEFAULT = 1.5e-8
ALPHA_DEFAULT = 1.5

# Vsource 不平衡惩罚系数（孤岛模式，非实际购售电价）
# Vsource > 0: 功率缺额，从电压参考节点吸收 → 惩罚（等价于被迫外购）
# Vsource < 0: 功率盈余，向电压参考节点反送 → 抵免（等价于余电外送，但价值低于缺额惩罚）
PENALTY_DEFICIT = 0.8   # 缺额惩罚 (元/kWh)，激励优先用储能填补缺口
CREDIT_SURPLUS = 0.4    # 盈余抵免 (元/kWh)，鼓励消纳而非反送

SOH_RETIRE = 0.70
SOH_NEW = 1.00
RESID_DERIV = 1.0 / (SOH_NEW - SOH_RETIRE)   # ≈ 3.333

# SOC 区间 → 年老化率分段表 (soc_health_v3 表2)
KCAL_BINS = [
    (0.10, 0.50, 0.010),
    (0.50, 0.80, 0.015),
    (0.80, 0.90, 0.025),
    (0.90, 1.00, 0.040),
]

# 候选功率 13 档，均匀分布 [-3750, 3750]
NUM_CANDIDATES = 13


def _k_cal(soc_pct: float) -> float:
    """SOC 依赖的年日历老化率（分段查表）。"""
    s = max(0.10, min(1.00, soc_pct / 100.0))
    for lo, hi, rate in KCAL_BINS:
        if lo <= s <= hi:
            return rate
    return KCAL_BINS[-1][2]


def _candidates(rated_power_kw: float) -> List[float]:
    """生成 13 档均匀分布候选功率。"""
    step = (2.0 * rated_power_kw) / (NUM_CANDIDATES - 1)
    return [round(-rated_power_kw + i * step, 1) for i in range(NUM_CANDIDATES)]


def _apply_power(kw: float, soc_pct: float,
                 rated_capacity_kwh: float, charge_eff: float = 0.95,
                 discharge_eff: float = 0.95, soc_min: float = 10.0,
                 soc_max: float = 90.0) -> Tuple[float, float]:
    """施加储能功率，返回 (有效功率_kW, 新SoC_pct)。

    若 SOC 限幅导致功率被截断，有效功率可能小于请求功率。
    """
    if kw < 0:
        charge_kw = abs(kw)
        delta = charge_kw * charge_eff / rated_capacity_kwh * 100.0
        if soc_pct + delta > soc_max:
            delta = soc_max - soc_pct
            charge_kw = delta * rated_capacity_kwh / 100.0 / charge_eff
        return -charge_kw, soc_pct + delta
    elif kw > 0:
        discharge_kw = kw
        delta = discharge_kw / discharge_eff / rated_capacity_kwh * 100.0
        if soc_pct - delta < soc_min:
            delta = soc_pct - soc_min
            discharge_kw = delta * discharge_eff * rated_capacity_kwh / 100.0
        return discharge_kw, soc_pct - delta
    else:
        return 0.0, soc_pct


def _simulate_simple_balancing(
    net_kw: float,
    soc_pct: float,
    rated_capacity_kwh: float,
    rated_power_kw: float,
) -> Tuple[float, float]:
    """SimpleBalancing 单步模拟: (storage_kW, new_soc_pct)。"""
    if abs(net_kw) < 0.1:
        return 0.0, soc_pct

    if net_kw > 0:
        charge_power = min(net_kw, rated_power_kw)
        kw, new_soc = _apply_power(-charge_power, soc_pct, rated_capacity_kwh)
        return kw, new_soc
    else:
        discharge_power = min(-net_kw, rated_power_kw)
        kw, new_soc = _apply_power(discharge_power, soc_pct, rated_capacity_kwh)
        return kw, new_soc


class EconomicMPCStrategy(StorageStrategy):
    """经济最优 MPC 策略: 网格搜索 13 档 + SimpleBalancing 向前模拟。

    compute() 枚举 13 档候选充放电功率，对每个候选:
      本步用候选功率 → 向前用 SimpleBalancing 模拟到 23h
      → 累加剩余小时的日历老化 + 循环老化 + 购售电收支
      → 返回净利润最高的候选功率。

    策略注册名: mpc_grid13_sb
    """

    def __init__(
        self,
        forecast_pv: List[float],
        forecast_wind: List[float],
        forecast_load: List[float],
        group_mgr=None,
        eb_kwh: float = 7500.0,
        c_new: float = C_NEW_DEFAULT,
        gamma: float = GAMMA_DEFAULT,
        alpha: float = ALPHA_DEFAULT,
    ):
        self.pv = list(forecast_pv)
        self.wind = list(forecast_wind)
        self.load = list(forecast_load)
        self.group_mgr = group_mgr
        self.eb_kwh = eb_kwh
        self.c_new = c_new
        self.gamma = gamma
        self.alpha = alpha

    @property
    def _current_soh(self) -> float:
        if self.group_mgr is not None:
            return self.group_mgr.soh_system
        return 1.0

    # ── StorageStrategy 接口 ──────────────────────────────

    def compute(
        self,
        pv_kw: float,
        wind_kw: float,
        load_kw: float,
        soc_pct: float,
        rated_capacity_kwh: float,
        rated_power_kw: float,
        time_hour: int,
    ) -> Tuple[float, float]:
        """单步 MPC 决策入口。

        Returns:
            (storage_kW, new_soc_pct): 充放电功率 (正=放电) 及新 SOC
        """
        h = time_hour
        if h >= 23:
            return self._terminal_step(
                pv_kw, wind_kw, load_kw, soc_pct,
                rated_capacity_kwh, rated_power_kw,
            )

        cands = _candidates(rated_power_kw)
        best_kw = 0.0
        best_soc = soc_pct
        best_score = float("-inf")

        for cand in cands:
            kw, new_soc = _apply_power(
                cand, soc_pct, rated_capacity_kwh,
            )
            score = self._evaluate_candidate(
                h, kw, new_soc, rated_capacity_kwh, rated_power_kw,
            )
            if score > best_score:
                best_score = score
                best_kw = kw
                best_soc = new_soc

        logger.debug(
            "MPC h=%02d | soc=%.1f | best=%.1f kW score=%.2f",
            h, soc_pct, best_kw, best_score,
        )
        return best_kw, best_soc

    def _terminal_step(
        self, pv_kw, wind_kw, load_kw, soc_pct,
        rated_capacity_kwh, rated_power_kw,
    ) -> Tuple[float, float]:
        """末小时 (23h) 不用模拟未来，直接用 SimpleBalancing。"""
        cands = _candidates(rated_power_kw)
        best_kw = 0.0
        best_soc = soc_pct
        best_score = float("-inf")

        for cand in cands:
            kw, new_soc = _apply_power(
                cand, soc_pct, rated_capacity_kwh,
            )
            score = self._score_single_step(
                cand, kw, new_soc, pv_kw, wind_kw, load_kw,
            )
            if score > best_score:
                best_score = score
                best_kw = kw
                best_soc = new_soc

        return best_kw, best_soc

    # ── 候选评估 ──────────────────────────────────────────

    def _evaluate_candidate(
        self,
        hour: int,
        cand_kw: float,
        cand_new_soc: float,
        rated_capacity_kwh: float,
        rated_power_kw: float,
    ) -> float:
        """评估一个候选功率的剩余日净利润得分。"""
        h = hour

        soc_traj = [cand_new_soc]
        storage_traj = [cand_kw]

        # 当前小时 (h) 的不平衡量
        source_h = (self.load[h] - self.pv[h] - self.wind[h]
                    - cand_kw)
        deficit_kwh = max(0.0, source_h)
        surplus_kwh = max(0.0, -source_h)

        # 未来小时 (h+1 ~ 23) 用 SimpleBalancing 模拟
        cur_soc = cand_new_soc
        for t in range(h + 1, 24):
            net = self.pv[t] + self.wind[t] - self.load[t]
            store_kw, cur_soc = _simulate_simple_balancing(
                net, cur_soc, rated_capacity_kwh, rated_power_kw,
            )
            storage_traj.append(store_kw)
            soc_traj.append(cur_soc)

            source = (self.load[t] - self.pv[t] - self.wind[t]
                      - store_kw)
            if source > 0:
                deficit_kwh += source
            elif source < 0:
                surplus_kwh += abs(source)

        cal_cost = self._calc_calendar_cost(soc_traj)
        cyc_cost = self._calc_cycle_cost(storage_traj, soc_traj)

        penalty = deficit_kwh * PENALTY_DEFICIT
        credit = surplus_kwh * CREDIT_SURPLUS

        return credit - penalty - cal_cost - cyc_cost

    def _score_single_step(
        self, cand_cmd, actual_kw, actual_soc,
        pv_kw, wind_kw, load_kw,
    ) -> float:
        """末小时单步评分（无未来小时模拟）。"""
        source = load_kw - pv_kw - wind_kw - actual_kw
        penalty = max(0.0, source) * PENALTY_DEFICIT
        credit = max(0.0, -source) * CREDIT_SURPLUS

        cal = _k_cal(actual_soc) / 365.0 / 24.0
        cal_cost = 1.0 * self.c_new * cal * RESID_DERIV

        throughput = abs(actual_kw)
        dod = max(0.01, abs(actual_soc - actual_soc) / 100.0 or 0.01)
        cyc = self.gamma * throughput * (dod ** (self.alpha - 1))
        cyc_cost = 1.0 * self.c_new * cyc * RESID_DERIV

        return credit - penalty - cal_cost - cyc_cost

    # ── 老化成本计算 ──────────────────────────────────────

    def _calc_calendar_cost(self, soc_traj: List[float]) -> float:
        """基于剩余小时 SOC 轨迹计算日历老化成本。"""
        if not soc_traj:
            return 0.0
        n = 1
        deriv = RESID_DERIV
        total = 0.0
        for s in soc_traj:
            k = _k_cal(s)                    # 年老化率
            delta = k / 365.0 / 24.0          # 每小时衰减
            total += n * self.c_new * delta * deriv
        return total

    def _calc_cycle_cost(self, storage_traj: List[float],
                         soc_traj: List[float]) -> float:
        """基于剩余小时储能功率和 SOC 轨迹计算循环老化成本。"""
        if not storage_traj:
            return 0.0
        n = 1
        deriv = RESID_DERIV
        throughput = sum(abs(kw) for kw in storage_traj)

        soc_max = max(soc_traj)
        soc_min = min(soc_traj)
        dod = max(0.01, (soc_max - soc_min) / 100.0)
        dod = min(1.0, dod)

        delta_soh = self.gamma * throughput * (dod ** (self.alpha - 1))
        return n * self.c_new * delta_soh * deriv
