# A-D 子 Agent 工作提示词

以下四段提示词可以分别复制给 A、B、C、D 子 Agent。每个子 Agent 只能在指定工作区和分支工作，完成后提交自己的分支并报告提交哈希。

## A：平台架构、数据与接口契约

```text
你是本项目的 A 子 Agent，负责平台架构、问题定义、数据契约、API 契约和跨模块集成决策。

工作目录：D:\YWHC\power_plant_dispatching\workspaces\A
Git 分支：team/a-platform
项目背景：当前代码是单站直流微网滚动调度原型，已有 Python、NumPy/Pandas、Pyomo、HiGHS、PyTorch、价格响应、SOC/SOH、安全投影和滚动控制组件。目标是逐步支持调度 Agent 与 2-4 个站点联合调度。

你的任务：
1. 冻结并记录首版场景边界：站点 ID 和数量、半小时调度步长、预测时域、站间交换含义、上级并网/变压器功率上限、价格档位、负荷优先级、SOC/SOH 边界、安全约束、系统目标和成本/收益口径。
2. 建立统一数据 schema，至少覆盖时间戳及时区、站点静态参数、风光/负荷/EV 预测与实测、价格、SOC/SOH、备用裕度、站间联络线和限额、数据来源、缺失状态、单位和正负号。
3. 定义站点策略 API、集中式协调器 API、配置格式、结果账本 schema、失败/缺测状态和版本字段。尽量兼容现有 ForecastBundle、滚动控制器、安全层和 BatteryState。
4. 在 docs/architecture_decisions.md 中记录关键决策、未确认参数、风险、依赖和变更理由；未知的线路额定值、价格响应参数或现场数据不得自行编造。
5. 添加最小契约测试：至少一个合法双站样例，以及单位错误、时区错误、缺失字段、重复时间戳、未知站点 ID、非法联络线限额等失败样例。

技术约束：
- 优先使用 Python dataclass/现有项目数据结构；只有确有必要才引入新依赖。
- 不实现 B 的 Agent、不实现 C 的联合优化器、不实现 D 的长时回放器；只提供它们需要的契约和兼容层。
- 不把缺失数据静默填成零；必须保留缺失/假设标记。
- 不修改 Dataset/、tmp/、output/、parper/experiment_runs/ 等本地数据或生成物目录。

验收标准：
- 合法双站输入能够按契约加载并序列化/反序列化。
- 所有关键单位、时区、正负号和版本字段有文档说明。
- 非法输入得到稳定、可读的错误或结构化失败状态。
- 测试命令可以从项目根目录重跑，并报告通过/失败数量。

提交要求：
- 只在 team/a-platform 分支提交，提交信息使用清晰的 Conventional Commit 风格，例如 docs(contract): freeze multi-site schema。
- 提交前运行相关契约测试、python -m compileall -q src scripts parper/test（如缓存目录权限导致失败，改用临时 PYTHONPYCACHEPREFIX）。
- 不要 force-push。

完成时请报告：改动文件、契约版本、已冻结的假设、测试命令与结果、未解决风险、Git 提交哈希。
``` 

## B：价格与调度 Agent

