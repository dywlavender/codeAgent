# Answer Checkpoint v2 三题真实模型 A/B（2026-11-15）

状态：**效果验收未通过；检查点保持默认关闭。** 本轮修正了验收指出的结构关联、会话隔离和成本问题；真实模型显示完成数有所上升，但关键缺口识别仍未稳定，且出现“检查点完成后提前收尾并宣称已覆盖”的案例。

## 本轮修正

针对 `ac4b8e9` 验收意见：

- **事项与缺口强制关联**：检查点 schema 升为 v2（`CHECKPOINT_VERSION=2`），`aspects` 必须有唯一 `id` 和 `required`，`gaps` 必须用 `aspectId` 关联已有事项。`aspects` 为空、`gaps` 关联不存在事项、`missing` 事项没有缺口、全部事项都标为非必需、字段类型错误都判定为检查点失败，不注入任何指导。
- **防止必需缺口被降为 optional**：`required` 且 `missing` 的事项，其关联缺口若标为 optional，程序提升为 required 并记录 `adjustedGapCount`。
- **区分“是否必需”和“当前能否核实”**：缺口新增 `resolvable`。`resolvable=false` 的 required 缺口不会进入“下一步只补查这些”，而是提示“当前授权资料内无法继续核实，不要继续搜索，答案中说明限制”。
- **降低检查点成本**：检查点使用独立短提示（不再复用完整调查系统提示），独立预算 `BUSINESS_CODE_AGENT_CHECKPOINT_TIMEOUT=60`、`...OUTPUT_TOKENS=1024`、`...CONTEXT_CHARS=20000`；工具原文在检查点请求中替换为路径/行号占位，完整原文仍保留在本轮消息和引用核验中。所有预算计入调用与总耗时。
- **修复会话污染**：检查点进展消息带 `runtimeOnly=checkpoint` 标记，`DirectSessionStore.save/load` 和 `compact_messages` 都不把它当作用户问题或后续会话历史；上下文压缩也不会因它丢掉原问题。
- **兼容性**：检查点关闭时不把新增预算字段放进会话范围，避免无谓地使既有 core 会话失效；开启时预算变化会重建会话。

配置和会话隔离：`runtimeRevision=core-direct-2-checkpoint-2`；开关、版本、超时、输出和上下文预算均进入执行设置；开启时进入会话范围。

## 环境与设计

- 工程 `validation-projects/complex-loan`，Profile `core-v1`，模型 `glm-5.3-flash`，温度 0.6。
- 40 次调用/300 秒/600,000 字符/131,072 输出 token；检查点 60 秒/1024 token/20,000 字符。
- A=检查点关闭，B=相同代码/工具/Prompt 开启检查点；3 题 × 2 次重复，单路顺序。
- 主样本 12 次运行；另补跑 1 次 B 组 `native-lifecycle`，因为主样本两次 lifecycle 检查点都遇到供应商连接错误。

## 主样本结果

| 组/题 | 状态 | 调用 | 耗时 | 确认引用 | 关键文件 | 检查点 | 检查点耗时/输入 | 检查点后工具/Read/新仓库 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-r1 frontend | completed | 17 | 122.0s | 30/34 | 8/8 | — | — | — |
| A-r1 lifecycle | incomplete | 34 | 263.4s | 14/21 | 4/6 | — | — | — |
| A-r1 v2 | incomplete | 29 | 224.0s | 15/17 | 5/5 | — | — | — |
| A-r2 frontend | completed | 30 | 194.1s | 27/39 | 8/8 | — | — | — |
| A-r2 lifecycle | incomplete | 39 | 206.3s | 14/18 | 5/6 | — | — | — |
| A-r2 v2 | completed | 11 | 134.0s | 9/17 | 3/5 | — | — | — |
| B-r1 frontend | completed | 29 | 220.2s | 27/36 | 8/8 | completed：2/1/0，2 缺口/1 必需 | 27.2s / 2,532 | 12/3/0 |
| B-r1 lifecycle | incomplete | 36 | 213.6s | 7/12 | 4/6 | failed：OpenAIConnectionError | 连接失败 / 2,352 | 24/2/0 |
| B-r1 v2 | completed | 14 | 176.8s | 14/16 | 5/5 | completed：2/1/0，2/1 | 30.4s / 2,811 | 4/2/0 |
| B-r2 frontend | completed | 21 | 159.4s | 32/36 | 8/8 | completed：3/0/0，2/0 | 31.6s / 2,685 | 0/0/0 |
| B-r2 lifecycle | incomplete | 39 | 197.7s | 8/11 | 4/6 | failed：OpenAIConnectionError | 连接失败 / 2,418 | 20/3/2 个仓库 |
| B-r2 v2 | completed | 13 | 209.7s | 18/22 | 5/5 | completed：2/1/0，2/1 | 58.0s / 3,246 | 4/3/0 |

聚合：

| 指标 | A（关闭） | B（开启） |
| --- | --- | --- |
| 完成 | 3/6 | 4/6 |
| 模型调用 | 160 | 152（-5.0%） |
| 耗时 | 1143.8s | 1177.4s（+2.9%） |
| 确认引用 | 109/146（74.7%） | 106/133（79.7%） |
| 关键文件 Read | 33/38 | 34/38 |
| 答案关键词覆盖 | 41/48 | 39/48 |

检查点实际执行：4/6 成功，2/6 因供应商连接错误失败（均在 lifecycle，约 12 毫秒内连接失败，未产生检查点输出）。成功的检查点耗时 27.2–58.0 秒，合计 147.2 秒，平均 36.8 秒，约占 B 组总耗时 12.5%；输入 2.5–3.2 千字符。v1 的检查点合计 355.8 秒、占 27.5%，v2 的成本明显下降，但仍不是“无感”开销。

