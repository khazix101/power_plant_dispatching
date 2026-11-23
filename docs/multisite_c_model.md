# C：集中式多站模型与验收记录

2026-11-23。范围为合成调度原型，不能据此声称现场电气可行。

## 接口依赖与文件

共享契约完全沿用 A 的 `src.multisite.contract` v1.0.0，依赖 A 提交
`8fec9f9`、`7bc03cc`，在 C 分支分别引入为 `de1f6d6`、`7f1b42a`。
没有改写或复制另一套共享 schema。已向 A 反馈并按其冻结决定处理：联络线
export 为 from→to，import 为反向；0 禁止该方向；名义容量不乘 SOH；
reserve_margin 是观测，reserve_requirement 才是额外备用要求；响应价格和三种结算价格各自独立。

C 新增：

- `src/game/multisite.py`：内部参数、显式 site/time 模型、求解、独立基线和数值审计。
- `src/game/multisite_coordinator.py`：A 的 Coordinator 协议适配器和逐时站点/联络线/系统账本。
- `scripts/run_multisite.py`：2/4 站合成对照、规模测试和共享契约 JSON 入口；结果仅打印 stdout。
- `parper/test/test_multisite.py`：物理、手算、灵敏度、失败状态和基线审计。
- `parper/test/test_multisite_coordinator.py`：共享契约集成、缺测拒绝、重复输入与结果一致性。

## 模型与边界

半小时平均有功功率，内部功率 kW、能量 kWh、结算价 CNY/kWh。
所有调度变量使用显式 `(site, time)` 索引，SOC 使用区间边界索引。
每站风电与 PV 是已知非负预测，允许合并弃电；基础负荷和 EV 请求分别记录。
EV 服务量是集中决定的需求响应目标，发布价格固定且必须属于配置的合法档位；
未引入没有标定依据的弹性价格模型，也没有实现 B 的 Agent。

站内平衡为：风光−弃电＋购电−售电＋放电−充电＋联络线净接收＝基础服务＋EV 服务。
每条线在发送端计功率、接收端乘显式效率，系统平衡必须计入这部分损耗。
线路是可控运输功率模型，效率为固定比例；没有电压、无功、相角、环流或 I²R 潮流约束。
站点变压器限制作用于站点对上级并网口；联络线是另一独立端口。
共享上级进出口限制分别约束各站购电/售电的**毛功率之和**，不允许相互抵销。

SOC 动态沿用 `DCSOCState.advance` 的效率与正负号：
`SOC[t+1] = SOC[t] + dt*(eta_charge*charge-discharge/eta_discharge)/capacity`。
测试逐时与原实现交叉验证。容量按 A 的 v1 名义容量口径，初始 SOH 验界并保持常数；
这里不估算寿命衰减反馈。额外备用能量映射为 `soc_min + reserve_requirement_kwh/capacity`。
末端 SOC 不低于显式目标，避免通过免费耗尽储能制造收益。
充放电、站点买卖电以及每条联络线正反向均用二进制变量保证互斥。

基础负荷下限为逐时请求×base_min_fraction；只要该时段服务任何 EV，必须先满足全部基础负荷。
EV 最低服务率是站点全时域服务电量/请求电量的硬下限，EV 设备功率额定也单独约束。
公平约束限制任意两个有正 EV 请求站点的全时域 EV 服务率差不超过 service_gap。
零请求站点不参与该比例比较。当前公平规则没有保证每站经济上获益，逐站成本/价值一并披露。

目标最小化：购售电净成本＋储能吞吐退化成本＋基础/EV 未服务惩罚−实际 EV 交付收入。
运营价值单独定义为实际 EV 交付收入−购售电净成本−退化成本，不含未服务惩罚。
用户成本在首版等于 EV 实际交付账单；基础负荷没有虚构零售收入。
弃电与储能/网络损耗分别报告，弃电没有额外收费。权重、效率与价格均是合成假设。

## 配置方法

独立内部合成入口为 `synthetic_case(2|4, periods)`；`SiteInput/TieInput/NetworkInput`
只是优化器内部参数，不是外部共享 schema。正式入口为
`CentralizedCoordinator().coordinate(CoordinationRequest)`，或 JSON 文件：

```powershell
& D:/miniconda3/python.exe scripts/run_multisite.py --request C:/path/request.json
```

