# B：单站价格 Agent v1

范围为可复现的单站价格策略原型，不是生产控制器，也不是多站点联合优化。2026-11-23 阅读了 TEAM_WORKSPACES.md、联合研发计划、B/AGENTS.md，以及 A 工作区新提供的 docs/multi_site_contract.md、architecture_decisions.md 和 src/multisite/contract.py（v1.0.0）。不修改 C 优化器、D 回放或本地数据目录。

## 观测、动作、信息集与 episode

`src/game/price_agent.py` 复用 `RollingMDPState`、`ForecastBundle` 和已有 `DiscretePolicyNet`。观测依次包括四组完整预测窗口（PV、风电、EV 请求和非 EV 负荷），SOC、SOH、执行可用容量、备用能量、SOC 能量减备用的裕度、EV backlog、当前计量 EV 请求、上次价格及存在标记、上次投影后储能计划、日内时间 sin/cos、星期、步序号，以及三项网络摘要（loading、voltage margin、availability flag）。所有特征采用固定工程尺度，不在测试数据上拟合归一化参数。

预测来自每个 common-trace step 显式附带的 `forecast`：`issued_at`、`timestamps`、`pv_kw`、`wind_kw`、`ev_kw`、`base_kw`、`congestion`。签发时间不得晚于决策时刻，首个时间戳等于决策时刻，预测采样间隔等于半小时执行间隔。后三项拥塞摘要不能用未来实测潮流回填；availability=0 表示摘要不可用，不代表实际网络零拥塞。缺失必需字段直接报错。未来实测行不会用于构造策略输入。

动作是长度 H 的整数价格档位索引，全部通过同一外部 `price_levels` 表映射。每次只执行第一档价格，下一步用新预测重新决策。固定价使用中间索引（偶数 K 取 K//2），规则按预测供需盈余选最低/最高档，优化基线用现有 BRI（2 sweeps），Agent 用离散策略网络。价格响应、leader 储能规划及安全层统一走 `PricingEnv.evaluate_action`；安全校验失败立即报错，禁止派发。D 的 DC 执行器负责最终物理执行、限幅和计量。没有新增物理仿真器，也没有开放学习储能功率的旁路。

一个 episode 是输入 trace 的完整有序时段，所有方法从该 trace 的相同 SOC/SOH、资产和终端约束开始。episode 结束不跨 trace 继承电池状态。训练使用完整 episode 的 undiscounted return-to-go REINFORCE、指数移动基线和梯度裁剪。上一储能输入是上一投影计划，不伪装成已计量功率；D v1 回调没有前一步执行账本反馈。

## 奖励与实际执行账本

只消费 D 的 executed ledger；不把 RollingSFSEMDPEnv 的 planned reward 当作实际收益。

每步交付收入/用户成本 = D 的实际 EV 服务电量 × 执行价格。未服务量 = 原始物理请求量 − 实际服务量，包含价格抑制和物理削减。运营价值 = 交付收入 − 未服务惩罚 − 弃电惩罚 − 转换损耗成本 − 网络损耗成本 − 吞吐退化成本。训练奖励再减价格二次平滑项及逐项硬违约惩罚；终端 SOC/SOH 违约另计数并扣终端奖励。

默认合成权重：未服务 2、弃电 0.2、储能损耗 0.1、网络损耗 0.1、退化 0.05（CNY/kWh），平滑 0.1，逐项硬违约 1000。收益和损耗成本不能解释为真实合同结算；它们是显式实验权重。每个实验保存 RewardConfig。

逐项统计电压、能量残差（1e-6 kWh）、SOC 动态残差（1e-6）、SOC 边界、储能功率、EV 服务功率上限和安全计划验证。终端 SOC/SOH 单独计数。安全投影率表示计划被安全层修改、EV 被安全层削减，或执行功率与投影计划差异超过 1e-6 的步比例；它不等同于硬违约率。必须同时看计数和 rate，不能用平均奖励抵消失败。非收敛行由 D 保留，本训练器拒绝为缺失执行奖励构造回报。

报告含请求/服务/未服务电量、用户成本、交付收入、弃电、储能损耗、网络损耗、退化成本、运营价值、奖励、各项硬约束违约数、终端违约数、投影率、策略耗时和配对回放总耗时，另保存 D 原始逐步执行账本。BRI 优化其原有目标，不是此执行后奖励的全局 oracle。

## 训练、锁定测试与保存

入口：`scripts/train_price_agent.py`。依赖 Python、NumPy、PyTorch、既有 Pyomo/HiGHS；不新增 Gymnasium、Ray 或服务。

```powershell
$env:PYTHONPYCACHEPREFIX = Join-Path (Get-Location) 'local/b-agent-cache'
& D:/miniconda3/python.exe scripts/train_price_agent.py --synthetic --episodes 2 --bc-epochs 1 --out local/b-price-smoke-v1
# 使用真实来源的显式预测输入时，替换 --synthetic：
# --partitions <JSON文件路径> --episodes 20 --bc-epochs 0 --out local/<新目录>
```

partitions JSON 包含 train/validation/test 三个非空 trace 数组。每个 trace 遵循 D common-trace-v1，含执行参数、初态、终态、哈希及上述 forecast 扩展。时间戳必须显式 +08:00；按本地日期隔离，预测覆盖日期也参与交集检查。不支持把同一天拆成多个 trace；需要合并成一个 episode。历史已探索日期应由上游排除，不能因为日期不重叠就称其未见测试。

