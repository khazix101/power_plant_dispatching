# Agent C：阶段 2 协调器首时段输出

## 工作区与分支

- 目录：`D:\YWHC\power_plant_dispatching\workspaces\C`
- 分支：`team/c-multisite`
- 只在本工作区修改和提交。不得改 A/B/D 工作区。

## 本轮目标

阅读根目录 `docs/ABCD阶段2集成任务.md` 和 A 的 `docs/multisite_integration_v1.md`（如已提交），把已有 centralized optimizer 暴露成 D 每个滚动步可消费的接口。

1. 使 `CentralizedCoordinator` 接受冻结的 config：`provenance`、`method`、`price_levels`、`service_gap`、`time_limit_seconds` 和每站 `soc_terminal`、`reserve_requirement_kwh`、`degradation_per_kwh`、`base_min_fraction`、`base_unserved_cost`、`ev_unserved_cost`。
2. 保留现有整段求解 ledger；在 `metadata.actions[site_id]` 返回首区间候选 SiteAction，在 `metadata.tie_line_flows_kw[line_id]` 返回首区间有符号潮流（正 from→to），并说明 price 是输入响应价、EV 服务是联合优化的需求响应目标。
3. 返回 solver status、model/source/config hashes、failure code 和 `safe_to_dispatch=false`。不可行、超时、非法输入不得回退到 heuristic 或伪造 schedule。
4. 添加 D-compatible fixture 测试：2/4 站、首时段 actions 和 tie flow 与 optimizer schedule 完全一致、价格档位合法、非法输入/timeout/infeasible 保留状态、重复运行账本一致。

## 边界与验收

- 本轮不实现 D 的实际执行器，不宣称 AC/DC 潮流或热约束可行。
- 原有多站模型/Coordinator 测试必须继续通过；运行 `parper.test.test_multisite`、`test_multisite_coordinator`、contract tests 及新增测试。
- `python -m compileall -q src scripts parper/test` 通过。
- 提交仅包含协调器/模型测试/文档，提交至 `team/c-multisite`，不 force-push。

完成时报告 2/4 站接口测试、首时段动作/联络线样例、求解与失败状态、限制和提交哈希。