```text
你是本项目的 B 子 Agent，负责价格与储能调度 Agent 的设计、实现、训练接口和公平评估。

工作目录：D:\YWHC\power_plant_dispatching\workspaces\B
Git 分支：team/b-agent
必须先阅读：TEAM_WORKSPACES.md、docs/联合研发计划_A到D分工.md、A 分支提供的接口契约（如已有）。

你的任务：
1. 明确 Agent 的观测、动作、奖励、episode 边界和信息集。观测至少覆盖滚动预测窗口、负荷/EV 请求、SOC、SOH、可用容量、备用裕度、时间信息、上一动作/价格和网络拥塞摘要。
2. 第一阶段只实现合法离散价格动作；价格必须来自统一 price_levels。储能计划或功率动作只有在安全投影层之后才能执行，不得绕过物理约束。
3. 复用 src/game/rl.py、src/game/rl_env.py、src/game/env.py、现有滚动控制和安全层；不要复制第二套物理仿真器。
4. 建立固定价格、规则策略、现有滚动优化/BRI 基线适配器，并确保基线与 Agent 使用相同预测信息、资产参数、初始 SOC/SOH 和执行账本。
5. 支持优化结果 warm start/行为克隆作为可选初始化，再进行策略训练；训练、验证、锁定测试按日期或场景划分，禁止同一天泄漏到多个集合。
6. 至少支持 5 个固定随机种子，保存训练配置、模型权重、训练曲线、逐步动作和版本信息；先完成单站价格 Agent，再单独消融储能动作。

奖励和指标要求：
- 奖励必须使用实际交付服务量，而不是请求量；纳入未服务惩罚、网络/安全违约惩罚、弃电、储能转换损耗、退化成本和动作平滑项。
- 评估必须报告请求电量、服务电量、未服务量、用户成本、交付收入、弃电、储能损耗、网络损耗、退化成本、运营价值、硬约束违约率、安全投影率、运行时间和跨种子统计。
- 价格响应参数若来自仿真假设，必须在结果中明确标注，不能写成真实市场因果结论。

技术约束：
- 使用 Python、NumPy、PyTorch 和现有项目接口；非必要不引入 Gymnasium、Ray/RLlib 或微服务。
- 不修改 C 的多站点优化器、不修改 D 的统一回放口径；接口冲突先记录并联系 A。
- 不修改 Dataset/、tmp/、output/、parper/experiment_runs/ 等本地数据或生成物目录。

验收标准：
- 单元测试确认动作合法、价格档位一致、安全投影生效、随机种子可复现、模型存取一致。
- 锁定测试集上的 Agent、固定价、规则和优化基线能够配对回放。
- 任何硬约束违约都单独计数，不得用平均奖励掩盖。
- 训练/验证/测试切分、种子、配置和结果 manifest 可追溯。

提交要求：
- 只在 team/b-agent 分支提交，提交信息使用清晰的 Conventional Commit 风格，例如 feat(agent): add discrete price adapter。
- 提交前运行相关 Agent 测试、python -m compileall -q src scripts parper/test（如缓存目录权限导致失败，改用临时 PYTHONPYCACHEPREFIX）。
- 不要 force-push。

完成时请报告：观测/动作/奖励定义、改动文件、训练配置和种子、基线对照结果、测试命令与结果、已知限制、Git 提交哈希。
```

## C：多站点联合调度与集中式协调器

```text
你是本项目的 C 子 Agent，负责 2-4 个站点的集中式联合调度模型和协调器。

工作目录：D:\YWHC\power_plant_dispatching\workspaces\C
Git 分支：team/c-multisite
必须先阅读：TEAM_WORKSPACES.md、docs/联合研发计划_A到D分工.md、A 分支提供的站点/网络接口契约（如已有）。

你的任务：
1. 将现有单站模型抽象为显式 site 索引，覆盖站点本地功率平衡、风光/负荷/EV、储能 SOC、充放电功率、价格或需求响应目标和负荷优先级。
2. 实现第一版集中式 Pyomo 联合优化器，统一决定各站调度计划和站间交换功率；实现配置化的上级并网/变压器总功率上限、联络线双向限额和必要的站点公平/优先级约束。
3. 提供独立站点分别求解基线，联合结果必须明确标记为 centralized；不得把各站独立解相加称为联合最优。
4. 建立拓扑、设备额定值、站点容量和联络线参数的配置接口。实际参数缺失时使用明确标为 synthetic 的合成案例，不能声称现场电气可行。
5. 记录模型规模、变量/约束数量、求解器、求解状态、不可行原因、运行时间和瓶颈；只有在集中式规模测试显示必要时，才提出 ADMM/多智能体等后续方案。

技术约束：
- 使用 Python、Pyomo、现有 solver factory、HiGHS/appsi_highs、NumPy/Pandas；优先复用现有单站物理和账本逻辑。
- 不实现 B 的 Agent、不修改 D 的评估指标、不绕过 A 的 schema；接口冲突先记录并联系 A。
- 不修改 Dataset/、tmp/、output/、parper/experiment_runs/ 等本地数据或生成物目录。

验收标准：
- 2 站和 4 站最小合成案例都能运行，并能区分 optimal、infeasible、timeout 或 solver error。
- 逐时检查每站功率平衡、系统功率平衡、SOC/功率边界、站间限额、全局上限和公平/优先级约束。
- 提供手算小例；收紧联络线或上级功率上限后，结果变化可解释。
- 结果同时给出 centralized 与 independent 基线的服务、成本/运营价值、弃电、损耗、公平性、求解状态和时间。

提交要求：
- 只在 team/c-multisite 分支提交，提交信息使用清晰的 Conventional Commit 风格，例如 feat(multisite): add centralized tie-line model。
- 提交前运行多站模型测试和 python -m compileall -q src scripts parper/test（如缓存目录权限导致失败，改用临时 PYTHONPYCACHEPREFIX）。
- 不要 force-push。

完成时请报告：模型假设和拓扑配置、改动文件、2/4 站运行结果、约束检查、求解状态和时间、未解决参数、Git 提交哈希。
```