request 使用 A 的 contract_version、run_id、input、config；input 中的额定功率、
储能容量/效率/SOC-SOH 下限、EV 容量与最低服务率、上级限额、联络线损耗必须已知。
预测、三个结算价格及响应价不得 missing/stale/invalid，来源/假设保留到每行账本。
初始 SOC 来自首个区间；后续输入 SOC 是观测/预测背景，不强迫优化轨迹逐时等于它。
reserve_margin 必须与该输入 SOC、设备下限、名义容量一致。
PV/风电预测不得超过设备额定值，EV 请求可以超过 EV 功率额定，但实际服务不得越额。

config 必填：provenance（synthetic/measured/anonymized）、price_levels、service_gap、
time_limit_seconds、sites（ID 与 input 完全一致）。每站 policy 必填：
soc_terminal、reserve_requirement_kwh、degradation_per_kwh、base_min_fraction、
base_unserved_cost、ev_unserved_cost。method 可为 centralized（默认）或 independent。
random_seed 用于追溯；本优化器没有随机需求生成。完整可执行合成共享输入在
`test_multisite_coordinator.request_fixture(2|4)` 中，不需要读取任何现场数据。
参数未知会返回 invalid_input 和解释，不会自动补零。

每次返回输入/配置 SHA256、代码 revision、模型版本、来源标记、模型大小、求解时间、
逐时审计残差和结算分项。即使 optimal，safe_to_dispatch 也为 false：这是规划候选，
没有通过现场潮流/安全投影。失败不产生伪造的成功账本。

## 独立基线与状态

independent 对每站分别求解同一物理模型，初态、请求、价格、资产不变，联络线断开；
各站只执行本地并网限额，共享上级约束和跨站公平约束在事后审计。
因此它可能返回每站 optimal、但 shared_network_feasible=false，不能称为联合最优或
共同网络可执行方案。需要共同网络可执行的独立方案时，应由 A 冻结配额/安全投影规则后另加比较臂。
当前演示中独立基线违反共享进口限额，其更高服务量不能作为可行方案优势。
集中式与独立对比不构成“在相同耦合可行域上一定改善”的证明。

求解状态区分 optimal、infeasible、timeout、solver_error，输入不完整另报 invalid_input。
只发布已证明 optimal 的计划，超时的 incumbent 不冒充最优计划。
不可行说明涉及基础/EV 最低服务、末端 SOC/备用、并网/联络线和公平约束；
当前 HiGHS 适配器不提供 IIS，不能声称精确定位了唯一冲突约束。
故障不会回退 independent。独立基线保留每站子结果和状态。

复用现有 `get_solver_name(prefer='appsi_highs')` 工厂选择，未修改共享 solver factory。
appsi_highs 的 Legacy.solve 会用 timelimit 参数覆盖 config.time_limit；
这里显式传入 timelimit，0 秒限时测试证实返回 timeout。其他求解器回退保留但未做跨求解器验收。
time_limit 是每次求解的预算，独立基线每站各一次、另报总耗时。

## 合成结果（4×0.5 小时）

拓扑为 2 或 4 节点链，双向联络线各 6 kW、效率 0.98。
奇数站 PV=12 kW、偶数站 PV=0；风=0，基础=2、EV 请求=8 kW；
每站并网进口=10、出口=0 kW，BESS=10 kWh/2 kW，效率 0.95，SOC 0.1–0.9，
初态与末端目标均 0.5。共享进口=每站数×4 kW、出口=0，服务率差上限 0.5。
购电价=1、EV 交付价=2、售电价=0；基础/EV 缺供惩罚 100/10，退化权重 0.01。
共享契约集成夹具使用显式 1 kW 出口额定（A 当前站点额定字段要求正数）；其余同源假设。

- 2 站 centralized：optimal，102 变量（28 二进制）、88 显式约束；EV 服务 31.92/32 kWh，
  基础服务 8/8 kWh；购电成本 16，交付收入 63.84，运营价值 47.84；
  弃电 0、网络损耗 0.08 kWh、储能损耗 0；EV 服务率差 0.005；总耗时 0.0522 s。
- 2 站 independent：两站 optimal，EV 服务 32/32 kWh，基础服务 8/8 kWh；
  购电成本 20，交付收入 64，运营价值 44；弃电 4 kWh，网络/储能损耗 0，
  服务率差 0；耗时 0.0620 s；共享进口超限最大 2 kW。
- 4 站 centralized：optimal，216 变量（60 二进制）、184 显式约束；EV 服务 63.84/64 kWh，
  基础服务 16/16 kWh；购电成本 32，交付收入 127.68，运营价值 95.68；
  弃电 0、网络损耗 0.16 kWh、储能损耗 0；服务率差 0.0075；总耗时 0.0638 s。
