# Answer Checkpoint 三题真实模型 A/B（2026-11-15）

状态：**实验未通过效果验收，检查点保持默认关闭。** 本轮只验证单次“答案先行检查点”能否改善调查收敛；生产默认仍为 Legacy，`core-v1` 仍需显式启用。

## 目的与设计

Commit 2 的退化 Trace 显示，`native-frontend` 在关键证据已齐后继续扩展、`native-lifecycle` 在命令路由证据缺失时转向无关系统，问题在“何时停止扩展、如何识别缺口”，不在搜索工具。本轮验证一个轻量检查点能否把已确认结论、部分结论和缺口整理出来，让模型更早收敛，同时不牺牲关键证据覆盖。

- A 组：当前代码（含检查点实现），`BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED=false`。
- B 组：完全相同代码、工具、Prompt、模型参数，仅 `BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED=true`。
- 检查点只在正常调查预算约一半时触发一次；只发一次无工具 JSON 请求；计入模型调用与时间预算；解析失败、上下文不足或取消时回退原调查；预算收尾继续兜底。
- 检查点的 `confirmed` 依据必须通过既有 `verify_answer` 的路径、行号和 Read 交付区间核验，否则降级为 `partial`。

题目的选择依据是 Commit 3.1 的退化诊断：`native-frontend`（关键文件已齐仍收尾）、`native-lifecycle`（遗漏命令路由并扩展）和 `v2-strategy-code`（正向对照）。

## 环境与数据

- 工程：`validation-projects/complex-loan`；Profile：`core-v1`。
- 模型：本地端点 `glm-5.3-flash`；`temperature=0.6`；输出上限 131,072 token；最终答案上限 131,072 token。
- 预算：40 次模型调用、300 秒、600,000 字符上下文；正常调查时间预算为 180 秒（预留 120 秒收尾），检查点时间阈值 90 秒。
- 供应商单账号并发上限为 1，所有运行单路顺序执行；每次运行使用独立临时数据库、工作区和 Trace；`NO_PROXY`/`no_proxy` 在验证进程中清空，避免宿主代理影响连接。
- 3 题 × 2 方案 × 2 次重复，共 12 次运行；A/B 交替按题执行，未修改 `.env`。
- 运行数据（`results.json`、`traces/`）保存在本机临时目录 `/tmp/wf-checkpoint-ab-20261115/`，与历史真实验证记录一致，未纳入仓库。Trace 可用 `scripts/evaluation/trace_diagnosis.py` 离线复核。

## 单次结果

| 组/题 | 状态 | 调用 | 耗时 | 确认引用 | 关键文件 Read | 检查点（确认/部分/缺失，缺口/必需） | 检查点耗时 | 检查点后工具/Read/新仓库 | 答案字数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A-r1 native-frontend | completed | 23 | 143.7s | 26/42 | 8/8 | — | — | — | 5202 |
| A-r1 native-lifecycle | incomplete | 26 | 214.0s | 7/12 | 4/6 | — | — | — | 3210 |
| A-r1 v2-strategy-code | incomplete | 24 | 241.8s | 13/19 | 4/5 | — | — | — | 3124 |
| A-r2 native-frontend | incomplete | 35 | 220.1s | 23/27 | 8/8 | — | — | — | 3467 |
| A-r2 native-lifecycle | incomplete | 23 | 233.0s | 14/16 | 4/6 | — | — | — | 3721 |
| A-r2 v2-strategy-code | completed | 24 | 191.0s | 18/21 | 5/5 | — | — | — | 3512 |
| B-r1 native-frontend | incomplete | 17 | 225.9s | 17/21 | 8/8 | 3/5/0，3/0 | 55.3s | 5/4/0 | 2726 |
| B-r1 native-lifecycle | incomplete | 22 | 228.6s | 4/12 | 3/6 | 2/2/2，1/1 | 43.8s | 5/0/0 | 2811 |
| B-r1 v2-strategy-code | completed | 14 | 179.8s | 14/14 | 5/5 | 5/1/0，3/0 | 52.1s | 0/0/0 | 3923 |
| B-r2 native-frontend | incomplete | 19 | 219.0s | 20/22 | 8/8 | 5/3/0，2/0 | 75.3s | 0/0/0 | 2855 |
| B-r2 native-lifecycle | incomplete | 22 | 229.7s | 5/11 | 3/6 | 5/2/0，4/2 | 92.2s | 2/1/0 | 3148 |
| B-r2 v2-strategy-code | incomplete | 24 | 210.4s | 12/14 | 4/5 | 3/3/1，3/1 | 37.1s | 11/4/0 | 2979 |