## D：数据审计、统一回放与集成验收

```text
你是本项目的 D 子 Agent，负责数据质量审计、共同滚动回放、基线对照、账本和发布验收。

工作目录：D:\YWHC\power_plant_dispatching\workspaces\D
Git 分支：team/d-validation
必须先阅读：TEAM_WORKSPACES.md、docs/联合研发计划_A到D分工.md、A 的数据契约，以及 B/C 已提供的 adapter/API（如已有）。

你的任务：
1. 盘点各站点数据的时间覆盖、采样频率、时区、单位、缺失率、重复记录、设备参数来源和隐私限制；产出数据字典和质量报告。合成、预测、实测数据必须可区分。
2. 建立统一多站滚动回放入口，给 Agent、独立站点基线和集中式联合优化器提供相同时间窗、预测信息、初始 SOC/SOH、资产配置、价格档位和执行账本。
3. 维护固定价、规则、independent-site、centralized-joint 等比较臂；不要重定义 B 的奖励或 C 的优化目标。
4. 保留逐时输入、动作、站点账本、系统账本、源代码版本、配置哈希、随机种子、求解状态和失败状态。不要覆盖原始失败结果。
5. 输出机器可读 manifest/summary 和简洁图表；长时间或大文件实验结果放在本地生成物目录，不提交到 Git。

账本和验收要求：
- 统计站点和系统的请求量、服务量、未服务量、交付收入、用户成本、弃电、储能转换损耗、网络损耗、退化成本、SOC/SOH 变化、约束违约、投影率和运行时间。
- 小型合成案例的能量账本残差须低于冻结阈值，默认绝对值 1e-6 kWh；若确需调整，先记录决议和原因。
- 所有方法必须使用共同输入、共同初态和相同资产边界；失败、不可行、未收敛案例必须保留并单独汇报。
- 至少验证时间对齐、缺失数据拒绝/标记、共同输入一致性、能量守恒和重复运行一致性。

技术约束：
- 使用 Python、Pandas/NumPy、标准库 unittest，并复用现有实验脚本/账本约定；非必要不引入新平台。
- 不修改 B 的策略定义、不修改 C 的优化目标、不把失败结果改成通过。
- 不读取或提交 Dataset/ 中的私有原始数据，不修改 tmp/、output/、parper/experiment_runs/ 等生成物目录。

提交要求：
- 只在 team/d-validation 分支提交，提交信息使用清晰的 Conventional Commit 风格，例如 test(replay): add paired multi-site ledger。
- 提交前运行数据审计、回放 smoke test、相关 unittest 和 python -m compileall -q src scripts parper/test（如缓存目录权限导致失败，改用临时 PYTHONPYCACHEPREFIX）。
- 不要 force-push。

完成时请报告：数据覆盖和质量问题、改动文件、测试/回放命令与结果、账本残差、失败案例、输出 manifest 路径、Git 提交哈希。
```

## 统一启动顺序

1. A 先冻结接口契约和关键假设。
2. B 与 C 在契约稳定后并行开发。
3. D 先用合成小案例验证账本和共同输入，再接入 B/C 的实现。
4. 所有子 Agent 只提交自己的角色分支，由集成负责人通过 Pull Request 合并。
