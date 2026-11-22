# 主论文实验计划

## A. 数据实验

1. 高速服务区正常充电会话清洗；
2. 跨小时充电电量分摊；
3. 站点/小时基础 EV 负荷；
4. 用户群参数；
5. 价格分箱和观测响应；
6. 按日期留出测试，避免随机交易拆分泄漏。

## B. 算法对比

| 方法 | 动态 SOC | 用户响应 | DC 网络约束 | 作用 |
|---|---:|---:|---:|---|
| 固定电价+规则储能 | 否/简单 | 否 | 否/后验 | 工程基线 |
| 单层经济优化 | 是 | 聚合 | 是 | 优化基线 |
| 无动态 SOC 博弈 | 否 | 是 | 是 | 消融 |
| 无备用博弈 | SOC但无备用 | 是 | 是 | 消融 |
| 本文动态 SFSE | 是 | 是 | 是 | 主方法 |

## C. 场景

- 正常供能；
- 风光低出力；
- EV 高到达负荷；
- PV/Wind 过剩；
- 初始 SOC 20%、50%、80%；
- 预测误差 ±5%、±10%、±20%；
- 不同用户价格敏感性。

## D. 指标

- 运营商净利润；
- 用户充电成本；
- EV 服务满足率；
- EV 失负荷量；
- PV/Wind 弃电量；
- 最低 SOC 和备用裕度；
- 节点电压和线路电流；
- 电池 SOH 日衰减；
- 计算时间和收敛率。

## E. 论文图表

1. 系统结构图；
2. 动态 Stackelberg 信息流；
3. 真实基础 EV 负荷曲线；
4. 不同 SOC 的价格和储能功率；
5. 动态 SOC 轨迹；
6. EV 服务量、削减量和弃电量；
7. DC 节点电压与线路电流；
8. 收益—用户成本 Pareto/对比柱图；
9. 预测误差敏感性；
10. 消融实验。

## F. 论文证据契约与放行门槛

以下项目是比较性结论的放行条件。未满足时可以保留原始结果和诊断图，但只能使用描述性、场景限定的表述；不得写成普遍优越性、全局最优或已验证网络安全。

### F.1 共同需求与动态/固定价格公平性

1. 对动态价格和固定价格使用同一组 EV 请求、同一日期序列、同一初始 SOC/SOH、同一积压需求、同一终端 SOC 规则和同一执行步长；若价格会改变请求，另设 common-request replay，不把 endogenous request 的服务率直接用于政策排名。
2. 每个方法同时报告 requested、served、shed、service rate、curtailment、battery conversion loss、curtailment+loss、throughput、端 SOC/SOH 和 runtime。服务率必须与绝对服务量及请求量同表呈现。
3. EV 功率上限存在未决配置差异：当前实现默认是 8×120=960 kW（`src/game/dc_microgrid.py`），稿件/部分冻结材料采用 8×60=480 kW。不得在新实验中静默选择其中一个值；在项目模型口径正式确定前，所有回放必须显式记录容量配置并将 480/960 kW 当作分开的敏感性场景。
4. 固定价格和动态价格的旧实验还存在日期/需求曲线、 tariff mode、时间步长（24×1 h vs 48×0.5 h）和收入时间积分差异。动态侧EV能量统计乘了0.5h，但 EV revenue 未乘时间步长；固定基线的小时级功率和能量数值相等只是数值巧合。旧结果不能作为公平性能比较，也不得据此给出因果或政策排名结论。
5. 验收：common-request replay 的能量身份残差不超过 `1e-6 kWh`；两方法的 useful delivery（EV+non-EV served）和 terminal stored energy 口径一致；任何“优于固定价格”的句子必须同时给出绝对服务量和共同请求结果。

### F.2 多 SOH × 需求场景

1. 需求至少覆盖 low/P10、median/P50、high/P90；初始 SOH 至少覆盖 `1.00, 0.90, 0.80, 0.70`，每个组合使用不少于 3 个日期或 3 个随机种子，并保持初始 SOC、价格和积压口径可追溯。
2. 每个组合记录收敛状态、各物理残差、requested/served/shed、弃电、battery loss、throughput、aging cost、端 SOC/SOH 和是否触发容量/备用边界。SOH=0.80 高需求若仍未收敛，只能作为失败诊断，不进入性能平均或优越性结论。
3. 必须增加机制消融：匹配初始边际退化系数后分别关闭 SOH 容量反馈和 SOH 成本反馈，避免把“健康反馈”和“固定成本系数差异”混为一个效应。
4. 验收：报告每个 SOH×需求单元的 `n/converged/n_nonconverged`；低、中、高需求不得用单个日期的差异概括为普遍寿命或服务收益。

### F.3 收敛与 Stackelberg 证据

1. 分开记录 `price_residual`、`demand_residual_kw`、`soh_residual` 和 `forecast_price_residual`，不得用不同单位的数值最大值作为唯一均衡证书；达到迭代上限的步必须保留 `nonconverged` 标记并从比较性汇总中隔离。
2. 离散价格内层至少执行完整坐标扫描，或提供对所有单坐标偏离的无改进证书。单次 BRI sweep、多起点收益相同或输出文件完成，均不足以声称全局 Stackelberg 最优。
3. 小规模实例必须用独立 KKT/MILP 基准报告目标值、可行性和最优性间隙；大规模 BRI 仅报告有限迭代近似及多起点稳定性。
4. 验收：代表日逐步收敛率、30 日 nonconverged 数、最大各类 residual、独立最优性 gap 和运行时间均进入结果表；任何“均衡/最优”表述必须限定为“实现的有限迭代近似”。

### F.4 弃电利用率与能量守恒

1. 弃电指标必须同时给出 renewable input、EV+non-EV useful delivery、curtailment、battery conversion loss、network loss、stored-energy change 和 `curtailment+loss`。禁止仅凭 curtailment 下降宣称可再生能源利用率提高。
2. 固定请求、固定端 SOC 的 replay 用于区分真实服务增益和“弃电转电池损耗”；raw curtailment penalty 扫描不得被解释为因果经济最优。
3. 验收：每个 replay 的能量身份残差不超过 `1e-6 kWh`；在 useful delivery 不增加时，若 curtailment 下降而 battery loss 等量上升，应明确标记为耗散转移而非利用率改进。

### F.5 网络和可部署性表述

1. 电压和支路电流若仍是调度后的 post-check，结果只能称为执行层诊断；无真实线路额定电流时不得写“热约束通过”或“网络安全均衡”。
2. 典型 PV/Wind 曲线、循环预测窗口和未经日期匹配的风光输入必须标为 synthetic/assumed；真实预测泛化需另做留出日期实验。
