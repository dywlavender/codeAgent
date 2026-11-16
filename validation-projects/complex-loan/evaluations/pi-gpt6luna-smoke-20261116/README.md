# gpt-6-luna 生产链路冒烟（2026-11-16）

- 目的：确认问答默认模型切换后，`get_available_models`、`--model` 校验、生产 `QueryService + PiAgentRuntime` 和完整回答链路可用；同时观察质量变化。
- 运行：`native-frontend / none / r1`，模型 `deepseek/gpt-6-luna`，Pi 1.5.0，系统提示指纹 `c12fd22d7ef4c3a5`。
- 结果：completed；13 次模型调用、11 次工具、2 个读取文件、187,615 Token、436.1 秒。
- 质量：关键文件 2/8；逐项复核为 1 项通过、4 项部分通过、1 项未通过（见 [checks.md](raw-traces/checks.md)）。答案自述“批量 endpoint/还款 command/API 前缀 provider 未核实”，比 glm-5.3-flash 的 native-frontend 结果浅。
- 引用：答案含 17 个 Markdown 链接；按独立源码引用去重后 15 条（两处相同 path+line 重复），其中读取确认 4、搜索可见 11、未交付 0。15 条原始目标都是临时绝对路径，其中缺段 2 条、重复 `repos/...` 前缀 2 条（合计 4 条格式错误）；路径归一化后 0 条无法映射。生产运行已在最终答案层改为工程相对路径，旧答案中的绝对路径只保留在原始 Trace。
- Codemode：首次调用因模型生成不完整 JavaScript 返回 `SyntaxError`，未执行任何内部工具；随后 Pi 改用 ls/grep/read。单次工具参数错误不代表 Codemode 整体不可用。
- 说明：这是单次冒烟，不是 gpt-6-luna 的质量基线；glm 的 7 次基线不能与它混算；该次与 glm 基线的系统提示也不同，不能用于严格证明模型优劣。原始 Trace 归档在 `raw-traces/`（`batch.json` 与 `runs/`）。