说明：

- “关键文件 Read”按人工定义的文件清单统计，只表示文件正文是否被 Read 交付，不表示答案语义正确。`native-frontend` 8 个、`native-lifecycle` 6 个、`v2-strategy-code` 5 个；清单见附录。
- “检查点后工具/Read/新仓库”统计检查点之后的工具调用、Read 到的不同文件数和首次进入的新仓库；新仓库均为 0。
- B 组检查点触发轮次依次为：frontend 第 12/18 轮、lifecycle 第 18/20 轮、v2 第 13/17 轮；均在 90 秒正常时间预算后、下一次模型调用前执行，没有提前于半预算触发。
- `incomplete` 均为 `stopReason=budget`，最后一次调用是程序触发的无工具收尾答题，不是模型自行完成。

## 汇总

| 指标 | A（检查点关闭） | B（检查点开启） |
| --- | --- | --- |
| 完成 | 2/6 | 1/6 |
| 模型调用 | 155 | 118（-23.9%） |
| 耗时 | 1243.6s | 1293.4s（+4.0%） |
| 确认引用 | 101/137（73.7%） | 72/94（76.6%） |
| 关键文件 Read | 33/38 | 31/38 |
| 答案关键词覆盖 | 38/48 | 35/48 |
| 检查点调用 | — | 6 次，全部解析成功 |

分题观察：

- `native-frontend`：A 1/2 完成、B 0/2 完成；关键文件两组均为 8/8。B 在检查点点名“没有 required 缺口”后，r1 仍继续 5 次工具调用，r2 直接收尾但仍因预算未完成。检查点没有带来完成率提升，且 55–75 秒的检查点耗时压占了收尾前的可用时间。
- `native-lifecycle`：A 4/6 → B 3/6，两次重复均缺 `CommandWrapperBuilder`、`LoanApplicationApprovalCommandHandler`、`CommandHandlerProvider`；B 的引用确认降为 4/12 和 5/11。检查点虽然识别到部分命令链路未核实，却把 `LOAN_CREATED` 触发点列为 required，而把 `logCommandSource` 的命令路由降为 optional；模型继续补查错误缺口，答案缺少“REST 入口 → CommandWrapper/@CommandType → 审批 Handler”的关键环节。
- `v2-strategy-code`：B-r1 是唯一明确正向案例，14 次调用、179.8 秒完成、关键文件 5/5、确认引用 14/14、检查点后不再调用工具；A-r2 同样完成但用了 24 次调用和 191.0 秒。B-r2 则把产品实际策略配置列为 required 缺口，检查点后继续 11 次工具调用、耗时 210.4 秒，最终未完成且缺 `LoanProduct.java`。

检查点调用本身耗时 37.1–92.2 秒，合计 355.8 秒，约占 B 组总耗时的 27.5%。B 组模型调用减少 23.9%，但总耗时仍增加 4.0%，说明“减少调查轮次”没有转化为实际时间收益。

## 缺口识别质量

这是本轮最关键的失败点，而不是检查点请求本身：

| 运行 | 检查点识别到的核心缺口 | 实际问题 | 后果 |
| --- | --- | --- | --- |
| B-r1 lifecycle | required：`LOAN_CREATED` 生产触发点 | 命令路由/审批 Handler 链未核实 | 补查了非关键触发点，关键链仍缺 |
| B-r2 lifecycle | required：`LOAN_CREATED`、审批 transition 调用点；optional：`logCommandSource` 路由 | 命令路由被标为 optional | 模型继续扩展但未补命令链 |
| B-r2 v2 | required：产品实际策略值与覆盖开关 | 四个授权仓库没有产品创建源码，无法在本轮回答 | 检查点后 11 次工具调用仍未解决，拖到预算收尾 |
| B-r1/B-r2 frontend | 0 个 required | 关键证据已齐 | 未强制结束，但检查点耗时压缩了收尾时间 |

