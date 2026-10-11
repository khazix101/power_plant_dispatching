# 物理离网 GFM QSTS 阶段性模型

## 当前阶段的明确选择

论文和大创阶段暂定采用：

- 单站光伏：`400 kW`；
- 单站风电：`100 kW`；
- GFM BESS：`2000 kWh`；
- PCS 有功额定：`800 kW`；
- PCS 无功额定：`600 kvar`；
- PCS 视在功率额定：`1000 kVA`；
- SOC 物理范围：`10%--90%`；
- 运行备用下限：`20%`；
- 初始 SOC：`50%`；
- 充/放电效率：`95%`；
- 待机损耗：阶段性假设为 `0 kW`；
- 标称频率：`50 Hz`；
- 频率调差范围：`0.5 Hz`；
- 标称电压：`1.0 pu`；
- 电压范围：`0.95--1.05 pu`；
- 电压调差：`0.01 pu`；
- 下垂模式：`P-f/Q-V`，配置名为 `X_gt_R`；
- 不使用虚拟阻抗；
- 不使用外部电网补给。

`P-f/Q-V` 是当前阶段的指定控制策略，不表示已经由现场馈线 R/X 严格识别。`model/islanded_gfm.dss` 使用典型低压线路参数作为阶段性仿真参数：

```text
R1 = 0.125 ohm/km
X1 = 0.08 ohm/km
```

当前论文阶段仍按用户指定的 `P-f/Q-V` 运行；线路 R/X 是阶段性典型参数，真实馈线数据获取后再做 R/X 敏感性和耦合方式校核。不能把当前典型线路算例称作现场馈线控制性能验证。

## OpenDSS 参考的含义

普通 OpenDSS `Storage` 元件不会自动成为 GFM 下垂参考源。当前实现采用：

1. 将 `Vsource.source` 移到 `Storage_Bus`，作为数值 `Vtheta` 潮流参考；
2. 外部 Python 控制器根据 `P-f/Q-V` 下垂关系更新 BESS P/Q 和电压/频率参考；
3. 每次更新后调用 OpenDSS 快照潮流；
4. 读取 Vsource 端口 P/Q、母线电压、线路电流和损耗；
5. Vsource 净功率不能被当作外部供电，终态必须在交换容差内；
6. BESS 达到 PCS/SOC/P-Q 限值后的剩余缺额转为 EV 负荷削减，盈余转为风光限发。

## 当前输入接口

`IslandedGFMQSTS.solve_step()` 需要单时刻：

- `pv_kw`、`wind_kw`；
- `ev_base_kw`、`ev_load_kvar`；
- `non_ev_kw`、`non_ev_kvar`；
- 当前 BESS 计划 P/Q；
- 当前 SOC 由控制器保留并递推。

单时刻可以验证控制器、符号和物理约束；连续 24 小时数据才可以验证 SOC 演化、备用容量、弃电/失负荷和滚动 QSTS。未来滚动接口应把预测窗口转换成每个小时的上述 P/Q 输入。当前 `run_islanded_gfm.py` 要求显式提供 PCS kVA、PCS 最大 kvar、EV 负荷 kvar 和下垂耦合方式。

## 尚未具备现场依据的参数

- PCS 具体 GFM 控制器参数；
- 变压器参数；
- 线路实际长度、R/X 和额定电流；
- PCS 待机损耗和无功能力曲线；
- 服务区非 EV 负荷的 P/Q；
- 现场功率因数和电压等级校核。

因此当前线路电流接口只记录 `Iline`。没有真实线路额定电流时，输出的 `line_thermal_limits_verified` 为 `False`，不能据此宣称线路热约束已满足。

运行示例：

```powershell
D:\miniconda3\python.exe -m src.model.run_islanded_gfm `
  --pcs-kva 1000 --pcs-q-kvar 600 --coupling X_gt_R `
  --ev-load-kvar 60 --soc 50 --pv 200 --wind 50 --ev-load 180
```

这里的 `X_gt_R` 只是当前论文阶段按指定 `P-f/Q-V` 策略运行的配置名；它不等于已经证明现场馈线满足 `X>R`。
