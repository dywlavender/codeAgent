# Pi lifecycle 命令链修复验证快照（2026-11-16）

状态：部分完成。4 次计划运行中 `none` 两轮 completed；`backbone` 两轮因上游模型额度耗尽而 failed（`1005 exceed quota limit`），不能用于 A/B 结论。

- 修复内容：Pi 系统提示补回调查契约（入口 → 路由/命令 → Handler/注册表 → Service/状态机），并在 `usage.systemPromptSha256` 记录提示指纹（本轮为 `c12fd22d7ef4c3a5`）。
- `none r1`：关键文件 6/6；104 次工具、3.41M Token、542.7 秒；引用 31 条读取确认、8 条搜索可见、0 条未交付。
- `none r2`：关键文件 4/6；139 次工具、6.16M Token、895.6 秒，接近 900 秒超时；答案同样覆盖 `CommandHandlerProvider → @CommandType → Handler` 链；1 条引用使用了错误的模块路径（内容在另一路径被读取确认）。
- `backbone r1`：在 100 次模型调用、123 次工具后因上游额度失败，无最终答案；`backbone r2` 启动 16 秒即因同一额度错误失败。
- 结论边界：命令链缺口在 `none` 两轮重复中均已补上，但成本波动大、停止行为仍不稳定；Business Context 组尚无有效修复后样本。
- 原始 Trace、逐次 `result.json` 和 `review-template.json` 归档在 `raw-traces/`（`batch.json` 与 `runs/`）；Pi 会话仍只在本地 `.data/`。
