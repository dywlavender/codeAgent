# Pi lifecycle 命令链修复验证快照（2026-11-16）

状态：进行中。4 次计划运行已完成 1 次（`none r1`），`none r2`、`backbone r1/r2` 待完成。

- 修复内容：Pi 系统提示补回调查契约（入口 → 路由/命令 → Handler/注册表 → Service/状态机），并在 `usage.systemPromptSha256` 记录提示指纹。
- `none r1` 结果：关键文件 6/6（修复前基线 3/6）；答案覆盖 `CommandWrapperBuilder`、命令总线、`@CommandType` 注册表与 `LoanApplicationApprovalCommandHandler`/`DisburseLoanCommandHandler`；引用无“读取与搜索均未出现”的路径；104 次工具、3.41M Token、542.7 秒。
- 原始 Trace、逐次 `result.json` 和 Pi 会话保留在本地 `.data/pi-lifecycle-fix-ab-20261116/`（不提交）。
- 本快照不代表修复已完成稳定性与 A/B 验收；其余运行完成后再更新汇总与报告。
