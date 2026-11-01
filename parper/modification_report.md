# 代码修改报告

**提交**：`c7d0798 fix: correct DC per-unit physics and dynamic SOC accounting`
**分支**：`research/dc-microgrid-dynamic-soc`
**改动规模**：8 个文件，+906 / −84 行
**验证**：`parper/test/run_paper_tests.py` 15 项全部通过

---

## 一、改动文件清单

| 文件 | 状态 | 增删 | 改动性质 |
|---|---|---:|---|
| `src/game/dc_microgrid.py` | 修改 | +90 / −25 | 标幺化、网损公式、平衡迭代、EV 刚性需求 |
| `src/game/dynamic_dc.py` | 修改 | +131 / −38 | SOH 容量、未来需求建模、积压记账、报告口径 |
| `src/game/forecast_provider.py` | 新增 | +165 / 0 | `CalibratedForecastProvider` |
| `scripts/run_dynamic_dc.py` | 修改 | +44 / −14 | 观测尺度 24 h 滚动示例 |
| `scripts/run_24h_dc_experiment.py` | 新增入库 | +194 / 0 | 改用标定负荷与 480 kW 口径 |
| `parper/error_report.md` | 新增 | +221 / 0 | 错误与修复报告 |
| `parper/model_freeze.md` | 修改 | +30 / −2 | 冻结 60 kW/480 kW 规模链 |
| `parper/test/run_paper_tests.py` | 修改 | +31 / −5 | 接入 7 项物理回归测试 |

> `local/tests/tests/test_dc_physics.py`（新增 7 项测试，约 110 行）因 `local/` 被 `.gitignore` 排除，未进入提交。

---

## 二、逐文件变更明细

### 1. `src/game/dc_microgrid.py`

#### (1) 配置新增规模字段

```python
# 之前：无 EV 装机概念
# 之后：
ev_posts: int = 8
ev_post_kw: float = 60.0

@property
def ev_rated_kw(self) -> float:
    return float(self.ev_posts) * float(self.ev_post_kw)   # 480 kW

@property
def z_base_ohm(self) -> float:
    return (self.base_voltage_kv ** 2) / (self.base_power_kw / 1000.0)  # 0.16 Ω
```

#### (2) 电导矩阵标幺化（E01）

```python
# 之前
y = 1.0 / branch.resistance_ohm          # 200 S，未标幺

# 之后
z_base = self.config.z_base_ohm
y = z_base / branch.resistance_ohm      # 32 pu
```

影响：480 kW 负荷的电压跌落由 0.4% 修正为 1.5%，电流由 320 A 修正为 1200 A。

#### (3) 网损单位（E02）

```python
# 之前
loss_kw = (current_a / 1000) ** 2 * branch.resistance_ohm   # 小 1000 倍

# 之后
loss_kw = current_a ** 2 * branch.resistance_ohm / 1000.0   # 480 kW → 7.2 kW
```

#### (4) 平衡函数重写

```python
def balance_dc_step(..., ev_service_limit_kw=None, ev_must_serve_kw=0.0):
```

- 默认上限改为 `config.ev_rated_kw`（480 kW），不再允许 800 kW；
- 新增 `ev_must_serve_kw`：已承诺、不可削减的 EV 负荷；
- 残差闭环迭代最多 6 次：
  1. 先让 BESS 增发补足（含网损）；
  2. 再削减柔性 EV；
  3. 刚性需求不足时记入 `unserved_rigid_kw`，不伪装为已服务；
- 盈余时先限发风光，再降低 BESS 充电功率；
- 新增返回字段：`ev_limit_kw`、`ev_must_serve_kw`、`losses_kw`、`residual_kw`、`unserved_rigid_kw`、`voltage_feasible`。

### 2. `src/game/dynamic_dc.py`

#### (1) 默认上限与基准容量（E03/E09）

```python
# 之前
ev_service_limit_kw=800.0
self.soc.config.capacity_kwh = self.soc_config.capacity_kwh * self.soh  # 复利衰减

# 之后
ev_service_limit_kw=None  -> self.dc_config.ev_rated_kw   # 480
self.base_capacity_kwh = float(self.soc_config.capacity_kwh)
self.soc.config.capacity_kwh = self.base_capacity_kwh * self.soh
```

#### (2) 需求建模重写（E04/E05/E11）

```python
# 之前：仅“当前需求(刚性)” + “积压”两类用户，未来 24h 负荷完全未使用
# 之后：
#   - 当前小时 ev[0] 计入 base_load（刚性、不参与定价）
#   - 未来每小时一个用户，value 轮换 1.55/1.35/1.15，窗口 (t-1, t, t+1)
#   - 未来用户 energy_min=0（价格弹性），max_power_kw=480（不再用能量值）
#   - 积压需求窗口含当前小时，energy_min=(1-flex)*积压
```

#### (3) 执行与记账

```python
requested_kw = float(ev[0]) + flexible_kw
flow = balance_dc_step(..., ev_request_kw=requested_kw,
                       ev_must_serve_kw=float(ev[0]), non_ev_kw=non_ev[0])
```

- 报告口径：`ev_served_kw = served - unserved_rigid_kw`，不再把未供的刚性需求算作已服务；
- 积压两本账：`remaining = max(0, remaining - backlog_served + current_shortfall)`，上限 6 小时满功率；
- `ev_shed_kw` 改为 `ev_shed + unserved_rigid`。