检查点能够整理已确认结论、发现“命令路由未核实”这类缺口，但**不能可靠判断缺口是否属于原问题的必需环节**。`missing`/`partial` 与 `required`/`optional` 之间没有稳定映射：模型会把无法在本轮回答的产品配置标为 required，也会把原问题要求的命令路由标为 optional。程序只能核验引用是否存在，不能推断语义完整性；因此检查点不能作为自动结束机制。

## 结论与边界

按预定决策树，本轮属于**接近情形 B、并带有 C 的部分特征**：

- 调用数和部分题的收敛速度改善：总计 -23.9% 调用；`v2-strategy-code` B-r1 以 14 次完成且 0 次检查点后工具调用。检查点作为“进度整理器”有可观察价值。
- 完成率没有改善（2/6 → 1/6），关键文件覆盖下降（33/38 → 31/38），`native-lifecycle` 两次重复均退化，答案关键词覆盖下降（38/48 → 35/48）。因此**不验收自动结束，也不进入 8 题扩展**。
- 人工浏览 6 份 B 答案，未发现与源码直接冲突的新错误断言；B 的答案普遍明确列出未确认缺口。但“没有发现新错误”不能抵消关键证据遗漏，遗漏本身已触发 P0 退化；本轮也未做逐条语义复核。
- 检查点默认关闭，保持 `BUSINESS_CODE_AGENT_CHECKPOINT_ENABLED=true` 显式启用；生产默认仍为 Legacy。检查点不改变工具权限、不强制结束，预算收尾仍为兜底。
- 不把检查点耗时当作轻量成本：单次 37–92 秒，已接近一次正常调查调用的数倍；后续若要保留，必须同时评估它对时间预算的挤占。
- 不因 `v2-strategy-code` 的正向结果宣称整体有效；三题、温度 0.6、每组两次重复，A/B 运行时刻不同，且供应商并发限制为 1，结果只适用于该模型、该预算和该组题目。

下一步若继续，应先改进缺口识别而不是增加检查点次数或提示词堆叠：

1. 要求 `aspects` 中标记为 `missing` 的项必须对应 `required` 缺口，除非明确说明该 aspect 不属于原问题；避免 `logCommandSource` 这类核心链路被降为 optional。
2. 禁止把“授权仓库中不存在”的配置（如产品实际策略值）列为 required；这类项应标为不可确认，并提示基于已读源码作答。
3. 在检查点后的进度提示中，把 required 缺口与对应的未覆盖问题环节绑定，减少模型自行改写缺口目标。
4. 重新跑同一 3 题、同一预算的 A/B；只有关键文件覆盖和完成率同时不退化，才考虑 8 题扩展。

## 限制

- 单模型 `glm-5.3-flash`、温度 0.6、三题、每组两次重复；不是统计显著的效果结论。
- A/B 顺序执行且运行时刻不同；供应商曾出现连接错误，失败运行未纳入结果；并发上限 1 使并行对照不可用。
- 关键文件清单由人工按题库 `checks` 指定，Agent 不可见；关键词覆盖只检查答案是否出现术语，不等同于语义正确。
- 引用确认只验证路径、行号和 Read 交付区间，不证明业务语义正确；人工浏览没有覆盖所有断言。
- 检查点、收尾和答案生成共享同一 300 秒/40 次预算；检查点耗时会计入总耗时，不能与“无检查点运行”的直接耗时简单相减。

## 附录：关键文件清单

- `native-frontend`（8）：`loans.service.ts`、`create-loans-account.component.ts`、`approve-loan.component.ts`、`disburse.component.ts`、`make-repayment.component.ts`、`api-prefix.interceptor.ts`、`authentication.interceptor.ts`、`environment.ts`。
- `native-lifecycle`（6）：`LoanStatus.java`、`DefaultLoanLifecycleStateMachine.java`、`LoansApiResource.java`、`CommandWrapperBuilder.java`、`LoanApplicationApprovalCommandHandler.java`、`CommandHandlerProvider.java`。
- `v2-strategy-code`（5）：`FineractClient.java`、`LoanRepaymentScheduleTransactionProcessorFactory.java`、`FineractStyleLoanRepaymentScheduleTransactionProcessor.java`、`LoanProduct.java`、`LoanAssemblerImpl.java`。
