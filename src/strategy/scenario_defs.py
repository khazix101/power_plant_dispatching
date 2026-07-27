"""
场景定义工厂。

将所有场景配置集中管理，每个场景包含：
  - 命中条件 (Condition 组合)
  - 调度策略实例
  - 优先级 / 切换模式 / 策略类型

场景清单说明详见设计方案文档。
"""

from dataclasses import dataclass
from typing import List

from src.strategy.storage_strategy import (
    StorageStrategy,
    SimpleBalancing,
    PeakShaving,
    ValleyFilling,
    RenewableAbsorb,
    SummerPeakStrategy,
    HolidayStrategy,
    SoCSafety,
    DeficitControl,
    ConservativeStrategy,
    StormStrategy,
)
from src.strategy.conditions import (
    Condition,
    hour_in_range,
    month_in_range,
    load_above,
    load_below,
    soc_between,
    soc_below,
    soc_above,
    weather_equals,
    renewable_ratio_above,
    renewable_ratio_below,
    net_positive,
    is_holiday,
    always,
)


@dataclass
class ScenarioDef:
    """单个场景的完整定义。"""

    name: str
    description: str
    conditions: Condition
    strategy: StorageStrategy
    priority: int
    switch_mode: str       # "daily" | "hourly"
    strategy_type: str     # "base" | "correction"


