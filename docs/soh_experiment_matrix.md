# SOH 多场景矩阵 runner

`scripts/run_soh_experiment_matrix.py` 只负责生成和校验 SOH 实验矩阵及结果
schema，不实例化策略、不启动耗时比较。默认设计为：

- 初始 SOH：`1.0, 0.9, 0.8, 0.7`；
- 需求分位：`P10, P50, P90`；
- 每个 SOH×需求格至少三个独立 seed（默认 `11, 23, 37`）；
- 四个独立机制组合：容量反馈、退化成本反馈分别开/关。

容量反馈和退化成本反馈是两个布尔字段，且每个 case 明确
`legacy_combined_soh_switch_used=false`。manifest 禁止出现旧的
`use_soh_in_game` 字段，避免把单一开关当成两个消融机制。

## Dry-run

```powershell
python scripts/run_soh_experiment_matrix.py --write-manifest output/soh_matrix_manifest.json
python scripts/run_soh_experiment_matrix.py --manifest output/soh_matrix_manifest.json
```

结果执行器应逐条写出 `status=converged|nonconverged|failed`、四类 solver
residual（price/demand/SOH/forecast-price）和三类物理残差（功率平衡、能量
平衡、SOC 转移）。汇总必须给出每格 `n/converged/nonconverged`；仅
`converged` 记录进入 `performance_mean_converged_only`，未收敛记录保留在
`nonconverged_failure_diagnostics` 中供失败诊断。

验证命令：

```powershell
python -m unittest parper.test.test_soh_experiment_matrix -v
```
