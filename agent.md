# Agent D：阶段 2 联合回放执行器

## 工作区与分支

- 目录：`D:\YWHC\power_plant_dispatching\workspaces\D`
- 分支：`team/d-validation`
- 只在本工作区修改和提交。不得改 A/B/C 工作区。

## 本轮目标

阅读根目录 `docs/ABCD阶段2集成任务.md` 和 A 的 `docs/multisite_integration_v1.md`（如已提交）。升级 D 共同回放，使它能真实执行有损联络线并接入 B/C，而不只运行 heuristic copperplate arms。

1. 验证 A v1 tie-line 端点、双向额定和损耗；实现按站点/联络线执行功率与损耗账本，执行站点变压器和共享上级限制。未知额定不得当零/无限制；非法或未建模 topology 明确拒绝。
2. 扩充 `adapt_site_strategies` constraints（contract_version、trace_sha256、step_index、dt_hours、assets 和网络摘要），接入 B 的 SiteStrategy；扩充 `adapt_coordinator` 配置构建和输出解析，读取 C 首时段 `metadata.actions` 与 `tie_line_flows_kw`。
3. 在执行层重验、投影、计数 B/C 候选动作和 tie flows。投影后仍不可行则保留失败，不下发；逐步 ledger 记录输入快照、raw/projected/executed site/tie actions、线损、站点/系统残差、求解状态和来源版本。
4. 新增端到端双站 synthetic manifest：至少 4 个 30 分钟区间、至少 1 条有损双向联络线；运行固定价、实际 B checkpoint policy、独立站点、实际 C `CentralizedCoordinator` 四臂。测试不得把 synthetic reference heuristic 标作 Agent/optimizer。
5. 共用输入/资产/初态哈希在四臂必须一致；能量残差 `<1e-6 kWh`；写 manifest、summary、逐步 ledger 到新的 ignored local output 目录，不覆盖旧 run。

## 验收与提交

- 旧 D replay/data audit tests 和新增 tie-line、变压器、B/C adapter、accounting tests 全通过。
- 执行 command-level smoke；输出明确 `data_class=synthetic`、`ranking_authorized=false`，每臂 provider identities/status 正确、状态 completed，且最大逐站/系统能量残差低于阈值。
- `python -m compileall -q src scripts parper/test` 通过。
- 不读取 `Dataset/`；本地数据、checkpoint、权重和大型结果不得提交。
- 仅提交 D 回放/测试/文档至 `team/d-validation`，不 force-push。

完成时报告 smoke manifest/summary 路径、四臂 provider identities/status、哈希一致性、最大能量残差、投影/违约数、测试结果、限制和提交哈希。
