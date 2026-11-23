# A-D 阶段 2：联调与共同回放

目标是把 A 的契约、B 的单站价格 Agent、C 的集中式协调器和 D 的共同执行回放接起来。当前 A/C/D 的功能测试已通过，B 的 10 项 Agent 测试也已通过；上一阶段的缺口是 D 没执行联络线、B/C 适配器未进入 D 的真实回放。当前阶段只验收合成 2 站端到端，不把合成结果解释成真实现场性能。

## 共用接口冻结

继续使用 A 的 `src.multisite.contract` v1.0.0，不改 wire schema。A 在 `docs/multisite_integration_v1.md` 冻结以下适配约定；B/C/D 按同一约定实现：

### D → B：逐站策略调用

`SiteStrategy.decide(site_id, forecast_window, state, constraints) -> SiteAction`

- `forecast_window` 是从当前半小时开始的共同预测序列；每项包含 `timestamp`、数值型 `pv_forecast_kw`、`wind_forecast_kw`、`load_forecast_kw`、`ev_forecast_kw` 及其来源/质量摘要。
- `state` 至少含当前 `soc`、`soh`。
- `constraints` 至少含 `contract_version`、`trace_sha256`、`step_index`、`dt_hours`、站点 `assets`、当前合法价格档位和网络/安全摘要。未知网络摘要要显式标记不可用，不能伪造成零约束。
- 输出 `SiteAction` 的时间戳必须与当前区间一致；价格必须属于 `price_levels`。`storage_kw` 和 `requested_ev_kw` 是候选动作，由 D 执行层再次限幅/校验。

### D → C：滚动协调调用

- 每个决策步将 A v1 输入裁成当前及未来剩余时段，时间戳、输入和初态来自同一 replay manifest。
- `CoordinationRequest.config` 明确包含 `provenance`、`method`、`price_levels`、`service_gap`、`time_limit_seconds` 和 `sites`。`sites[site_id]` 明确包含 `soc_terminal`、`reserve_requirement_kwh`、`degradation_per_kwh`、`base_min_fraction`、`base_unserved_cost`、`ev_unserved_cost`。不得在协调器内静默使用未声明的业务/设备参数。
- `CoordinationResult.ledger` 继续保存整段优化明细；`metadata.actions[site_id]` 给出首时段候选 `SiteAction`；`metadata.tie_line_flows_kw[line_id]` 给出首时段有符号潮流（正值按 from→to，负值反向）。响应价格由输入/策略提供，C 的 EV 服务量是优化的需求响应目标；C 不虚构价格弹性。
- `safe_to_dispatch` 仍为 false。D 必须独立重验设备、站间和总线约束；未通过时保留失败状态，不执行或伪造可行结果。

## A-D 具体交付

- **A**：冻结以上接口和 config 字段，给出最小双站 fixture、字段单位/正负号、状态与失败语义；测试合法/非法 request 和 action/flow 形状。不得实现 B/C/D 模块。
- **B**：实现 D `SiteStrategy` 回调适配器，把 D 的预测窗口、SOC/SOH 和约束安全映射到现有价格 Agent；支持锁定价格档位、固定 checkpoint；动作仍走安全执行层。测试当前 H=1 checkpoint 在 D 长预测窗调用时的维度/索引处理、时间戳、档位、状态字段和确定性。
- **C**：按 A config 适配 `CoordinationRequest`；把优化解首时段转为 `metadata.actions` 和 `metadata.tie_line_flows_kw`，保留完整账本、solver status、源版本和 infeasible/timeout 状态；手算 2 站例确认返回动作与优化首时段一致。
- **D**：支持 A v1 tie-line 输入，校验端点/双向限额/效率，执行正反向功率和线损并核对站级/系统账本；支持站点变压器限额，不得忽略。接入 B/C adapter，运行固定价、B Agent（至少一个固定 checkpoint）、独立站点和 C centralized 四臂共同输入的双站合成测试。保留每步原始动作、投影动作、联络线动作、失败和 solver 状态。

## 整体验收门槛

1. A/B/C/D 的接口测试全部通过；所有四个 worktree 的 `compileall` 通过。
2. D 使用 2 站、至少 4 个半小时区间、至少 1 条有损双向联络线跑通四臂。B 必须加载实际固定 checkpoint，不允许用规则策略冒充 Agent；C 必须调用真实 `CentralizedCoordinator`，不允许 heuristic 冒充优化器。
3. 同一时间轴、预测、初态和资产摘要的哈希在四臂一致。输出明确标为 synthetic、ranking_authorized=false（仅冒烟时）。
4. 按每站和系统检查需求/服务、SOC、功率、变压器/上级进口出口、联络线方向限值、线损、未服务和能量恒等式；账本最大绝对残差 `<1e-6 kWh`。超过阈值失败，不得放宽阈值隐藏问题。
5. 故意越界动作要被安全层投影并计数；越界联络线/未知参数/非法结算输入须拒绝；infeasible/timeout 保留失败状态。任何一臂错误不得改成基线成功。
6. 四臂 smoke 只验证联调正确性，不验收 Agent 收益优劣或现场电气安全；性能评估另需多日期锁定测试和已确认站点数据。

## 分支与提交

A/B/C/D 继续只在 `workspaces/A-D` 的各自分支工作；每个角色提交聚焦 commit，不改其他 worktree。每个提交附测试命令和结果。D 完成后给主线程 manifest/summary 路径和四臂 status，主线程从四个 worktree 审核实现并独立重跑验收门槛；未达到门槛的角色标记部分通过并列出阻塞项。

## 本轮验收记录（2026-11-23）

主线程已独立执行 A 契约及适配一致性测试 19 项、B Agent/策略 adapter 测试 18 项、C 模型/契约/协调器测试 36 项、D 数据/回放/真实四臂 CLI 测试 19 项，均通过。B 的 checkpoint 临时文件测试需要沙箱外文件权限；首次权限失败保留，授权重跑通过。

A 最终冻结的可执行配置：`provenance={data_class,source}`；`service_gap={max_ev_service_fraction_gap}`，比例在 [0,1]；`method=centralized|independent`；协调请求 `time_limit_seconds>0`。站点变压器限额 `null` 表示未知、执行拒绝，`0` 明确禁止该方向。正联络线潮流使用 export_limit，负潮流使用 import_limit。D 的本轮 SOC 状态转移使用名义容量、SOH 保持不变，与 A/B/C 一致。

主线程真实四臂回放入口为 D 工作区的 `scripts/run_multisite_replay.py --stage2-smoke`，通过三个显式 `--integration-root` 加载 A/B/C 工作区。独立复跑结果保留在 D 的 ignored `local/results/d_validation/acceptance-stage2-root-01/`；后续 CLI 回归也以唯一目录保存证据。双站、4 个半小时、1 条有损联络线；四臂均完成，共同 trace/资产/初态哈希一致，最大站级/系统能量残差低于 2.23e-15 kWh。

本轮结论：A/B/C 模块及 D 的真实 B checkpoint/C coordinator 接口联调通过；完整优化性能比较尚不验收。四臂中独立站点臂仍是明确标记的合成启发式，并非 C 的独立站优化器，因此只支持工程冒烟。B checkpoint 是本机 ignored 产物，不能仅凭 Git clone 复现实验；复跑需提供锁定 checkpoint 或按 B 训练入口重建并重新记录哈希。本轮没有多日期泛化、收益优势、现场潮流/热约束或生产部署结论，`ranking_authorized=false`。
