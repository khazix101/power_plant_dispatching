"""Apply the feasibility-focused editorial revision to both review copies."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1] / 'parper'


def section(text, start, end, replacement):
    a = text.index(start)
    b = text.index(end, a)
    return text[:a] + replacement.strip() + '\n\n' + text[b:]


def revise_en(s):
    s = s.replace(s.splitlines()[0], '# Integrating Battery State of Health into Rolling Leader-Follower Pricing and Dispatch for an Islanded Highway EV Charging Microgrid: A Feasibility Study', 1)
    s = section(s, '## Abstract', '**Keywords:', '''## Abstract

This study proposes a method for incorporating battery state of health (SOH) into rolling leader-follower pricing and storage dispatch at an islanded highway electric vehicle (EV) charging station. SOH changes the operator's usable-capacity constraint and marginal degradation cost, while executed trajectories update physical health at daily boundaries. Feasibility is assessed through mechanism ablations, energy and delivered-revenue identities, and continuous state-transfer checks, rather than assumed economic superiority. The frozen system comprises 400-kW PV, 100-kW wind, a 2000-kWh/800-kW BESS, and eight 60-kW charging posts, with date-matched ERA5 physical drivers. Of 144 independent-day ablation cases, 136 passed all declared gates and eight failed the cumulative energy gate. A planned 90-day common-request diagnostic produced 72 accepted consecutive days in both dynamic arms; each passed 3456 half-hour energy and SOC checks. In the SOH-aware arm, health declined from 0.900000 to 0.896641 and the marginal coefficient increased from 0.082496 to 0.082888 CNY/kWh, demonstrating active cross-day feedback. Both dynamic arms failed the coordinate-search stopping test on day 73, so full-window solver reliability is not established. Because the continuous diagnostic fixes requests, it validates health-dependent dispatch and accounting, not strategic price responsiveness or equilibrium. Its conditional modeled-value gain does not establish general profitability or lifetime improvement. The evidence supports a computationally implementable SOH-coupling method over the accepted tested domain, with explicit limits on convergence and deployment.''')
    old = "The present study asks whether daily health feedback can be integrated consistently into rolling price and storage decisions, and what conclusions the resulting evidence supports. Its contributions are an implemented coupling between operational decisions and SOH-dependent capacity and marginal cost; a three-way SOH ablation with common trajectory-based accounting; and controlled energy audits that distinguish useful renewable delivery from storage losses. A limited learning-controller diagnostic follows the optimizer test, after examining the source of the apparent improvement."
    s = s.replace(old, '''The central question is whether SOH can enter the operator's decision problem before dispatch, with its consequences carried consistently into the next decision. The method connects two time scales: operational price-response and storage decisions use a health-dependent feasible set and degradation coefficient; daily executed SOC and throughput update the physical battery state. SOH is a state of the operator's asset, not an additional player. The follower retains its own charging-allocation objective. Health can therefore affect the operator's candidate-price evaluations indirectly through storage feasibility and cost, without requiring the user to optimize battery health.

The contributions are: (i) an explicit capacity-and-cost interface that incorporates SOH into the rolling leader-follower decision model; (ii) a state-transfer and accounting protocol that connects executed dispatch to subsequent health-dependent decisions; and (iii) bounded feasibility evidence from component ablations and continuous ledgers, including failed cases. The study tests implementation and numerical executability under declared assumptions. It does not require the health-aware policy to outperform every baseline. Learning assistance is an auxiliary diagnostic and is not the proposed method's defining contribution.''')
    s = s.replace('Planning enforces SOC 0.10-0.90 and terminal SOC at least 0.20. Execution enforces a 0.20 reserve at every step. These feasible sets differ; a planned first-slot discharge can be reduced during execution.', 'In the historical controller and independent-day diagnostics, planning allows SOC 0.10-0.90 and terminal SOC at least 0.20, whereas execution enforces a 0.20 reserve at every step; execution may reduce a planned discharge. The continuous precision runner explicitly uses 0.20-0.90 in both planning and execution, with terminal minimum 0.20. These protocol differences are recorded rather than pooled.')
    s = s.replace('In the SOH-aware planner, effective capacity is E_nom times provisional health; ablations plan at nominal capacity. Physical execution uses health-dependent capacity in every variant.', 'The SOH-aware planner uses effective capacity E_nom times health. The independent-day capacity ablation can instead plan at nominal capacity while retaining health-dependent execution. In the continuous matched-cost diagnostic, all arms use current health-dependent capacity in planning and execution; its dynamic-arm contrast isolates refreshed versus frozen marginal cost, followed by the resulting state-path effects.')
    s = s.replace("Trajectory accounting uses daily incoming effective capacity times 1000 CNY/kWh as its value coefficient and a residual-value derivative of 1/0.30. The planning marginal coefficient is recomputed from the attached provisional health state. Its coefficient C(h) is that state's effective capacity times 1000 CNY/kWh. It uses reference DOD 0.5 and an interim low-health wear multiplier:", "The current continuous protocol uses a declared nominal replacement-value reference C_ref = 2,000,000 CNY (2000 kWh times 1000 CNY/kWh) for both trajectory aging valuation and the planning marginal coefficient, with a residual-value derivative of 1/0.30. C_ref is not reduced again with usable capacity. The coefficient is refreshed from the incoming physical health at each daily boundary; historical outer-loop variants also evaluate provisional health. For the single-group battery, reference DOD 0.5 and the interim low-health wear multiplier give:")
    s = s.replace('c_{deg}(h)=\\frac{C(h)', 'c_{deg}(h)=\\frac{C_{ref}')
    s = s.replace('Health is clipped to 0.70-1.00. The SOH-independent planner retains 0.05 CNY/kWh. Thus the ablation changes cost representation as well as capacity feedback; a difference at initial SOH=1 cannot be attributed exclusively to capacity degradation. Daily trajectory cost is deducted once in cross-method value comparisons. It is exact only relative to the implemented evaluator, not a physically exact aging measurement.', 'Health is clipped to 0.70-1.00 for this coefficient. The historical SOH-independent planner used 0.05 CNY/kWh, so those early ablations mix coefficient choice and health feedback. The corrected continuous fixed-cost baseline instead freezes the SOH-aware initial coefficient, 0.082495791 CNY/kWh at SOH 0.90. Daily trajectory aging is valued consistently and deducted once for every arm; it remains a model-based evaluation, not measured physical aging.')
    s = s.replace('## 5. Solution and Execution', '''### 4.6. How SOH enters the leader-follower decision

The coupling has three explicit links. First, incoming health sets usable battery energy in the leader's SOC transition and feasible set, Equation (5). Second, health sets the marginal throughput cost subtracted when comparing candidate prices, Equations (7) and (10). Third, the executed trajectory updates health through Equations (8)-(9), and the next daily solve receives that updated state. A post-hoc aging charge alone implements only accounting; it does not implement the first two links. A stored SOH field alone is likewise insufficient evidence unless the capacity or cost used by the solver changes with it.

This is a rolling health-parameterized leader-follower method. Neither a nonzero SOH update nor a changed dispatch proves that the optimal price must change: constraints, rigid demand, discrete prices, and the execution adapter can leave the selected tariff or delivered service unchanged. Component ablations test whether the interface is active; continuous replay tests whether state and accounting remain connected. A responsive-user equilibrium claim requires additional evidence beyond a fixed-request replay.

## 5. Solution and Execution''')
    s = s.replace('The implementation uses Pyomo and HiGHS through appsi.', 'The implementation uses Pyomo and HiGHS through appsi. The continuous common-request runner is a distinct diagnostic: it uses shrinking half-hour perfect-trace horizons within each day and at most two coordinate sweeps. Its recorded acceptance test is an absolute difference of at most 1e-6 CNY between the final two sweep objective values. This objective-stagnation flag is not the historical outer price-demand-SOH residual test, nor proof that every unilateral deviation has been exhausted.')
    s = section(s, '### 5.2. Algorithm 1:', '### 5.2. DC network post-check', '''### 5.2. Algorithm 1: rolling SOH coupling and execution

**Inputs:** dated EV/weather traces or a declared forecast; frozen equipment, tariff grid and penalties; initial SOC and full battery-group state; solver limits and acceptance gates. **Outputs:** price, requests, separately delivered EV/non-EV energy, battery actions, carried health/SOC, cost ledger and failure flags.

1. At the daily boundary, receive the complete physical battery state and SOC from the preceding executed day. Set usable capacity from health. Compute the health-dependent marginal coefficient; in the matched fixed-cost ablation, retain the same initial coefficient instead.

2. At each half-hour decision, assemble the declared horizon and user availability. For each candidate tariff considered by the bounded coordinate search, solve the follower allocation and the leader storage/service MILP using that capacity and coefficient. The responsive-user formulation uses Equations (1)-(4); the common-request diagnostic fixes user energy and availability to isolate the health/dispatch interface.

3. Record the stopping test actually used and every exhausted iteration limit. The historical outer controller uses its component residuals; the continuous diagnostic uses final-sweep objective stagnation. Do not label either as a global equilibrium certificate. If assistance is enabled, a locked Agent supplies only a legal starting price vector to the same search.

4. Execute only the first half-hour action through the physical adapter. Record requested and delivered EV energy, non-EV shortage, conversion and network losses, curtailment, and SOC. Compute realized EV revenue as price times delivered kWh. Preserve the distinction between planned and executed actions.

5. At day end, calculate aging from executed SOC and throughput, update the full battery state, and record stored-energy revaluation when health reduces capacity. Apply declared replacement rules and keep replacement expenditure separate from operating value. Pass the updated state and SOC to the next date without resetting health.

6. Apply the unchanged energy, SOC and solver gates. Retain a failed day's records and status; an accepted prefix is not a completed full-window validation. Any numerical or search repair must be reported as a separate replay or a newly versioned continuous run.

The proposed method requires no learning agent. The supervised warm-start and historical storage-only Q-learning tests in Section 7 are auxiliary negative diagnostics, not evidence that reinforcement learning establishes SOH feasibility. Their training, validation and test boundaries remain separately reported.''')
    s = s.replace('### 5.2. DC network post-check', '### 5.3. DC network post-check')
    s = s.replace('## 7. Results', '''### 6.5. Operational definition of feasibility

Feasibility is assessed at three levels. **Model integration:** SOH must alter a declared solver capacity or objective coefficient, and daily executed actions must feed the next health state. **Numerical execution:** records must satisfy the declared energy/revenue identities, SOC bounds, state-transfer checks and the applicable stopping gate. Load shedding is allowed and reported; numerical feasibility does not mean all station demand is supplied. **Game solution:** a price-responsive follower and an adequately checked leader search are required before making a strategic-response or equilibrium claim. The first two levels are the present study's primary evidence; rigid common-request trajectories do not establish the third. Global convergence, thermal security, measured aging calibration and real-time deployment are outside the demonstrated claim.

## 7. Results''', 1)
    s = s.replace('Sections 7.0–7.0.2 report the corrected date-matched short-window check, locked Agent test, and a continuous three-day diagnostic.', 'Sections 7.0-7.0.4 report the corrected short-window check, auxiliary Agent test, continuous diagnostics and the feasibility evidence boundary.')
    s = section(s, '### 7.0.3.', '### 7.1.', '''### 7.0.3. Planned 90-day diagnostic: accepted prefix and failed stopping gate

The prespecified block is 1 May-29 July 2020, starting at SOC 0.50 and SOH 0.90 with the frozen main ratings. The 18 November 2026 audit found 90 accepted days in the fixed-price arm and 72 consecutive accepted days (1 May-11 July) in each dynamic arm. Both dynamic jobs recorded day 73, 12 July, as failed and stopped. At 02:00 local time, neither met its final-sweep objective-stagnation test within the two-sweep budget. Both day's energy identities still passed (absolute residuals 2.86e-11 and 2.88e-11 kWh). Thus the failure is the recorded search-stopping gate, not energy conservation; no full 90-day dynamic validation is claimed. Failed day 73 is retained and excluded from accepted-prefix totals. Days 74-90 were not executed in those runs.

For the 72 accepted paired dates, all three arms requested 8359.71 kWh and delivered 7406.38 kWh EV energy, with 953.33 kWh unserved. Dynamic fixed-cost and SOH-aware delivered-energy revenue was identical at 17631.69 CNY. SOH-aware minus dynamic fixed cost was -11.81 kWh non-EV unserved energy, -11.83 kWh curtailment plus battery/network losses, +94.04 CNY trajectory aging, and zero replacement expenditure. Modeled operating values were -13,653,476.31 and -13,665,199.25 CNY, respectively. The +11,722.94 CNY difference depends on the assumed 1000 CNY/kWh non-EV shortage valuation; both totals are strongly negative and are not project cash profits.

Revaluing the same paired physical outcomes gives -91.39 CNY at zero non-EV shortage penalty, -32.32 CNY at 5 CNY/kWh and +26.75 CNY at 10 CNY/kWh. The break-even penalty is 7.74 CNY/kWh. These are single-path sensitivity results, not population confidence intervals or an identified welfare benefit. The main feasibility claim does not rely on this conditional value advantage.

Recorded runtimes for the 72 matched dates were 441.58 s for fixed price, 160570.63 s for dynamic fixed cost, and 160701.28 s for SOH-aware. The dynamic-arm difference was +130.65 s. Host pauses and concurrent workload can affect these records; no controlled speedup or demonstrated half-hour deployment capability is inferred. These 72-day totals replace the earlier 69-day timing snapshot rather than being combined with it.

Each dynamic arm's accepted prefix contains 3456 half-hour records, SOC stays in 0.20-0.90, and terminal-SOC failures are zero. The maximum absolute daily energy residual is 2.33e-10 kWh. Prototype voltage reconstruction gives 0.99623-1.01219 pu, inside 0.95-1.05 pu, without a comparative voltage advantage. Every step's thermal verification remains unavailable because branch ampacity limits are unset. Accepted numerical execution does not imply uninterrupted demand satisfaction or surveyed-network security.

### 7.0.4. Evidence that the SOH interface is active

The ledger audit found no mismatch in any adjacent daily full-state or SOC handoff, no error in step revenue = executed price times delivered EV kWh, and no error in incoming usable capacity = 2000 times SOH. The audit is reproducible with `scripts/audit_soh_feasibility.py`; the source hashes and failed-day timestamps are retained in `soh_feasibility_audit_20261118.json`.

In the SOH-aware accepted 72-day path, SOH decreases from 0.900000000 to 0.896640771 and usable capacity from 1800.0000 to 1793.2815 kWh. Its recorded daily marginal coefficient rises from 0.082495791 to 0.082888297 CNY/kWh, whereas the matched dynamic baseline holds 0.082495791 CNY/kWh. The coefficient agrees with Equation (10) to 2.78e-17 CNY/kWh. These changes demonstrate that health affects the next solve's capacity and cost rather than merely being appended to an output table. The continuous contrast isolates cost refresh with common health-dependent capacity; the independent-day capacity-switch ablations separately demonstrate that capacity can alter planned actions.

![Continuous SOH feedback and failed search gate](second_draft_figures/figure_13_soh_feasibility_state_feedback.png)

**Figure 8.** Recorded daily SOH, usable capacity and marginal degradation coefficient in the continuous diagnostic. Curves show the 72 accepted days in each arm; the red crosses denote the retained failed day 73 in the dynamic arms. The fixed-price arm completed 90 days but is truncated here to the same displayed dates. Coefficient refresh is directly observable, while the later search-gate failure limits the reliability claim.

The responsive formulation in Equations (1)-(7) establishes where health enters the leader-follower model, and historical responsive runs show that the implementation can return tariffs and dispatch under that formulation. However, the continuous test uses fixed per-slot requests, so user energy cannot strategically respond to price. Its search-stagnation flag cannot prove a responsive game equilibrium. Existing evidence therefore supports model integration and bounded numerical executability. Robust price-responsive convergence, reliable completion of the entire 90-day dynamic block, and battery-lifetime or deployment benefits remain unestablished.''')
    s = s.replace('The continuous 69-day comparison supplies a longer matched trajectory, but the cost result remains dominated by an assumed non-EV shortage penalty and does not increase EV service.', 'The continuous 72-day accepted prefix supplies direct state-transfer and coefficient-refresh evidence. Its day-73 search-gate failure limits numerical reliability, while its conditional value difference remains dominated by the non-EV shortage valuation and does not increase EV service.')
    s = s.replace('A defensible continuous-life study should next finish this 90-day block,', 'Before a continuous-life study, the day-73 stopping failure must be diagnosed and any repaired search validated on a separately versioned continuous path without relabeling the original failure. A subsequent study should complete the 90-day block,')
    s = section(s, '## 9. Conclusions', '## Data and Code Availability', '''## 9. Conclusions

This study presents a method for incorporating battery SOH into the operator side of a rolling leader-follower charging-price and storage-dispatch model. Health determines usable capacity and marginal degradation cost before scheduling; executed SOC and throughput update the daily physical state that is passed to the next solve. SOH is therefore an operational input as well as an accounting output. The proposed contribution is this explicit coupling and its auditable execution, rather than a reinforcement-learning controller or a claim that health awareness must improve profit.

Evidence supports a bounded feasibility conclusion. The 144-case independent-day matrix has 136 gate passes and eight energy-gate failures; its capacity switches can change planned actions, while its fixed daily health cannot identify cross-day cost feedback. The continuous common-request diagnostic provides 72 consecutive accepted dynamic days, 3456 half-hour records per arm with SOC/energy checks, exact delivered-revenue integration and uninterrupted state handoffs. SOH-aware capacity and marginal cost change with the carried health as specified. Both dynamic jobs fail their coordinate-search stopping gate on day 73, so full-window reliability is not demonstrated. Fixed requests also mean that this diagnostic does not establish strategic tariff responsiveness or equilibrium convergence.

The accepted prefix shows no EV delivery or revenue gain from refreshed SOH cost. Its conditional modeled-value difference reverses below a 7.74 CNY/kWh non-EV shortage penalty and accompanies higher aging cost. Voltage checks apply only to an assumed prototype and thermal safety remains unverified. The method is computationally implementable over the accepted tested domain; global equilibrium, uninterrupted service, calibrated lifetime gains and real-time deployment remain beyond the evidence. The next method-specific validation is a traceable repair and re-test of the bounded search, followed by continuous price-responsive tests with matched information and unchanged physical ratings. Auxiliary Agent tests currently provide no acceleration evidence and are not required for this method's feasibility claim.''')
    return s


def revise_zh(s):
    s = s.replace(s.splitlines()[0], '# 将电池SOH纳入高速公路孤岛充电微电网滚动主从定价与调度：可行性研究', 1)
    a = s.index('中文翻译日期：')
    b = s.index('## 摘要',a)
    s = s[:a] + '修订日期：2026年11月18日。本版与同日英文稿同步，以SOH接入方法及其可行性证据为主线。图片保留英文，参考文献保留原文；CNY表示人民币元。连续实验采用72个通过日及第73天失败记录的最新审计。\n\n' + s[b:]
    s = section(s, '## 摘要', '**关键词：', '''## 摘要

本文提出将电池健康状态（SOH）纳入高速公路孤岛充电微电网滚动主从定价与储能调度的方法。SOH改变运营者的可用容量约束和边际退化成本，执行轨迹在日边界更新物理健康状态。可行性依据来自机制消融、能量与交付收入恒等式及连续状态传递检查，而非预设经济优势。冻结系统为400 kW光伏、100 kW风电、2000 kWh/800 kW储能及8×60 kW充电桩，采用日期匹配ERA5物理驱动。144个独立日消融中136例通过全部门槛、8例累计能量失败。计划90天的共同需求诊断中，两个动态组均有72个连续通过日，每组3456个半小时记录满足能量和SOC检查。SOH-aware组健康由0.900000降至0.896641，边际系数由0.082496升至0.082888元/kWh，显示跨日反馈确实生效。两个动态组均在第73天未通过坐标搜索停止判据，因此完整窗口求解可靠性尚未建立。连续诊断固定请求，只验证健康相关调度与核算，不能验证策略性价格响应或均衡。条件性模型价值增益不证明普遍盈利或寿命改善。现有证据支持在已通过测试范围内计算实现SOH耦合方法，并明确其收敛与部署边界。''')
    s = s.replace('本文研究日尺度健康反馈能否一致地接入滚动定价与储能决策，以及现有证据支持哪些结论。工作包括：实现运行决策与SOH相关容量及边际成本的耦合；在共同轨迹核算下开展三类SOH消融；通过受控能量审计区分有用可再生电量交付与储能损耗。在检查表面改善的来源后，再开展有限的学习控制器诊断。', '''本文的核心问题是：SOH能否在调度前进入运营者决策，并将执行后果一致传递至下一次决策。方法连接两个时间尺度：运行层电价响应与储能决策使用健康相关可行域和退化系数；每日执行SOC与吞吐量更新物理电池状态。SOH是运营者资产的状态，不是新增博弈主体；跟随者仍优化自身充电分配。健康通过储能可行性和成本间接改变运营者对候选电价的评价，不要求用户直接优化电池健康。

贡献包括：（1）将SOH接入滚动主从模型的容量与成本接口；（2）把执行调度连接至后续健康决策的状态传递与核算协议；（3）含失败案例的组件消融和连续账本可行性证据。本文检验声明假设下的模型实现与数值可执行性，不要求健康感知方案优于全部基准。学习辅助仅为附加诊断，不构成方法的核心贡献。''')
    s = s.replace('SOH-aware规划中，有效容量为E_nom乘以暂估健康状态，消融规划则采用额定容量；所有方案在物理执行时均采用健康相关容量。', 'SOH-aware规划使用E_nom乘以健康状态作为有效容量。独立日容量消融可在规划中使用额定容量、在执行中使用健康相关容量；连续匹配成本诊断则在所有组的规划和执行中均使用当前健康相关容量，动态组差异用于识别边际成本刷新及其后续状态路径效应。')
    s = s.replace('规划要求SOC为0.10-0.90、终端SOC不低于0.20；执行在每一步均要求0.20储备。两者可行域不同，首时隙规划放电可能在执行时被削减。', '历史控制器与独立日诊断规划SOC为0.10-0.90、终端最低0.20，执行每步保留0.20储备，故规划放电可能被削减。连续高精度程序则在规划和执行中均使用0.20-0.90、终端最低0.20。上述协议差异分别记录，不合并解释。')
    s = s.replace('轨迹核算的价值系数为日初有效容量乘以1000元/kWh，残值导数为1/0.30。规划边际系数依据附带的暂估健康状态重算，C(h)为该状态有效容量乘以1000元/kWh。采用参考放电深度DOD=0.5及暂定低健康磨损乘数：', '当前连续协议的轨迹老化与规划边际系数采用统一额定置换价值C_ref=2000000元（2000 kWh乘以1000元/kWh），残值导数为1/0.30。C_ref不再随可用容量二次缩减。日边界按输入物理健康刷新系数；历史外层变体另评价暂估健康。单电池组采用参考DOD=0.5与暂定低健康磨损乘数：')
    s = s.replace('c_{deg}(h)=\\frac{C(h)', 'c_{deg}(h)=\\frac{C_{ref}')
    s = s.replace('健康值截断于0.70-1.00。不依赖SOH的历史规划器保留0.05元/kWh。因此该消融同时改变成本表示与容量反馈，初始SOH=1时的差异不能完全归因于容量退化。跨方法价值比较中每日轨迹成本只扣除一次；这种精确性仅相对于实现的评价器而言，并非物理上精确的老化测量。', '该系数中的健康截断于0.70-1.00。历史不含SOH规划器使用0.05元/kWh，故早期消融混合系数选择与健康反馈。修正连续基准冻结SOH-aware初始系数，在SOH=0.90时为0.082495791元/kWh。各组轨迹老化统一估值且每日只扣除一次，仍属于模型评价而非实测老化。')
    s = s.replace('## 5. 求解与执行', '''### 4.6. SOH如何进入主从决策

耦合含三个明确连接：第一，输入健康通过式（5）决定领导者SOC转移及可行域中的可用电池容量；第二，健康通过式（7）和（10）决定比较候选电价时扣除的边际吞吐成本；第三，执行轨迹通过式（8）-（9）更新健康，后续日求解接收该状态。仅事后计老化只实现核算，未实现前两个连接；仅保存SOH字段也不足以证明接入，必须确认求解器实际使用的容量或成本随之变化。

该方法属于健康参数化的滚动主从决策。SOH变化或调度改变均不意味着最优电价必须改变：刚性需求、约束、离散电价与执行适配器均可能使电价或服务保持一致。组件消融检验接口是否生效，连续回放检验状态与核算是否连通。价格响应均衡主张还需要固定请求回放之外的证据。

## 5. 求解与执行''')
    s = s.replace('实现使用Pyomo，并通过appsi调用HiGHS。', '实现使用Pyomo，并通过appsi调用HiGHS。连续共同需求程序是另一诊断协议：采用日内逐步缩短的半小时完全轨迹预测，最多两轮坐标扫描，记录的接受条件是末两轮目标值绝对差不超过1e-6元。该目标停滞标记不同于历史外层电价-需求-SOH残差，也不能证明全部单边偏离已充分检查。')
    s = section(s, '### 5.2. 算法1：', '### 5.3. 直流网络事后检查', '''### 5.2. 算法1：滚动SOH耦合与执行

**输入：** 日期匹配EV/气象轨迹或声明预测；冻结设备、电价网格和罚值；初始SOC、完整电池组状态、求解预算与接受门槛。**输出：** 电价、请求、EV/非EV独立交付量、储能动作、传递健康/SOC、成本账本与失败标记。

1. 日边界接收前一执行日完整物理电池状态与SOC，由健康计算可用容量及边际系数；匹配固定成本消融则保留相同初始系数。

2. 每半小时组装声明时域与用户可用窗口。有限坐标搜索评价每个候选电价时，在上述容量和系数下求解跟随者分配及领导者储能/服务MILP。响应模型使用式（1）-（4）；共同需求诊断固定用户电量及可用时隙，以隔离健康-调度接口。

3. 记录实际采用的停止判据与迭代预算耗尽。历史外层用分量残差，连续诊断用末轮目标停滞；均不标为全局均衡证明。若启用辅助，锁定Agent只向相同搜索提供合法初始电价。

4. 经物理适配器只执行首个半小时动作。记录EV请求/交付、非EV缺供、电池和网络损耗、弃电及SOC。实际EV收入等于执行电价乘以交付kWh，保留规划与执行动作区别。

5. 日末依据执行SOC与吞吐量计算老化，更新完整电池状态，单列健康降低容量导致的储能重估。执行声明的置换规则，置换支出与运行价值分列。将更新状态与SOC传至下一日，不重置健康。

6. 按不变的能量、SOC及求解门槛检查。保留失败日及其状态，通过前缀不等于完整窗口验证。数值或搜索修复必须作为独立回放或新版本连续轨迹报告。

该方法无需学习Agent。第7节监督热启动与历史储能Q-learning仅为附加负结果诊断，不用于证明强化学习建立SOH可行性；训练、验证、测试边界另行报告。''')
    s = s.replace('## 7. 结果', '''### 6.5. 可行性的操作性定义

分三个层次评价。**模型接入：** SOH必须改变声明的求解容量或目标系数，每日执行动作必须反馈下一健康状态。**数值执行：** 记录满足能量/收入恒等式、SOC边界、状态传递及适用停止门槛。模型允许缺供并如实报告，数值可行不等于满足站点全部需求。**博弈求解：** 策略响应或均衡主张需要具有电价响应的跟随者及充分检查的领导者搜索。当前主要证据覆盖前两个层次，刚性共同请求轨迹不能建立第三层。全局收敛、热安全、实测老化校准及实时部署均超出现有证明范围。

## 7. 结果''', 1)
    s = s.replace('第7.0-7.0.3节给出修正后的日期匹配短窗检查、锁定Agent测试、连续三天诊断及连续90天阶段审计。', '第7.0-7.0.4节给出修正短窗、附加Agent、连续诊断及可行性证据边界。')
    s = section(s, '### 7.0.3.', '### 7.1.', '''### 7.0.3. 计划90天诊断：通过前缀与停止门槛失败

预设块为2020年5月1日至7月29日，初始SOC=0.50、SOH=0.90，主容量冻结。2026年11月18日审计发现固定组90天通过，两个动态组各有72个连续通过日（5月1日至7月11日）。二者均将第73天7月12日记录为失败并停止：本地02:00在两轮扫描预算内未满足末轮目标停滞判据；两组当日能量残差仍通过，分别2.86e-11与2.88e-11 kWh。因此失败属于搜索停止门槛，并非能量不守恒；不能宣称完整90天动态验证。第73天保留且不纳入通过前缀总量，第74-90天未在这些运行中执行。

72个通过配对日中，三组EV请求8359.71 kWh、交付7406.38 kWh、未服务953.33 kWh。两个动态组实际交付收入均17631.69元。SOH-aware减动态固定成本为：非EV缺供-11.81 kWh，弃电加电池/网络损耗-11.83 kWh，轨迹老化+94.04元，置换支出差为零。模型运行价值分别-13653476.31元与-13665199.25元。+11722.94元差值依赖假设的1000元/kWh非EV缺供估值，两总值均大幅为负，不是项目现金利润。

对同一物理结果重估，零非EV罚值时差-91.39元，5元/kWh时-32.32元，10元/kWh时+26.75元，盈亏分界7.74元/kWh。该单轨迹敏感性不是总体置信区间或已识别福利收益，核心可行性结论不依赖条件性价值优势。

72个匹配日记录时间：固定441.58 s、动态固定成本160570.63 s、SOH-aware 160701.28 s，动态差+130.65 s。主机暂停与并发负载影响记录，不能推断受控加速或已具半小时部署能力。这些值替代旧69天时间快照，不与旧快照合并。

各动态组通过前缀含3456个半小时，SOC为0.20-0.90，终端SOC失败为零，最大日能量残差2.33e-10 kWh。原型电压重构0.99623-1.01219 pu，位于0.95-1.05 pu内，无相对电压优势。所有步骤均因未设置支路载流额定值而热安全未验证。数值执行通过不表示无中断满足需求或实测网络安全。

### 7.0.4. SOH接口确实生效的依据

账本审计未发现相邻日完整状态或SOC传递不一致；逐步收入等于执行电价乘以实际交付EV电量，误差为零；日初可用容量等于2000乘以SOH，误差为零。可用`scripts/audit_soh_feasibility.py`复算，源哈希与失败时间戳保存在`soh_feasibility_audit_20261118.json`。

SOH-aware的72天通过路径中，健康0.900000000降至0.896640771，可用容量1800.0000降至1793.2815 kWh。日边际系数0.082495791升至0.082888297元/kWh；匹配动态基准保持0.082495791元/kWh。系数与式（10）误差不超过2.78e-17元/kWh，说明健康实际影响下次求解容量和成本，并非只附在输出表格中。连续对照在共同健康相关容量下隔离成本刷新；独立日容量开关消融另显示容量能够改变规划动作。

![连续SOH反馈与搜索门槛失败](second_draft_figures/figure_13_soh_feasibility_state_feedback.png)

**图8.** 连续诊断记录的每日SOH、可用容量与边际退化系数。曲线展示各组72个通过日，红叉表示两个动态组保留的第73天失败记录。固定组完成90天，此处仅显示相同日期。系数刷新可直接观察，后续搜索门槛失败限制可靠性结论。

式（1）-（7）的响应模型说明健康进入主从模型的位置，历史响应运行显示实现可返回电价与调度。但连续测试固定各时隙请求，用户电量无法策略性响应价格，搜索停滞标记不能证明响应博弈均衡。因此现有证据支持模型接入及限定范围数值可执行性，尚未建立稳健价格响应收敛、整个90天动态完成、寿命收益或部署收益。''')
    s = s.replace('69天连续比较提供更长匹配轨迹，但成本结果仍由假设的非EV缺供罚项主导，EV服务未增加。', '72天通过前缀提供直接状态传递与系数刷新证据，但第73天搜索门槛失败限制数值可靠性；条件性价值仍由非EV罚值主导，EV服务未增加。')
    s = s.replace('后续应先完成90天块，再', '连续寿命研究前应先诊断第73天停止失败，并在独立版本连续路径验证搜索修复，不改判原失败。之后完成90天块，再')
    s = section(s, '## 9. 结论', '## 数据与代码可用性', '''## 9. 结论

本文提出在滚动主从充电定价与储能调度中向运营者一侧接入SOH的方法。健康在调度前决定可用容量与边际退化成本；执行SOC与吞吐量更新日尺度物理状态，并传至下一次求解。因此SOH同时是运行输入和核算输出。贡献是明确的耦合及可审计执行，而非强化学习控制器或健康感知必然增利。

证据支持限定范围的可行性结论。144例独立日矩阵136例通过、8例能量失败；容量开关可改变规划动作，日内固定健康不能识别跨日成本反馈。共同请求连续诊断提供72个动态连续通过日，每组3456个半小时满足SOC/能量检查，实际收入积分一致、状态传递无断裂，SOH-aware容量与边际成本随健康按规定变化。两个动态任务第73天均未通过坐标搜索停止门槛，完整窗口可靠性未证明；固定请求也使该诊断不能建立策略电价响应或均衡收敛。

通过前缀没有EV交付或收入增益，条件性模型价值差在非EV罚值低于7.74元/kWh时反转，并伴随更高老化。电压检查只适用于假设原型，热安全未验证。方法在已通过测试范围内可计算实现；全局均衡、无中断服务、经校准寿命改善和实时部署均超出现有证据。下一项方法验证应对有限搜索进行可追溯修复和重测，再以匹配信息、固定物理容量开展连续价格响应实验。附加Agent测试尚无加速证据，也不是该方法可行性的必要条件。''')
    return s


if __name__ == '__main__':
    backup = ROOT / 'review_snapshots' / 'before_soh_feasibility_20261118'
    backup.mkdir(parents=True, exist_ok=True)
    for name, revise in [('WEVJ_second_draft.md', revise_en),
                         ('WEVJ_second_draft_zh.md', revise_zh)]:
        path = ROOT / name
        if not (backup / name).exists():
            shutil.copy2(path, backup / name)
        path.write_text(revise(path.read_text(encoding='utf8')), encoding='utf8')
        print('Revised', name)
