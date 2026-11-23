# Agent B：阶段 2 价格 Agent 回调适配

## 工作区与分支

- 目录：`D:\YWHC\power_plant_dispatching\workspaces\B`
- 分支：`team/b-agent`
- 只在本工作区修改和提交。不得改 A/C/D 工作区。

## 本轮目标

阅读根目录 `docs/ABCD阶段2集成任务.md` 和 A 的 `docs/multisite_integration_v1.md`（如已提交）。把已有单站价格 Agent 封装成 D 可调用的 `SiteStrategy`，不重写训练器或物理环境。

1. 新增/完善 D replay adapter：从 `forecast_window`、当前 SOC/SOH、assets、价格档位和 trace 信息构造现有价格 Agent 的观测；模型 checkpoint H 与 D 提供的预测窗长度不一致时，按文档约定安全截取/转换并显式记录，不得错误 reshape 或读取未来实测。
2. 返回 A `SiteAction` 或等价 mapping：当前时间戳、合法 price level、候选 storage_kw、requested_ev_kw。非法时间、站点 ID、价格档位、缺失状态应报错；storage 和 EV 仍交 D 安全执行器投影。
3. 支持明确加载现有 checkpoint，保存策略哈希/版本/price levels；没有 checkpoint 必须报错，不能降级成固定/规则策略并标记 Agent。
4. 新增 D 回调形状测试：forecast H=1 与 H>1、动作时间戳/档位、确定性、SOC/SOH 可见性、未来实测隔离和 checkpoint round-trip。

## 验收

- `parper.test.test_price_agent` 和新增 adapter tests 全通过。
- 用现有固定 checkpoint 生成至少一个有效 SiteAction；不得使用测试规则策略冒充真实 Agent。
- `python -m compileall -q src scripts parper/test` 通过。
- 提交仅包含 Agent/adapter/测试/说明，提交至 `team/b-agent`，不 force-push。

完成时报告 checkpoint revision/hash、适配边界、动作示例、测试结果、限制和提交哈希。单站 checkpoint 接入只验收工程兼容，不代表多站收益优势。