- 4 站 independent：四站 optimal，EV 服务 64/64 kWh，基础服务 16/16 kWh；
  购电成本 40，交付收入 128，运营价值 88；弃电 8 kWh，网络/储能损耗 0，
  服务率差 0；耗时 0.1233 s；共享进口超限最大 4 kW。

以上集中式所有建模约束通过逐时审计，最大能量平衡残差 4.45e-16 kWh 以下。
成本使用合成 CNY 权重，不代表现场收益。四站存在多重最优解，具体逐站功率分配可能变化，
但目标、服务总量和约束须一致；重复同环境执行也已测试一致。

## 手算与敏感性

单步半小时，无储能、无并网、无基础负荷：donor 发电 10 kW，receiver 请求 10 kW。
无损联络线 6 kW 时服务=3 kWh，弃电=2 kWh；收紧至 2 kW 时服务=1 kWh、弃电=4 kWh；
关闭后服务=0、弃电=5 kWh。目标为 `10×未服务kWh−2×服务kWh`，分别为 14、38、50。
联络线仍为 6 kW，允许 receiver 额外进口 4/2/0 kW 时服务为 5/4/3 kWh。
线损改为 10% 时，6 kW 发送仅交付 5.4 kW，服务 2.7 kWh、网络损耗 0.3 kWh。
以上均由测试断言，不依赖优化器自报的约束值。

## 规模与复现

环境：Python 3.9.23、Pyomo 6.9.5、HiGHS 1.15.1、NumPy 1.26.4、Pandas 2.3.3。
appsi_highs，60 s/solve，确定性重复负荷，未读取 Dataset 或输出目录。
约束数为 Pyomo 显式约束，不包含变量上下界、求解器预处理后行数或内部割。

- 2 站×48 步：1202 变量/968 约束，总计 0.201 s。
- 2 站×96 步：2402/1928，总计 0.322 s。
- 2 站×336 步：8402/6728，总计 1.155 s。
- 4 站×48 步：2548/1944，总计 0.364 s。
- 4 站×96 步：5092/3864，总计 0.649 s。
- 4 站×336 步：17812/13464，总计 2.491 s，其中优化 2.236 s。

全部 optimal，最大逐时能量残差 <1e-6 kWh，主要耗时为求解器导入/优化。
这只是重复合成负荷压力测试，不是复杂现场 MILP 的规模保证；没有证据需要本阶段引入 ADMM/多智能体。
后续应先增加真实时变输入与 48 步滚动预算测试，再决定是否改变协调架构。

```powershell
& D:/miniconda3/python.exe -m unittest discover -s parper/test -p 'test_multisite*.py' -v
& D:/miniconda3/python.exe scripts/run_multisite.py --sites 2
& D:/miniconda3/python.exe scripts/run_multisite.py --sites 4
& D:/miniconda3/python.exe scripts/run_multisite.py --scale
$env:PYTHONPYCACHEPREFIX = Join-Path $env:TEMP 'codex-pycache-c-multisite'
& D:/miniconda3/python.exe -m compileall -q src scripts parper/test
```

多站相关测试 30 项通过（含 A 契约 12 项、C 模型 14 项、C 协调器 4 项）。
全树 compileall 通过，缓存指向系统 TEMP。既有动态 DC 回归是普通 test 函数，
unittest discover 不会收集，且当前环境没有 pytest；通过标准库 FunctionTestCase
执行 11 项，10 项通过，1 项因缺少
`Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv` 报 FileNotFoundError。
该失败保留，不生成或复制缺失的数据。重跑命令：

```powershell
& D:/miniconda3/python.exe -c "import runpy,unittest;d=runpy.run_path('parper/test/test_dynamic_dc_regressions.py');s=unittest.TestSuite(unittest.FunctionTestCase(v) for k,v in d.items() if k.startswith('test_'));r=unittest.TextTestRunner(verbosity=2).run(s);raise SystemExit(not r.wasSuccessful())"
```

## 尚待实际资料

现场拓扑与设备额定、电气接线/计量边界、电压/无功与线路阻抗、实际损耗模型、
储能效率/SOH 与退化标定、负荷等级和 EV 最低服务合同、价格/税费及公平补偿规则均待确认。
固定损耗运输模型、固定价服务目标和常量 SOH 的结论只适用于明确标记的合成输入。