默认种子固定为 11、23、37、53、71。每种子保存 policy-<seed>.pt、training-<seed>.json（训练曲线、动作和执行记录），并按 seed/episode/method 保存锁定测试动作及账本。先完成训练和验证 checkpoint 选择，再访问 test。可选 `--bc-epochs` 仅以训练集 BRI 轨迹做分类 log likelihood 预训练，再做 RL；validation 不产生训练梯度。save/load 检查 schema 和完整 price_levels。

manifest 保存分区哈希、输入快照、源 Git revision、dirty 标记、源文件 SHA256、Python/NumPy/PyTorch、solver、奖励权重、网络/优化器配置、种子、输出文件哈希和成功/失败原因。测试汇总先在 seed 内按日期平均，再计算 5 seeds 的均值、样本标准差和 t(4) 95% 区间；另报 Agent 对每基线的配对差值区间。基线确定性重复不创造额外独立样本。原始结果保存在 git ignored `local/`，不提交数据或权重。

## A/D 接口协调与限制

2026-11-23 已通过 A 聊天联系并获得确认；A 最终依赖提交为 `7bc03cc524ea1e9531fcd195f578f32909e234a2`：

- A 的 SiteStrategy v1.0.0 已提供；`ContractPriceStrategy.decide(site_id, forecast, state, constraints)` 支持桥接，constraints 要求 contract_version、price_levels、trace_sha256。forecast 是显式 step+forecast mapping；state 为当前 SOC/SOH。集成时延迟导入 A 的 SiteAction，B 不复制 A 的契约模块。返回的 storage_kw 为候选安全计划，仍须交统一执行器。
- A 已冻结 common-trace-v1 按名义容量执行且不乘 SOH；D 当前冻结 SOH，B 为公平对照严格镜像此语义，不更新退化状态。不能据此声称长期 SOH-aware 策略已经验收。A 确认本桥接兼容 v1；动态 BatteryState 退化实验留待后续独立协议。
- A 的 reserve_margin_kwh 是 SOC 相对技术下限的能量裕度；B 当前 reserve_kwh 是 D soc_reserve 对应的备用目标能量，SOC 能量减该目标另作观测，不把它映射成 A 的 reserve_margin_kwh。正式输入转换时需保持两个概念独立。
- A 把响应价、购电价、售电价和 EV 交付结算价分开。B 的离网合成回放沿用 D 的“所选响应价同时作为交付单价”假设，不包含购售电，不能代替正式独立结算信号。
- D 没有提供可验证的线路热限额，也没有非 EV 缺供的独立完整账本。thermal_limits_verified=false 保留为未验证，不计作通过，也不凭空补零。现有硬违约零仅指可观测的已检查约束。
- 合成需求响应为 EV utility=1.9 CNY/kWh 的效用模型，训练未校准真实市场因果关系；合成样例用理想预测，三天各两个半小时，仅作端到端工程冒烟。
- 第一阶段只学习价格。储能动作消融尚未实施；需要冻结状态/容量接口并通过长时价格实验后，单独加入安全投影下的储能动作实验。

## 本次验证记录

五种子 smoke 使用三个不同日期（2024-01-01 train、01-02 validation、01-03 test），每个日期两个 0.5h 步，预测窗口 H=1，初始 SOC=0.5、SOH=1，容量 2000 kWh、功率 800 kW、EV cap=480 kW。统一档位 [1.5,1.7,1.9,2.1,2.3,2.5]，hidden=64、Adam lr=0.001、return scale=100、gradient clip=1；每种子 2 episodes + 1 BC epoch。

五种子平均 EV 服务量：Agent 52 kWh、固定价 10 kWh、规则 45 kWh、BRI 80 kWh；每方法原始请求均为 80 kWh。平均运营价值分别为 31.2794、-124.8185、-1.4038、133.6779 合成元。Agent 对固定价、规则、BRI 的配对均差分别为 +156.0979、+32.6832、-102.3986；95% 区间分别为 [-18.0026,330.1983]、[-141.4173,206.7837]、[-276.4991,71.7019]，全部包含零，不宣称优势。

全部 20 个方法×种子测试回放中，已检查硬约束和终端约束违约数为零；投影率均为 1（执行器为网络损耗调整计划）。热限额未验证。完整数值与逐步动作见 local/b-price-smoke-v1/summary.json 和各 test JSON；提交版本的复跑保存在 local/b-price-smoke-74ee75b/，manifest 绑定代码提交 `74ee75b32acf775902dcaec0d72ad2bb6d3a1e38`。

测试命令（均在 B 工作区）：

```powershell
& D:/miniconda3/python.exe -m unittest discover -s parper/test -p test_price_agent.py -v
& D:/miniconda3/python.exe -m unittest discover -s parper/test -p 'test_rolling*' -v
& D:/miniconda3/python.exe -m unittest discover -s parper/test -p test_fair_replay_runner_contract.py -v
& D:/miniconda3/python.exe -m unittest discover -s parper/test -p test_agent_warm_start.py -v
& D:/miniconda3/python.exe -m compileall -q src scripts parper/test
```

新增 Agent 测试 10 项、Rolling MDP 5 项、D 回放契约 8 项、既有 warm start 2 项通过。另通过 importlib 逐个调用 rolling_price_plan/rolling_interval/safety_interval 三个模块的 test_* 函数，共 7 项，总计 32 项；当前 Python 环境没有 pytest，不为本次工作引入新依赖。compileall 通过。Windows 沙箱的 TemporaryDirectory 权限导致 checkpoint 测试初次失败，经授权的相同命令重跑通过；其余测试不受影响。
