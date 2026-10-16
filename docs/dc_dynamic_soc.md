# 直流微网动态 SOC 主从博弈分支

该分支移除交流 OpenDSS/GFM 运行主线，保留预测、真实 EV 标定、主从博弈、BRI、RL、SOH 和数据处理工作。物理微网采用论文参考的电阻型 DC 网络。

## DC 网络

对参考母线以外的节点，采用论文线性化电导矩阵：

\[
\Delta u_{red}=G_{red}^{-1}p_{inj,red}/P_{base}.
\]

节点注入约定为：PV/Wind 正、Load 负。支路电流由电阻和节点电压差计算，支路损耗为 \(I^2R\)。线路额定电流未提供时只报告电流，不宣称热约束通过。

## 动态 SOC

当前状态是：

\[
s_t=(SOC_t, d_t, \xi_t),
\]

其中 `d_t` 表示尚未完成的充电服务需求，\(\xi_t\) 表示未来供能和基础 EV 负荷预测。

储能状态递推：

\[
SOC_{t+1}=SOC_t+
\begin{cases}
-P_t\Delta t/(\eta_dE_B),&P_t\ge0,\\
-P_t\eta_c\Delta t/E_B,&P_t<0.
\end{cases}
\]

放电只能到运行备用下限 `SOC_reserve=20%`；物理 SOC 范围为 10%--90%。

## 从论文 DC 微网方法借鉴的内容

目标论文使用 DC 节点电导矩阵、电压/线路电流约束、BESS 能量递推、需求响应和 Stackelberg 上下层模型。该分支保留这些适合本项目的部分，删除电氢系统、PHEV 放电、TCL、外部并网和交流 GFM 控制。
