"""
场景调度器 —— 场景评估与策略流水线执行引擎。

职责:
  1. 根据 DispatchContext 评估所有场景条件，筛选活跃场景
  2. 日级场景每日首次评估后缓存，24h 内复用
  3. 活跃场景按优先级排序，选最高优先级基策略 compute()
  4. 所有命中修正策略按优先级降序 adjust() 形成流水线
  5. 返回最终调度决策 (storage_kW, new_SoC)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.strategy.storage_strategy import StorageStrategy, SimpleBalancing
from src.strategy.conditions import DispatchContext
from src.strategy.scenario_defs import ScenarioDef

logger = logging.getLogger(__name__)


class ScenarioDispatcher:
    """
    场景调度器。

    用法:
        dispatcher = ScenarioDispatcher(scenarios=build_scenarios())
        result_kw, result_soc = dispatcher.dispatch(context)
    """

    def __init__(self, scenarios: List[ScenarioDef],
                 default_strategy: Optional[StorageStrategy] = None):
        """
        Args:
            scenarios: 场景定义列表
            default_strategy: 兜底策略，无任何基策略命中时使用
                              (默认使用 SimpleBalancing)
        """
        self.scenarios = sorted(scenarios, key=lambda s: s.priority, reverse=True)
        self.default = default_strategy or SimpleBalancing()
        self._daily_cache: Dict[str, List[ScenarioDef]] = {}

    def dispatch(self, ctx: DispatchContext):
        """
        执行一次调度决策（单个小时）。

        流程:
          1. 评估场景 → 获取活跃场景列表
          2. 分离基策略 / 修正策略
          3. 最高优先级基策略 → compute()
          4. 所有修正策略按优先级降序 → adjust() 流水线修正
          5. 返回最终 (storage_kW, new_SoC)

        Args:
            ctx: 当前小时的调度上下文

        Returns:
            tuple: (储能功率_kW, 操作后SoC百分比)
        """
        active = self._evaluate_scenarios(ctx)

        bases = [s for s in active if s.strategy_type == "base"]
        corrections = [s for s in active if s.strategy_type == "correction"]
        corrections.sort(key=lambda s: s.priority, reverse=True)

        if bases:
            best_base = bases[0]
            strategy = best_base.strategy
            sc_name = best_base.name
        else:
            strategy = self.default
            sc_name = "default"

        storage_kw, new_soc = strategy.compute(
            pv_kw=ctx.pv_kw,
            wind_kw=ctx.wind_kw,
            load_kw=ctx.load_kw,
            soc_pct=ctx.soc_pct,
            rated_capacity_kwh=ctx.rated_capacity_kwh,
            rated_power_kw=ctx.rated_power_kw,
            time_hour=ctx.hour,
        )

        for sc in corrections:
            prev_kw, prev_soc = storage_kw, new_soc
            storage_kw, new_soc = sc.strategy.adjust(storage_kw, new_soc, ctx)
            if abs(storage_kw - prev_kw) > 0.01:
                logger.debug(
                    "  修正 [%s]: %.1f kW -> %.1f kW",
                    sc.name, prev_kw, storage_kw,
                )

        logger.debug(
            "场景调度: %s | 基策略=%s | 修正=%s | 功率=%.1f kW SoC=%.1f%%",
            ctx.hour if hasattr(ctx, 'hour') else '?',
            sc_name,
            [c.name for c in corrections],
            storage_kw,
            new_soc,
        )

        return storage_kw, new_soc

    def _evaluate_scenarios(self, ctx: DispatchContext) -> List[ScenarioDef]:
        """
        评估所有场景条件，返回活跃场景列表。

        日级场景: 每日首次评估后缓存到 _daily_cache[date_key]，
                  后续同一日期直接从缓存读取。
        小时级场景: 每次调用均重新评估。
        """
        date_key = ctx.date.isoformat()

        if date_key not in self._daily_cache:
            daily_active = []
            for sc in self.scenarios:
                if sc.switch_mode != "daily":
                    continue
                try:
                    if sc.conditions.evaluate(ctx):
                        daily_active.append(sc)
                except Exception:
                    logger.warning(
                        "日级场景 [%s] 条件求值异常，跳过", sc.name,
                        exc_info=True,
                    )
            self._daily_cache[date_key] = daily_active
            logger.info(
                "日级场景已评估 (%s): %s",
                date_key,
                [s.name for s in daily_active],
            )

        active = list(self._daily_cache[date_key])

        for sc in self.scenarios:
            if sc.switch_mode != "hourly":
                continue
            try:
                if sc.conditions.evaluate(ctx):
                    active.append(sc)
            except Exception:
                logger.warning(
                    "小时级场景 [%s] 条件求值异常，跳过", sc.name,
                    exc_info=True,
                )

        active.sort(key=lambda s: s.priority, reverse=True)
        return active