## 分题观察

- **native-frontend**：A 2/2、B 2/2 完成，关键文件均 8/8。B 调用 50 次/379.6 秒，A 47 次/316.1 秒；B 没有提速，引用率略高。检查点未改变最终覆盖。
- **v2-strategy-code**：A 1/2、B 2/2 完成；A 关键文件 8/10，B 10/10。A-r2 只用 11 次调用完成但缺 `LoanProduct`、`LoanAssemblerImpl`；B 两次都 5/5，调用 27 次、耗时 386.5 秒。这是本轮最明确的正向案例：检查点帮助避免了“提前完成但关键证据不足”。
- **native-lifecycle**：主样本 A 0/2、B 0/2 完成；A 关键文件 9/12，B 8/12，引用 A 28/39、B 15/23。B 的两次检查点都因连接错误未执行，因此主样本没有真正检验命令路由缺口；B 组的结果不能归因于检查点。

## 补充 lifecycle 运行

主样本两次 lifecycle 检查点都未执行，单独补跑一次 B 组同题（独立临时数据库/工作区）：

| 指标 | 结果 |
| --- | --- |
| 状态 | completed |
| 模型调用 | 16 |
| 耗时 | 216.1s |
| 检查点 | completed，53.5s，2 确认/1 部分/0 缺失，2 缺口/1 必需 |
| 关键文件 | **3/6**（缺 `CommandWrapperBuilder`、`LoanApplicationApprovalCommandHandler`、`CommandHandlerProvider`） |
| 确认引用 | 10/17 |
| 检查点后 | 3 次工具、1 个新读文件、无新仓库 |

检查点把“REST 入口如何触发状态变化”标为 required，模型随后完成了答案，但答案开头写“所有关键环节已确认到行级证据”，实际没有覆盖命令包装器和 `@CommandType` 到审批 Handler 的路由。**这是完成度提高但关键证据遗漏、并对完整性作出过度声明的案例**，不能作为检查点有效的证据；它说明“是否必需”的判断仍可能漏掉原问题要求的命令链路，且检查点可能加速收尾。

## 与 v1 对比的边界

v1（`ac4b8e9`）A 2/6、B 1/6 完成，B 关键文件 31/38；v2 A 3/6、B 4/6 完成，B 关键文件 34/38。v2 的 A 基线本身更强（3/6 对 2/6），供应商在两次实验间的延迟和连接错误也不同，因此**不能把完成数变化直接归因于检查点**。可以确认的是：

- v2 的结构关联、必需缺口提升、`resolvable` 区分和会话隔离按设计工作，单元测试覆盖了失败回退。
- 真实运行中 4 次成功检查点没有出现 `adjustedGapCount` 或 `requiredUnresolvableCount` 非零的情况，说明这些规则尚未在真实分歧样本上得到充分检验。
- 检查点成功时，v2 题能从“缺关键文件完成”变为“5/5 完成”；检查点失败或缺口识别不准时，仍会出现关键链路遗漏和过度声明。

## 结论

本轮属于**接近情形 B、但不足以支持启用**：

- 完成数 3/6→4/6、关键文件 33/38→34/38、调用 -5.0%，但耗时 +2.9%、关键词覆盖 41/48→39/48；收益很小且不稳定。
- 最关键的 lifecycle 主样本检查点全部因连接错误失败；补充运行虽然完成，却只覆盖 3/6 关键文件并宣称已确认全部环节，触发“完成但遗漏”的 P0 风险。
- 检查点耗时从 v1 的平均约 59 秒降到 36.8 秒，但仍是显著预算开销，且 2/6 请求无法完成。
- **不批准默认启用，不进入 8 题扩展**。检查点继续作为默认关闭的实验能力保留；生产默认仍为 Legacy。

下一步不应继续增加检查点次数或再引入评审模型。优先解决“完成判断”本身：只有在原问题要求的必需事项都有可核验证据（或明确标为无法核实）时才允许完成；缺口关联和 `resolvable` 需要更多真实样本验证，尤其是命令路由、跨系统职责这类容易漏掉的关键环节。

## 限制

- 3 题、每组 2 次重复、温度 0.6；供应商单账号并发 1，运行时刻不同，连接错误和延迟波动明显。
- 补充 lifecycle 运行不属于主样本，只用于观察检查点执行后的行为，不能替代配对对照。
- 关键文件清单由人工按题库指定，Agent 不可见；关键词覆盖只检查术语，不等同于语义正确。
- 引用核验只验证路径、行号和 Read 交付区间，不证明业务语义；补充运行中的“所有关键环节已确认”是人工阅读发现的过度声明，不是引用核验能拦截的错误。
- 检查点请求会省略工具原文，只保留路径/行号；这降低了成本，但也可能使检查点对细节的判断弱于最终答案。

## 附录：关键文件清单

- `native-frontend`（8）：`loans.service.ts`、`create-loans-account.component.ts`、`approve-loan.component.ts`、`disburse.component.ts`、`make-repayment.component.ts`、`api-prefix.interceptor.ts`、`authentication.interceptor.ts`、`environment.ts`。
- `native-lifecycle`（6）：`LoanStatus.java`、`DefaultLoanLifecycleStateMachine.java`、`LoansApiResource.java`、`CommandWrapperBuilder.java`、`LoanApplicationApprovalCommandHandler.java`、`CommandHandlerProvider.java`。
- `v2-strategy-code`（5）：`FineractClient.java`、`LoanRepaymentScheduleTransactionProcessorFactory.java`、`FineractStyleLoanRepaymentScheduleTransactionProcessor.java`、`LoanProduct.java`、`LoanAssemblerImpl.java`。
