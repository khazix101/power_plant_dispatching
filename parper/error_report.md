# 错误与修改报告（复核版）

**项目**：离网直流微网动态 SOC 主从博弈调度
**分支**：`research/dc-microgrid-dynamic-soc`
**报告状态**：仅诊断，未修改代码
**参数口径（已确认）**：单桩 60 kW、8 桩、EV 装机 480 kW、BESS 2000 kWh/800 kW、PV 400 kW、Wind 100 kW、SOC 10–90%、备用 20%、非 EV 50–100 kW、主算例用观测尺度

---

## 一、阻断性问题（必须先修，否则结果不可用）

### E01 直流电导矩阵未做标幺化

- **位置**：`src/game/dc_microgrid.py` 电导矩阵构建
- **现象**：`y = 1.0 / resistance_ohm` 得到 200 S，直接代入 `p_inj/p_base = G·Δu`
- **应为**：`Z_base = V_base^2 / S_base = 0.4^2 / 1 = 0.16 Ω`，`R = 0.005 Ω = 0.03125 pu`，`G = 32 pu`
- **影响**：电压跌落与电流被低估 6.25 倍；800 kW 负荷下算出网损为 0、电压 1.00 pu
- **修复**：`G_pu = (1/R) × Z_base`
- **验证**：480 kW 负荷 → `I = 1200 A`、`ΔV = 6 V`（1.5%）

### E02 网损公式单位错误

- **位置**：`src/game/dc_microgrid.py` 支路损耗计算
- **现象**：`loss_kw = (current_a/1000)**2 * R`
- **问题**：把 kA²·Ω 直接当作 kW，结果比真实值小 1000 倍
- **应为**：`loss_kw = current_a**2 * R / 1000`
- **验证**：1200 A、0.005 Ω → 7.2 kW（现值 0.0072）

### E03 SOC 容量随 SOH 重复相乘

- **位置**：`src/game/dynamic_dc.py` 的 `step`
- **现象**：`self.soc.config.capacity_kwh = self.soc_config.capacity_kwh * self.soh`
- **问题**：`soc.config` 与 `soc_config` 为同一对象，每次 step 在已缩放值上再乘一次
- **影响**：SOH=0.99 时容量按 2000×0.99^n 复利衰减，SOC 轨迹失真
- **修复**：单独保存 `base_capacity_kwh`，每次由基准值计算
- **验证**：连续 10 步同 SOH=0.99，容量应恒为 1980 kWh

### E04 未来 24 h EV 负荷未进入需求模型

- **位置**：`src/game/dynamic_dc.py` 的 `_build_case`
- **现象**：只构造"当前需求"与"积压需求"两类用户，`ev_base[1:h]` 从未使用
- **影响**：运营商无法为次日 EV 高峰预留储能，"预测驱动"名不副实
- **修复**：按小时构造未来窗口需求用户（含能量上下限）
- **验证**：构造未来高峰场景，检查 BESS 是否提前充电

### E05 价格上限高于用户价值，积压需求永不服务

- **位置**：`price_levels` 最高 1.8；`EVUser.value_per_kwh = 1.55`
- **证据**：`run_dynamic_dc.py` 输出 6 小时全部 `price=1.80`，`EV=180/180`（仅当前需求）
- **机理**：当前需求 `energy_min = energy_max` 且仅 `available=(0,)`，完全无弹性 → 领导者取最高价；积压用户价值 1.55 < 1.8 → 永不充电
- **影响**：价格响应退化为"只服务刚性需求"，论文核心机制失效
- **修复**：
  1. 已成交的当前需求不再进入定价博弈，改计入基础负荷；
  2. 用户价值按时段异质化（如 1.15/1.35/1.55），避免单一价值导致角点解；
  3. 后续可增加"均价约束"（参考论文的 RPP 0.8–1.2 倍批发电价）
- **验证**：检查价格不恒为上限、积压需求能收敛

### E06 `C.csv` 规模与真实数据相差 20–100 倍

- **证据**：C 峰值 4000 kW、日 38,750 kWh；真实 C 站 1 min 峰值 300.5 kW
- **佐证**：24 h 实验在 4000 kW 下 `需求 38,750 / 服务 26,220 / 失负荷 12,530（32.3%）/ 弃电 10,350 kWh`
- **结论**：4000 kW 与 480 kW 装机、800 kW BESS 物理上不自洽
- **修复**：主算例改用观测尺度（峰值 300 kW），上限 480 kW

### E07 `A.csv`、`B.csv` 是人工典型曲线

- **证据**：全部为 50/100 整数倍；仅 2024-01-01 一天；首个提交说明为"24h 示例预测数据"；无生成脚本
- **修复**：接入现有预测模块；若暂不能，必须标注为"典型曲线假设"

