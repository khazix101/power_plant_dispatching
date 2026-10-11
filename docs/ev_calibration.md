# EV 基础负荷与价格响应标定

运行：

```powershell
D:\miniconda3\python.exe scripts/calibrate_ev_model.py
```

输出目录：`Dataset/processed/ev_calibration/`。该目录被 `Dataset/` 规则忽略，不提交原始或派生数据。

## 标定边界

高速服务区记录作为 EV 用户侧观测。基础 EV 负荷由正常交易记录聚合得到，价格响应只使用有效支付价格做条件关联分析。由于历史价格变化不是随机实验，回归斜率和 log-log 弹性只能作为观测代理，不能直接称为因果价格弹性。

## 输出与调度映射

- `expressway_clean_sessions.csv`：清洗后的充电会话；
- `expressway_hourly_ev_load.csv`：跨小时按重叠时长分摊后的基础负荷；
- `expressway_user_hour_parameters.csv`：站点/小时用户参数；
- `ev_user_group_parameters.csv`：经验用户群参数；
- `observed_price_response_bins.csv`：有效价格分箱下的观测响应；
- `calibration_summary.json`：样本数、日期、异常率和回归结果。

在滚动 SFSE 中，基础负荷使用 `normal_energy_kwh`，价格响应单独由跟随者模型计算：

\[
P^{EV}_t = \hat P^{base}_t + \Delta P^{response}_t(p_t).
\]

脚本将 `energy_min_kwh_proxy=0.8*mean_energy_kwh` 作为初始服务需求代理；这个 0.8 是研究假设，后续应通过留出日期和主动停止/满充行为重新校准。`value_per_kwh_proxy_p75_price` 只是观测价格代理，不能直接解释为用户真实支付意愿。
