# gpt-6-luna 生产链路冒烟（2026-11-16）

- 目的：确认问答默认模型切换后，`get_available_models`、`--model` 校验、生产 `QueryService + PiAgentRuntime` 和完整回答链路可用；同时观察质量变化。
- 运行：`native-frontend / none / r1`，模型 `deepseek/gpt-6-luna`，Pi 1.5.0，系统提示指纹 `c12fd22d7ef4c3a5`。
- 结果：completed；13 次模型调用、11 次工具、2 个读取文件、187,615 Token、436.1 秒。
- 质量：关键文件 2/8；check 1 覆盖，check 2-5 部分覆盖，check 6 未显式声明。答案自述“批量 endpoint/还款 command/API 前缀 provider 未核实”，比 glm-5.3-flash 的 native-frontend 结果浅。
- 引用：34 条；9 条读取确认、21 条搜索可见、4 条路径格式错误（绝对临时路径缺段或重复 `repos/...`），无法与交付原文匹配。引用路径精度是新增问题。
- 说明：这是单次冒烟，不是 gpt-6-luna 的质量基线；glm 的 7 次基线不能与它混算。原始 Trace 保存在本地 `.data/pi-gpt6luna-smoke-20261116/`。
