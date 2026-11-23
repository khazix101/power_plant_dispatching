# Agent A：阶段 2 共用接口冻结

## 工作区与分支

- 目录：`D:\YWHC\power_plant_dispatching\workspaces\A`
- 分支：`team/a-platform`
- 只在本工作区修改和提交。不得改 B/C/D 工作区或实现他们的模块。

## 本轮目标

阅读根目录 `docs/ABCD阶段2集成任务.md`，维护 A v1 契约，并冻结 B/C/D 实际联调所需的适配约定。

1. 新增 `docs/multisite_integration_v1.md`，逐字段记录 SiteStrategy 输入、SiteAction 输出、Coordinator config、首时段 actions/tie-line flow metadata、正负号、单位、版本、状态和错误处理；要求与阶段 2 文档完全一致。
2. 增加契约侧 conformance 测试/fixture，至少检查 SiteAction 时间戳/价格字段形状、协调器配置必需字段、metadata 首时段动作及有符号 tie-line flow 形状；保留 `MultiSiteInput` v1.0.0 wire schema，不做破坏性改动。
3. 记录尚未确认的物理/业务输入，不填造真实参数。向主线程报告 B/C/D 实现中发现的契约冲突并提出最小兼容修正。

## 验收

- `python -m unittest parper.test.test_multisite_contract -v` 及新增 conformance tests 全通过。
- 文档样例能构造合法请求，也有非法 config/action/flow 样例。
- `python -m compileall -q src scripts parper/test` 通过。
- 提交只包含契约文档/测试，提交至 `team/a-platform`，不 force-push。

完成时给出契约版本、变更文件、测试结果、未决问题和提交哈希。
