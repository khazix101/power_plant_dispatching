# Agent C

## 工作区

- 目录：`D:\YWHC\power_plant_dispatching\workspaces\C`
- 分支：`team/c-multisite`
- 只能修改本工作区，不得修改集成目录或 A/B/D 工作区。

## 任务

负责 2-4 个站点的集中式联合调度模型和协调器。

1. 将单站模型抽象为显式 site 索引，覆盖本地功率平衡、风光/负荷/EV、储能 SOC、充放电功率、价格/需求响应目标和负荷优先级。
2. 使用 Pyomo 实现集中式联合优化器，统一决定各站调度计划和站间交换功率；配置上级并网/变压器总功率、联络线双向限额和必要的公平/优先级约束。
3. 提供独立站点分别求解基线；联合结果必须标记为 `centralized`，不能把独立解相加称为联合最优。
4. 建立拓扑、设备额定值、站点容量和联络线参数接口。缺少真实参数时只能使用标明 `synthetic` 的案例。
5. 记录模型规模、求解器、求解状态、不可行原因、运行时间和瓶颈；分布式协调算法只作为后续建议。

## 技术与边界

使用 Python、Pyomo、现有 solver factory、HiGHS/appsi_highs、NumPy/Pandas，优先复用单站物理和账本逻辑。不要实现 B 的 Agent，不要修改 D 的指标，不要绕过 A 的 schema，也不要修改本地数据和生成物目录。

## 验收与提交

- 2 站和 4 站合成案例可运行，并区分 optimal、infeasible、timeout 和 solver error。
- 检查每站及系统功率平衡、SOC/功率边界、站间限额、全局上限和公平约束。
- 提供手算小例，并验证收紧联络线或上级上限后的结果变化。
- 同时报告 centralized 与 independent 的服务、价值、弃电、损耗、公平性、状态和运行时间。
- 只提交到 `team/c-multisite`，使用清晰的 Conventional Commit 信息，不得 force-push。

完成时报告假设和拓扑配置、改动文件、2/4 站结果、约束检查、求解状态、参数缺口和提交哈希。