### 3. `src/game/forecast_provider.py`（新增）

```python
class CalibratedForecastProvider:
    # 读取 Dataset/processed/ev_calibration/expressway_hourly_ev_load.csv
    # 指定站点 + 日期 -> 24h normal_energy_kwh（观测尺度）
    # PV/Wind 暂用 400/100 kW 典型曲线，注释标明为阶段性假设
```

同时保留原有 `DailyCsvForecastProvider` 与 `CallableForecastProvider`。

### 4. `scripts/run_dynamic_dc.py`

- 从 6 步固定数组改为 24 h 观测尺度曲线（EV 峰值 229 kW）；
- 逐小时前移 6 h 窗口，`_window` 处理日末越界补零；
- 传入 `current_ev_load_kw`，输出新增 `loss`。

### 5. `scripts/run_24h_dc_experiment.py`

- provider 由 `DailyCsvForecastProvider` 改为 `CalibratedForecastProvider`；
- 每步传入 `current_ev_load_kw=provider.ev_load[hour]`；
- 基线用户 `max_power_kw` 由“能量值”改为 480 kW；
- 基线用户可用时段由“全天”改为“仅本小时”，消除角点解（新增问题 E23）。

### 6. 文档与测试

- `parper/error_report.md`：新增 22 条问题、参数口径表、复核检查清单、修复状态表；
- `parper/model_freeze.md`：冻结 60 kW × 8 桩 = 480 kW、三种峰值口径、标幺与网损公式；
- `parper/test/run_paper_tests.py`：新增 `test_dc_physics` 的 7 项执行。

---

## 三、新增的 7 项回归测试

| 测试 | 覆盖问题 | 断言 |
|---|---|---|
| `test_z_base_and_conductance_are_per_unit` | E01/E02 | 480 kW → 1200 A、7.2 kW、Vmin 0.985 |
| `test_ev_rating_is_posts_times_post_power` | E08 | 8 × 60 = 480 kW |
| `test_balance_enforces_ev_capacity_limit` | E09 | 800 kW 请求被截断至 480 kW |
| `test_balance_uses_bess_to_cover_network_losses` | E10 | BESS 补足网损，残差 < 1e-6 |
| `test_reserve_floor_blocks_discharge_and_reports_unserved` | E12 | SOC=0.20 时放电为 0，缺额显式记录 |
| `test_soh_scaling_does_not_compound` | E03 | 连续两步容量恒为 1980 kWh |
| `test_current_hour_ev_load_enters_leader_balance` | E04/E05 | 无风光时 BESS 放电而非削减 |

---

## 四、修复前后行为对比

### 单站 480 kW 潮流

| 指标 | 修复前 | 修复后 | 解析值 |
|---|---:|---:|---:|
| 支路电流 | 320 A | 1200 A | 1200 A |
| 网损 | ≈0 kW | 7.20 kW | 7.20 kW |
| 最低电压 | 1.000 pu | 0.985 pu | 0.985 pu |

### 24 h 实验（真实标定负荷，观测尺度）

| 方法 | 需求 | 服务 | 失负荷 | 弃电 | 末 SOC |
|---|---:|---:|---:|---:|---:|
| 动态 SFSE | 1813.7 | 1813.7 | 0.0 | 725.4 | 0.542 |
| 固定电价 + 最优 BESS | 1813.7 | 1813.7 | 0.0 | 575.5 | 0.613 |
| 无储能 | 1813.7 | 1056.5 | 757.2 | 1675.8 | — |

修复前同一脚本（使用 4000 kW 人工 `C.csv`）为：需求 38,750 / 服务 800 / 失负荷 37,950。

---

## 五、明确**没有**改动的文件

工作树中以下未提交修改**不是本次改动**，内容属于 SOH 接入与文档同步等既有工作，请勿归入本报告：

- `docs/rolling_sfse.md`（新增 runtime forecast provider 段）
- `parper/test/README.md`（测试路径改为 `local/tests`）
- `src/game/baseline.py`、`case.py`、`equilibrium.py`、`rolling.py`（`effective_capacity_kwh`、`life_weight`、`battery_state` 等 SOH 字段）
- 一批 `参考文献/`、`用户侧主从博弈调研/` 下 PDF/TeX 的删除

---

## 六、验证与回滚

```powershell
D:\miniconda3\python.exe -m compileall -q src scripts parper
D:\miniconda3\python.exe parper/test/run_paper_tests.py      # 15 passed
D:\miniconda3\python.exe scripts/run_dynamic_dc.py
D:\miniconda3\python.exe scripts/run_24h_dc_experiment.py
```

回滚本次改动：

```powershell
git revert c7d0798
```

---

## 七、尚未修复（需确认后再动）

| 编号 | 问题 | 现状 |
|---|---|---|
| E05/E13 | 价格仍恒为 1.20，缺少价格承诺与均价约束 | 未做时段差异化定价 |
| E07/E16 | PV/Wind 仍为 400/100 典型曲线 | 预测模块未接入 |
| E12 | 电压越限只返回标志，不强制削减 | 部分修复 |
| E17 | 线路额定电流缺失 | 仅报告实测电流 |
| E20 | 工作树参考文献删除 | 需你确认是否有意 |