### E08 "每桩 120 kW"与数据矛盾

- **证据**：会话平均功率 60–120 kW 区间 0 条；16 桩上限均落在 53–58 kW
- **修复**：桩额定 60 kW，装机 480 kW，单车上限 60 kW

### E09 EV 功率上限仍是 800 kW

- **位置**：`dynamic_dc.py` 的 `ev_service_limit_kw=800.0`
- **修复**：改为 480，并在 `balance_dc_step` 统一使用

### E10 削减/限发后未重算功率平衡

- **位置**：`dc_microgrid.py` 的 `balance_dc_step`
- **现象**：调整 `ev_served` 后重新求解潮流得到新网损，但 `p_bess` 与残差未重算
- **修复**：二次求解后重算 residual，必要时迭代至收敛

### E11 `max_power_kw` 用能量值顶替功率

- **位置**：`dynamic_dc.py` 的 `_build_case`
- **现象**：`max_power_kw=max(energy/dt,1.0)`，语义错误且未按 60 kW/桩约束
- **修复**：单车 60 kW、全站 480 kW，显式分开

### E12 电压越限不阻断

- **位置**：`dc_microgrid.py` 仅返回 `voltage_feasible` 标志
- **问题**：调度层不使用该标志，可能接受越限解
- **修复**：越限时触发削减或标记该时步不可行

---

## 二、高优先级问题

| 编号 | 问题 | 证据/位置 | 建议 |
|---|---|---|---|
| E13 | 缺少价格承诺机制 | 已成交用户价格每轮被重新优化 | 区分"已承诺价格"与"未来价格" |
| E14 | 剩余需求会计口径混乱 | `remaining += ev[0] - served`，`served` 含积压服务 | 分列"当前需求/积压需求"两本账 |
| E15 | 节假日年份错配 | `E.csv` 为 2024 年；充电数据 2020–2021 | 重新生成对应年份日历 |
| E16 | 风光预测不可复现 | GRIB 缺辐照度；风电缺功率曲线 | 接入现有预测模块或补充数据源 |
| E17 | 线路热约束未验证 | 无 NormAmps，`thermal_limits_verified=False` | 论文中不得声明热约束通过 |
| E18 | 三站数据混用单站模型 | 真实数据含 A/B/C 三站 | 明确只取 C 站，或建三站模型 |

---

## 三、中低优先级问题

| 编号 | 问题 | 建议 |
|---|---|---|
| E19 | `对接文档.md` 仍描述已删除的 `src/main.py` | 更新文档或保留兼容入口 |
| E20 | 工作区有未提交的参考 PDF 删除与大量未跟踪文件 | 复核是否误删，明确 git 策略 |
| E21 | 测试未覆盖新口径 | 增加 480/60/网损/并发用例 |
| E22 | 146.5 kW 异常会话未入清洗规则 | 显式排除并记录规则 |

---

## 四、参数口径统一表（修复后应对齐）

| 符号 | 值 | 含义 |
|---|---:|---|
| `P_post` | 60 kW | 单桩额定 |
| `N_post` | 8 | 桩数 |
| `P_EV_cap` | 480 kW | 全站装机上限 |
| `P_obs_peak` | 300.5 kW | 观测 1 min 峰值（62.6%） |
| `P_obs_1h` | 226.0 kW | 观测小时峰值 |
| `P_design` | 420 kW | 8×60×0.875（p90 同时率） |
| `P_nonEV` | 50–100 kW | 假设 + 敏感性 |
| `P_loss` | ≈12.5 kW @480 kW | 约 2.6% |
| `P_PCS_rated` | 800 kW | 储能变流器 |
| `E_B` | 2000 kWh | 储能容量 |

修复后校验：`480 + 100 + 12.5 = 592.5 kW < 800 kW`，可覆盖。

---

## 五、数据替换计划

| 文件 | 现状 | 替换方案 |
|---|---|---|
| `C.csv` | 人工 4000 kW 曲线 | 用 `Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv` 的 C 站 `normal_energy_kwh`，上限 480 kW |
| `A.csv` / `B.csv` | 人工典型曲线 | 接入现有预测模块输出；短期以典型曲线 + 显式标注过渡 |
| `D.csv` | 2024 样例天气 | 用 `Weather_Data.csv`/GRIB 小时温度与天气特征 |
| `E.csv` | 2024 节假日 | 重新生成 2020–2021 节假日 |

---

## 六、文件级修改清单

