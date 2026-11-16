# lifecycle 提示修复初步验证（2026-11-16）

状态：已被 `pi-lifecycle-fix-ab-20261116` 取代，保留作原始证据。

- 运行：`native-lifecycle / none / r1`，模型 `zcode/glm-5.3-flash`，Pi 1.5.0，单次完成。
- 结果：completed；54 次模型调用、136 次工具、30 个读取文件、45 次搜索、2,666,770 Token、452.9 秒。
- 关键文件 4/6；末键轮次 50；答案已说明 `CommandWrapperBuilder → @CommandType → CommandHandlerProvider/SynchronousCommandProcessingService → Handler` 链路。
- 该次运行早于 `usage.systemPromptSha256` 埋点，结果中没有提示指纹；正式修复批次记录为 `c12fd22d7ef4c3a5`，A/B 结论以正式批次为准。
- 原始输出归档在 `raw-traces/`（`batch.json` 与 `runs/`）。
