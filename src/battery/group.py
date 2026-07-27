"""
SOH分组状态管理与组迁移逻辑。

按照 soc_health_v3.pdf 的建模思路，将电池按SOH划分为三组（G1/G2/G3），
每组维护聚合状态：电池数量 n_g、平均SOH、平均SOC。
电池SOH衰减后触发组降级迁移；G3降至退役阈值以下触发置换。

当前单体微网场景 N=1，仅一块电池。但模块支持扩展至多电池。
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 组定义 ──────────────────────────────────────────────────
GROUP_BOUNDS = [
    {"name": "G1", "soh_lo": 0.93, "soh_hi": 1.00, "role": "优质资产"},
    {"name": "G2", "soh_lo": 0.82, "soh_hi": 0.93, "role": "常规主力"},
    {"name": "G3", "soh_lo": 0.70, "soh_hi": 0.82, "role": "劣质资产"},
]

SOH_RETIRE = 0.70          # 退役 SOH 阈值
SOH_NEW = 1.00             # 新电池 SOH
NUM_GROUPS = 3


@dataclass
class GroupState:
    """单组聚合状态。"""
    name: str
    soh_lo: float           # 该组 SOH 下限（含）
    soh_hi: float           # 该组 SOH 上限
    n_batteries: int = 0    # 组内电池数量
    soh_avg: float = 0.0    # 组平均 SOH（0~1）
    soc_hourly: List[float] = field(default_factory=list)  # 当日每小时 SOC 记录

    @property
    def soc_avg(self) -> float:
        """当日时间加权平均 SOC (%)。"""
        if not self.soc_hourly:
            return 0.0
        return sum(self.soc_hourly) / len(self.soc_hourly)

    @property
    def is_empty(self) -> bool:
        return self.n_batteries == 0

    def clear_daily(self):
        """清空当日 SOC 记录。"""
        self.soc_hourly.clear()


class BatteryGroupManager:
    """SOH 三组状态管理器。

    维护三组电池的聚合状态，处理 SOH 衰减后的组迁移、
    退役触发与置换逻辑。支持单电池和多电池场景。

    用法::

        mgr = BatteryGroupManager(total_batteries=1, eb_kwh=7500.0)
        # 每小时记录一次 SOC
        mgr.record_soc(group_idx=0, soc_pct=65.0)
        # 每日仿真结束后更新 SOH
        mgr.update_soh(group_idx=0, delta_soh=0.00005)
        # 检查是否需要迁移/退役
        events = mgr.check_migration()
    """

    def __init__(self,
                 total_batteries: int = 1,
                 eb_kwh: float = 7500.0,
                 init_soh: Optional[List[float]] = None):
        """
        Args:
            total_batteries: 电池总数
            eb_kwh: 单块电池标称容量 (kWh)
            init_soh: 各电池初始 SOH 列表，默认全部为 1.0
        """
        self.total_batteries = total_batteries
        self.eb_kwh = eb_kwh

        if init_soh is None:
            init_soh = [SOH_NEW] * total_batteries
        elif len(init_soh) != total_batteries:
            raise ValueError(
                f"init_soh 长度 ({len(init_soh)}) 与 total_batteries ({total_batteries}) 不匹配"
            )

        self.groups: List[GroupState] = []
        for gdef in GROUP_BOUNDS:
            self.groups.append(GroupState(
                name=gdef["name"],
                soh_lo=gdef["soh_lo"],
                soh_hi=gdef["soh_hi"],
            ))

        self._battery_soh: List[float] = list(init_soh)
        self._battery_group: List[int] = [-1] * total_batteries
        self.retirement_count: int = 0
        self.total_replace_cost: float = 0.0

        for b_idx, soh in enumerate(self._battery_soh):
            g_idx = self._assign_group(soh)
            self._battery_group[b_idx] = g_idx
            self._add_to_group(g_idx, soh)

        self._validate_state()

    # ── 组分配 ─────────────────────────────────────────────

    def _assign_group(self, soh: float) -> int:
        """根据 SOH 返回所属组索引 (0=G1, 1=G2, 2=G3)。"""
        for g_idx, g in enumerate(self.groups):
            if g.soh_lo <= soh <= g.soh_hi:
                return g_idx

        if soh > self.groups[0].soh_hi:
            return 0
        if soh < self.groups[-1].soh_lo:
            return 0  # 退役置换后会重新分配，此处先放 G1 占位

        return 0

    # ── 组内状态操作 ──────────────────────────────────────

    def _add_to_group(self, g_idx: int, soh: float):
        """添加一块电池到组，更新聚合 SOH。"""
        g = self.groups[g_idx]
        total_old_cap = g.n_batteries * g.soh_avg
        g.n_batteries += 1
        g.soh_avg = (total_old_cap + soh) / g.n_batteries

    def _remove_from_group(self, g_idx: int):
        """从组中移除一块电池（聚合 SOH 不变，数量减 1）。"""
        g = self.groups[g_idx]
        if g.n_batteries <= 0:
            return
        g.n_batteries -= 1
        if g.n_batteries == 0:
            g.soh_avg = 0.0

    def record_soc(self, group_idx: int, soc_pct: float):
        """记录组内某小时的 SOC 值。

        Args:
            group_idx: 组索引 (0=G1, 1=G2, 2=G3)
            soc_pct: SOC 百分比
        """
        if 0 <= group_idx < NUM_GROUPS:
            self.groups[group_idx].soc_hourly.append(soc_pct)

    def record_hourly_socs(self, soc_values: List[float], group_idx: int = 0):
        """批量记录一组小时的 SOC 值（用于单电池场景）。

        Args:
            soc_values: 每小时 SOC 百分比列表
            group_idx: 组索引
        """
        g = self.groups[group_idx]
        g.soc_hourly.extend(soc_values)

    def clear_daily_records(self):
        """清空所有组的当日 SOC 记录。"""
        for g in self.groups:
            g.clear_daily()

    # ── SOH 更新与迁移 ────────────────────────────────────

    def update_soh(self, group_idx: int, delta_soh: float):
        """更新指定组的 SOH（正值表示退化）。

        Args:
            group_idx: 组索引
            delta_soh: SOH 退化量（正数），如 0.00005 表示 0.005%
        """
        g = self.groups[group_idx]
        if g.is_empty:
            return
        new_soh = max(0.0, g.soh_avg - delta_soh)
        g.soh_avg = new_soh

        for b_idx, g_idx in enumerate(self._battery_group):
            if g_idx == group_idx:
                self._battery_soh[b_idx] = new_soh

    def check_migration(self,
                        c_new: float = 7_500_000,
                        c_salvage_ratio: float = 0.05) -> List[dict]:
        """检查并执行组迁移和退役置换。

        按 G1→G2→G3 顺序检查各组，若组平均 SOH 跌破该组下限则迁移。

        Args:
            c_new: 新电池购置成本 (元)
            c_salvage_ratio: 退役残值回收率

        Returns:
            事件列表，每项格式::
                {"type": "migration"|"retirement", "from": str, "to": str,
                 "n_batteries": int, "cost_yuan": float}
        """
        events = []

        for g_idx in range(NUM_GROUPS):
            g = self.groups[g_idx]
            if g.is_empty or g.n_batteries <= 0:
                continue
            if g.soh_avg >= g.soh_lo:
                continue

            if g_idx < NUM_GROUPS - 1:
                n_migrate = g.n_batteries
                target_g = self.groups[g_idx + 1]
                old_soh = g.soh_avg

                target_g.n_batteries += n_migrate
                if target_g.n_batteries > 0:
                    target_g.soh_avg = (
                        (target_g.soh_avg * (target_g.n_batteries - n_migrate) +
                         old_soh * n_migrate) / target_g.n_batteries
                    )

                g.n_batteries = 0
                g.soh_avg = 0.0

                for b_idx in range(self.total_batteries):
                    if self._battery_group[b_idx] == g_idx:
                        self._battery_group[b_idx] = g_idx + 1
                        self._battery_soh[b_idx] = old_soh

                events.append({
                    "type": "migration",
                    "from": g.name,
                    "to": target_g.name,
                    "n_batteries": n_migrate,
                    "cost_yuan": 0.0,
                    "soh_avg": old_soh,
                })
                logger.info(
                    "组迁移: %s → %s, %d 块电池, SOH=%.4f",
                    g.name, target_g.name, n_migrate, old_soh,
                )
            else:

                n_retire = g.n_batteries
                salvage_per_unit = c_new * c_salvage_ratio
                replace_cost = (c_new - salvage_per_unit) * n_retire

                g.n_batteries = 0
                g.soh_avg = 0.0

                g1_old_n = self.groups[0].n_batteries
                g1_old_soh_sum = g1_old_n * self.groups[0].soh_avg
                self.groups[0].n_batteries += n_retire
                self.groups[0].soh_avg = (
                    (g1_old_soh_sum + n_retire * SOH_NEW)
                    / self.groups[0].n_batteries
                )

                for b_idx in range(self.total_batteries):
                    if self._battery_group[b_idx] == g_idx:
                        self._battery_group[b_idx] = 0
                        self._battery_soh[b_idx] = SOH_NEW

                self.retirement_count += n_retire
                self.total_replace_cost += replace_cost

                events.append({
                    "type": "retirement",
                    "from": g.name,
                    "to": "G1 (new)",
                    "n_batteries": n_retire,
                    "cost_yuan": replace_cost,
                    "soh_avg_old": g.soh_avg,
                    "salvage_per_unit_yuan": salvage_per_unit,
                })
                logger.info(
                    "退役置换: %s → G1, %d 块电池, 净支出=%.1f 万元",
                    g.name, n_retire, replace_cost / 10000,
                )

        self._validate_state()
        return events

    # ── 查询接口 ───────────────────────────────────────────

    def get_active_group_idx(self) -> Optional[int]:
        """返回当前有电池的最高优先级组索引（单电池场景用）。"""
        for g_idx in range(NUM_GROUPS):
            if not self.groups[g_idx].is_empty:
                return g_idx
        return None

    @property
    def soh_system(self) -> float:
        """全系统加权平均 SOH。"""
        total_n = sum(g.n_batteries for g in self.groups)
        if total_n == 0:
            return 0.0
        return sum(g.n_batteries * g.soh_avg for g in self.groups) / total_n

    @property
    def usable_capacity_kwh(self) -> float:
        """全系统当前可用容量 (kWh)。"""
        return sum(
            g.n_batteries * self.eb_kwh * g.soh_avg
            for g in self.groups
        )

    @property
    def total_n_batteries(self) -> int:
        return sum(g.n_batteries for g in self.groups)

    def get_group(self, g_idx: int) -> GroupState:
        return self.groups[g_idx]

    def _validate_state(self):
        """内部一致性校验。"""
        total = sum(g.n_batteries for g in self.groups)
        if total != self.total_batteries:
            logger.error(
                "状态不一致：各组电池数之和 %d ≠ total_batteries %d",
                total, self.total_batteries,
            )

    def summary(self) -> dict:
        """返回全系统状态摘要。"""
        return {
            "soh_sys_pct": round(self.soh_system * 100, 4),
            "usable_capacity_kwh": round(self.usable_capacity_kwh, 2),
            "total_n_batteries": self.total_n_batteries,
            "retirement_count": self.retirement_count,
            "total_replace_cost_yuan": round(self.total_replace_cost, 2),
            "groups": {
                g.name: {
                    "n": g.n_batteries,
                    "soh_avg_pct": round(g.soh_avg * 100, 4),
                    "soc_avg_pct": round(g.soc_avg, 2),
                }
                for g in self.groups
            },
        }