| 文件 | 修改内容 |
|---|---|
| `src/game/dc_microgrid.py` | E01、E02、E10、E12；新增 `ev_rated_kw=480`、`ev_post_kw=60`、`ev_posts=8` |
| `src/game/dynamic_dc.py` | E03、E04、E05、E09、E11、E13、E14 |
| `src/game/case.py` | `EVUser.max_power_kw` 语义与单位；BESS/装机字段 |
| `src/game/forecast_provider.py` | 用真实标定负荷替代 C.csv；非 EV 独立传参 |
| `scripts/run_24h_dc_experiment.py` | 切换 provider；输出装机利用率与三口径峰值 |
| `parper/model_freeze.md` | 写入 60 kW/480 kW 规模链与证据 |
| `parper/experiment_plan.md` | 更新场景、容量一致性、敏感性 |
| `parper/paper_outline.md` | 明确"观测值/设计值/装机值"三口径 |
| `tests/` | 新增 E01/E02/E03/E09/E11 回归测试 |

---

## 七、给复核人的检查清单

1. 用解析值核对电导矩阵：`R=0.005 Ω`、`Z_base=0.16 Ω`，`G` 应为 32 pu 而非 200
2. 核对网损：1200 A、0.005 Ω → 7.2 kW
3. 连续 10 步同 SOH，检查容量是否恒定
4. 检查 24 h 内价格是否仍全为 1.80
5. 检查未来小时 EV 需求是否真正进入优化
6. 全文搜索 `800`，确认 EV 上限与 PCS 额定未被混用
7. 抽查 `data/C.csv` 是否仍被主流程读取
8. 确认 `thermal_limits_verified` 在论文中未被表述为"通过"

---

## 八、未决事项

1. 现有预测模块的输出目标是否包含 PV/Wind 功率？目前脚本目标是"输出功率、需求车辆数"，需确认能否直接映射。
2. 非 EV 负荷 50/100 kW 如何选取，是否分时段曲线？
3. `E.csv` 节假日是否只需保证主算例日期覆盖即可？

---

## 九、修复状态（第一轮）

| 编号 | 状态 | 处理方式 | 验证 |
|---|---|---|---|
| E01 | 已修 | `dc_microgrid.py` 电导矩阵改为 `G_pu = Z_base / R` | `test_z_base_and_conductance_are_per_unit` |
| E02 | 已修 | 网损改为 `I^2·R/1000` | 同上，480 kW → 7.2 kW |
| E03 | 已修 | 新增 `base_capacity_kwh`，每步由基准缩放 | `test_soh_scaling_does_not_compound` |
| E04 | 已修 | 未来逐小时需求用户 + 积压需求进入博弈 | `test_current_hour_ev_load_enters_leader_balance` |
| E05 | 部分修 | 当前小时需求移出定价；未来需求按价值弹性；价格不再恒为 1.8 | 24 h 实验价格 1.2，非角点 |
| E06 | 部分修 | 新增 `CalibratedForecastProvider`，24 h 实验改用真实标定负荷 | 日需求 1813.7 kWh（观测尺度） |
| E07 | 未修 | A/B 仍为典型曲线，已在 provider 注释标注 | — |
| E08 | 已修 | `ev_post_kw=60`、`ev_posts=8` | `test_ev_rating_is_posts_times_post_power` |
| E09 | 已修 | 默认上限取 `dc_config.ev_rated_kw`（480） | `test_balance_enforces_ev_capacity_limit` |
| E10 | 已修 | `balance_dc_step` 迭代至残差收敛 | `test_balance_uses_bess_to_cover_network_losses` |
| E11 | 已修 | 聚合用户 `max_power_kw` 用装机上限，不再用能量值 | 代码审查 |
| E12 | 部分修 | 输出 `voltage_feasible`、`unserved_rigid_kw`；未做越限强制削减 | `test_reserve_floor_blocks_discharge_and_reports_unserved` |
| E23（新增） | 已修 | 固定电价基线用户改为仅在本小时可用，消除角点解 | 24 h 基线由 777.8 → 1813.7 kWh 服务量 |

### 第一轮修复后的 24 h 结果（真实标定负荷，观测尺度）

| 方法 | EV 需求 | EV 服务 | 失负荷 | 弃电 | 末 SOC | 最大线路电流 |
|---|---:|---:|---:|---:|---:|---:|
| 动态 SFSE | 1813.7 | 1813.7 | 0.0 | 725.4 | 0.542 | 1000 A |
| 固定电价 + 最优 BESS | 1813.7 | 1813.7 | 0.0 | 575.5 | 0.613 | 1000 A |
| 无储能 | 1813.7 | 1056.5 | 757.2 | 1675.8 | — | — |

说明：

- 失负荷归零，说明当前 SOC/BESS 足以覆盖观测尺度负荷；
- 动态 SFSE 的弃电高于固定电价基线、末 SOC 略低，价格仍恒为 1.2，说明定价机制尚未产生有效的时段差异化，属于 E05/E13 的后续工作；
- 线路无额定电流，1000 A 仅为实测值，`thermal_limits_verified=False`。
