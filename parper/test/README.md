# 论文测试保留说明

论文分支的实际测试代码保留在仓库根目录 `tests/`，本目录只保存论文测试组织说明和运行方式，避免复制测试代码造成两份版本漂移。

## 当前相关测试

- `tests/test_dynamic_dc_soc.py`：直流网络、电压、有限 BESS SOC 和动态滚动控制；
- `tests/test_rolling_sfse.py`：滚动预测接口和 SFSE 当前时刻决策；
- `tests/test_calibration_loader.py`：真实 EV 标定结果读取接口。

## 运行方式

当前 D 盘 Miniconda base 未安装 pytest，论文入口可以用标准库直接执行：

```powershell
D:\miniconda3\python.exe -m compileall -q src tests scripts
D:\miniconda3\python.exe parper/test/run_paper_tests.py
D:\miniconda3\python.exe -c "from tests.test_dynamic_dc_soc import test_dynamic_dc_controller_updates_soc_and_returns_voltage; test_dynamic_dc_controller_updates_soc_and_returns_voltage()"
D:\miniconda3\python.exe -c "from tests.test_rolling_sfse import test_forecast_bundle_validates_horizon,test_rolling_controller_returns_safe_current_action; test_forecast_bundle_validates_horizon(); test_rolling_controller_returns_safe_current_action()"
D:\miniconda3\python.exe -c "from tests.test_calibration_loader import test_calibration_loader_requires_processed_output; test_calibration_loader_requires_processed_output()"
```

## 论文结果记录规则

- 测试数据、参数和随机种子必须记录；
- 真实标定输出在 `Dataset/processed/`，原始数据不提交；
- 论文图表由独立实验脚本生成；
- 交流 GFM 测试不属于本直流论文分支。