def build_scenarios() -> List[ScenarioDef]:
    """
    构建全部场景定义。

    返回的场景列表按以下分类组织：

    日级场景 (switch_mode="daily") —— 每日零点评估一次，24h 缓存：
      - holiday:            节假日宽幅平衡
      - season_summer:      夏季激进削峰
      - season_winter:      冬季保守限制
      - transition_season:  春秋过渡季标准平衡 (兜底)

    小时级场景 (switch_mode="hourly") —— 每小时重新评估：
      - storm_hour:         极端天气储能退出       (修正, pri=200)
      - soc_critical_low:   SoC < 10% 强制禁止放电   (修正, pri=100)
      - soc_critical_high:  SoC > 95% 强制禁止充电  (修正, pri=99)
      - soc_low:            SoC 10-20% 软限放电     (修正, pri=80)
      - soc_high:           SoC 80-95% 软限充电     (修正, pri=79)
      - peak_load:          负荷 > 10000kW 削峰      (基, pri=70)
      - high_renewable:     新能源 > 负荷×1.2 消纳  (基, pri=65)
      - low_renewable:      新能源 < 负荷×0.3 限制  (修正, pri=60)
      - valley_load:        负荷 < 3600kW 且盈余 填谷 (基, pri=50)
      - peak_hours:         峰时段 8-11/18-22 削峰   (基, pri=45)
      - valley_hours:       谷时段 23-7 填谷          (基, pri=40)
    """
    scenarios = []

    # -------------------------------------------------------
    #  日级场景
    # -------------------------------------------------------

    # D1: 节假日 —— 降低调度激进程度
    scenarios.append(ScenarioDef(
        name="holiday",
        description="节假日: 宽幅平衡，减少储能频繁动作",
        conditions=is_holiday(),
        strategy=HolidayStrategy(soc_min=5.0, soc_max=95.0, power_ratio=0.8),
        priority=30,
        switch_mode="daily",
        strategy_type="base",
    ))

    # D2: 夏季 (6-8月) —— 激进削峰，光伏时段优先放电
    scenarios.append(ScenarioDef(
        name="season_summer",
        description="夏季 (6-8月): 白天光伏时段削峰放电，夜间标准平衡",
        conditions=month_in_range(6, 8),
        strategy=SummerPeakStrategy(
            peak_limit_kw=5000.0, soc_min=10.0, soc_max=90.0,
        ),
        priority=15,
        switch_mode="daily",
        strategy_type="base",
    ))

    # D3: 冬季 (12-2月) —— 保守调度，抬高 SoC 下限限功率
    #     注意: 跨年 12月-2月 用 month_in_range(12, 2)
    scenarios.append(ScenarioDef(
        name="season_winter",
        description="冬季 (12-2月): 保守调度，SoC 下限 40%，限功率 70%",
        conditions=month_in_range(12, 2),
        strategy=ConservativeStrategy(power_limit_ratio=0.7, soc_floor_pct=40.0),
        priority=16,
        switch_mode="daily",
        strategy_type="correction",
    ))

    # D4: 过渡季 (3-5月, 9-11月) —— 标准供需平衡兜底
    #     条件: NOT (夏季 OR 冬季 OR 节假日)，在所有日级场景中优先级最低
    trans_cond = ~month_in_range(6, 8) & ~month_in_range(12, 2) & ~is_holiday()
    scenarios.append(ScenarioDef(
        name="transition_season",
        description="过渡季 (3-5月, 9-11月): 标准供需平衡",
        conditions=trans_cond,
        strategy=SimpleBalancing(soc_min=10.0, soc_max=90.0),
        priority=0,
        switch_mode="daily",
        strategy_type="base",
    ))

    # -------------------------------------------------------
    #  小时级场景 —— 修正策略 (pri 最高, 优先执行)
    # -------------------------------------------------------

    # H1: 极端天气 —— 储能退出 (最高优先级)
    scenarios.append(ScenarioDef(
        name="storm_hour",
        description="极端天气 (storm): 储能退出运行，功率归零",
        conditions=weather_equals("storm"),
        strategy=StormStrategy(),
        priority=200,
        switch_mode="hourly",
        strategy_type="correction",
    ))

    # H2: SoC 临界低位 (SoC < 10%) —— 强制禁止放电
    scenarios.append(ScenarioDef(
        name="soc_critical_low",
        description="SoC < 10%: 强制禁止放电",
        conditions=soc_below(10.0),
        strategy=SoCSafety(block_discharge=True),
        priority=100,
        switch_mode="hourly",
        strategy_type="correction",
    ))

    # H3: SoC 临界高位 (SoC > 95%) —— 强制禁止充电
    scenarios.append(ScenarioDef(
        name="soc_critical_high",
        description="SoC > 95%: 强制禁止充电",
        conditions=soc_above(95.0),
        strategy=SoCSafety(block_charge=True),
        priority=99,
        switch_mode="hourly",
        strategy_type="correction",
    ))

    # H4: SoC 低位 (10% ≤ SoC < 20%) —— 放电功率限制 50%
    scenarios.append(ScenarioDef(
        name="soc_low",
        description="SoC 10%-20%: 放电功率限制至 50% 额定",
        conditions=soc_between(10.0, 20.0),
        strategy=SoCSafety(limit_discharge_ratio=0.5),
        priority=80,
        switch_mode="hourly",
        strategy_type="correction",
    ))

    # H5: SoC 高位 (80% < SoC ≤ 95%) —— 充电功率限制 50%
    scenarios.append(ScenarioDef(
        name="soc_high",
        description="SoC 80%-95%: 充电功率限制至 50% 额定",
        conditions=soc_between(80.0, 95.0),
        strategy=SoCSafety(limit_charge_ratio=0.5),
        priority=79,
        switch_mode="hourly",
        strategy_type="correction",
    ))

    # H6: 新能源不足修正 —— 放电限 50% (由 DeficitControl 处理)
    scenarios.append(ScenarioDef(
        name="low_renewable",
        description="新能源不足 (PV+Wind < Load×0.3): 限制放电，保留底仓",
        conditions=renewable_ratio_below(0.3),
        strategy=DeficitControl(limit_discharge_ratio=0.5),
        priority=60,
        switch_mode="hourly",
        strategy_type="correction",
    ))

    # -------------------------------------------------------
    #  小时级场景 —— 基策略
    # -------------------------------------------------------

    # H7: 负荷峰值 —— 削峰
    scenarios.append(ScenarioDef(
        name="peak_load",
        description="负荷 > 10000kW: 储能放电削峰",
        conditions=load_above(10000.0),
        strategy=PeakShaving(peak_limit_kw=6000.0, soc_min=10.0, soc_max=90.0),
        priority=70,
        switch_mode="hourly",
        strategy_type="base",
    ))

    # H8: 新能源大发 —— 最大化消纳
    scenarios.append(ScenarioDef(
        name="high_renewable",
        description="新能源大发 (PV+Wind > Load×1.2): 最大化充电消纳",
        conditions=renewable_ratio_above(1.2),
        strategy=RenewableAbsorb(),
        priority=65,
        switch_mode="hourly",
        strategy_type="base",
    ))

    # H9: 负荷低谷 + 盈余 —— 填谷充电
    scenarios.append(ScenarioDef(
        name="valley_load",
        description="负荷 < 3600kW 且新能源盈余: 填谷充电",
        conditions=load_below(3600.0) & net_positive(),
        strategy=ValleyFilling(soc_min=10.0, soc_max=90.0),
        priority=50,
        switch_mode="hourly",
        strategy_type="base",
    ))

    # H10: 峰时段 (8-11, 18-22) —— 削峰
    scenarios.append(ScenarioDef(
        name="peak_hours",
        description="峰时段 8-11 / 18-22: 削峰放电",
        conditions=hour_in_range(8, 11) | hour_in_range(18, 22),
        strategy=PeakShaving(peak_limit_kw=6000.0, soc_min=10.0, soc_max=90.0),
        priority=45,
        switch_mode="hourly",
        strategy_type="base",
    ))

    # H11: 谷时段 (23-7) —— 填谷
    scenarios.append(ScenarioDef(
        name="valley_hours",
        description="谷时段 23-7: 填谷充电",
        conditions=hour_in_range(23, 7),
        strategy=ValleyFilling(soc_min=10.0, soc_max=90.0),
        priority=40,
        switch_mode="hourly",
        strategy_type="base",
    ))

    return scenarios
