# Pi lifecycle 命令链修复验证快照（2026-11-16）

状态：部分完成。4 次计划运行中 `none` 两轮 completed；`backbone` 两轮因上游模型额度耗尽而 failed（`1005 exceed quota limit`），不能用于 A/B 结论。

- 修复内容：Pi 系统提示补回调查契约（入口 → 路由/命令 → Handler/注册表 → Service/状态机），并在 `usage.systemPromptSha256` 记录提示指纹（本轮为 `c12fd22d7ef4c3a5`）。
- `none r1`：关键文件 6/6；104 次工具、3.41M Token、542.7 秒；引用去重后 39 条（30 读取确认、8 搜索可见、1 无行号）。
- `none r2`：关键文件 4/6；139 次工具、6.16M Token、895.6 秒，接近 900 秒超时；引用去重后 37 条（31 读取确认、4 搜索可见、1 未出现、1 无行号）；1 条引用使用了错误的模块路径（内容在另一路径被读取确认）。
- `backbone r1`：在 100 次模型调用、123 次工具后因上游额度失败，无最终答案；`backbone r2` 启动 16 秒即因同一额度错误失败。
- 逐项复核（见 [checks.md](raw-traces/checks.md)）：r1 为 3 项通过、2 项部分通过、0 项未通过，r2 为 4 项通过、1 项部分通过、0 项未通过。r1 检查项 4 因未核实 Fineract 审批 REST 入口 `POST /loans/{loanId}?command=approve` 降为部分通过；两轮检查项 5 均为部分通过：r1 覆盖 `CommandWrapperBuilder → 命令总线 → @CommandType → DisburseLoanCommandHandler`，但缺少 `LoanApplicationApprovalCommandHandler` 审批分发；r2 覆盖命令总线、注册表和审批/放款 Handler，但没有明确指出 `CommandWrapperBuilder`，也未读取该类。
- 结论边界：命令路由关系在 `none` 两轮中均有改善，但不能表述为检查项 5 完整通过；成本波动大、停止行为仍不稳定；Business Context 组尚无有效修复后样本。
- 原始 Trace、逐次 `result.json` 和 `review-template.json` 归档在 `raw-traces/`（`batch.json` 与 `runs/`）；Pi 会话仍只在本地 `.data/`。
