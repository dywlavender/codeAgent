# Pi 问答质量基线：纯源码与 Business Context 配对实验（2026-11-15）

> 状态：进行中。16 次基线运行已完成 7 次（native-frontend 4/4；native-lifecycle none 2/2、backbone 1/2）；`v2-strategy-code` 与 `biz-payment-pending` 尚未运行。lifecycle 修复验证完成 4 次：`none` 2/2 completed，`backbone` 2/2 因上游额度耗尽 failed。剩余运行因同一额度问题暂停，恢复后继续。
>
> 2026-11-16 起问答默认模型改为 `deepseek/gpt-6-luna`。本记录已有的 7 次基线均为 `zcode/glm-5.3-flash`，不能与 gpt-6-luna 续跑或混算；gpt-6-luna 需要新批次基线。
>
> 2026-11-16 复核补充：原始 Trace 已归档到各批次 `raw-traces/`；引用统计改为按独立源码引用去重，旧的正则命中次数统计作废；关键运行的逐项复核已落为 `checks.md`。下表引用列均为修正后数据。

## 结论摘要（阶段性）

- native-frontend：4 次运行都覆盖 5/6 检查项，第 6 项未被逐字声明但也没有把业务规则归到前端；没有发现凭空编造的路径或行号。低“引用确认率”主要因为生产核验只承认 `read` 原文，多数未确认引用实际在 grep 输出中可见。主要问题是末键后仍执行 5—36 次工具、3—22 次搜索，成本 0.33M—1.13M Token；A/B 正确性无可见差异，成本差异在两次重复内波动很大。
- native-lifecycle：两次 none 都只读到 3/6 关键文件，连续漏掉 `CommandWrapperBuilder → 命令总线 → @CommandType → Handler` 这一层（check 5），无效调查代理 22/30；backbone r1 读到 4/6，仍未覆盖该层，并把成本推到 248 次工具、6.63M Token。该缺口的直接原因是 Pi 使用自建系统提示且带 `--no-context-files`，工作区已有的调查契约（“找到第一个入口并不代表问题已回答”）没有进入 Pi。
- 针对性修复后，lifecycle `none` 两轮都显著改善了命令路由关系：r1 覆盖 `CommandWrapperBuilder → 命令总线 → @CommandType → DisburseLoanCommandHandler`，r2 覆盖命令总线、注册表和审批/放款 Handler。逐项复核均为 4 项通过、检查项 5 部分通过（r1 缺审批 Handler 分发；r2 未明确指出和读取 `CommandWrapperBuilder`），不能表述为 check 5 完整通过。关键文件分别为 6/6 和 4/6，成本 3.41M Token/542.7 秒与 6.16M Token/895.6 秒。第二轮接近 900 秒超时，修复改善了关系覆盖，但没有解决成本与停止问题。`backbone` 两轮因上游额度错误没有有效答案，Business Context 的修复后对照尚不成立。
- 尚未完成的 `v2-strategy-code`、`biz-payment-pending` 决定不了 Business Context 是否有价值；现在不能据此下结论。

## 实验设置

- 生产链路：`scripts/evaluation/evaluate_pi.py` 直接构造 `PiAgentRuntime` 与生产 `QueryService`，调用 `service.query()`；没有另写简化版 Agent，检查项只存在于评测脚本与 `review-template.json`，不进入问题或模型提示词。
- 题库：`complex-loan.json`（native-frontend、native-lifecycle）与 `complex-loan-diagnostic-v3.json`（v2-strategy-code、biz-payment-pending）。
- 工程：`complex-loan-validation`；同一冻结源码与需求资料。
- 模式：A 组 `mode=none`（源码、README、需求原文）；B 组 `mode=backbone`（在 A 组基础上增加 `knowledge/business-context`）。
- 模型与运行时：Pi 1.5.0，`zcode/glm-5.3-flash`，未启用 thinking，单次超时 900 秒；zcode 账号单并发，批次串行执行。
- 重复：每题、每组 2 次；运行顺序为题目 → 模式 → 重复。
- 冻结资料版本（来自运行记录的 `execution.materials`）：

| 资料 | 版本 |
| --- | --- |
| requirements | `sha256:f863836b7b3d6b81` |
| channel-service-repo | `meta-sha256:662306313bf04125` |
| external-simulator-repo | `meta-sha256:367ff820525ccd21` |
| fineract-repo | `meta-sha256:7e06e91044174aaa` |
| loan-middle-repo | `meta-sha256:7e71daa1be23e948` |
| mifos-web-repo | `meta-sha256:9cdee6a34439fab2` |

- 关键文件清单由 `evaluations/pi-quality-keys-20261115.json` 指定，仅供评测统计；它不是答案证据，也不代表只允许读这些文件。

## 指标与边界

- `completed` 只表示运行结束，不作为质量结论。
- 关键文件覆盖：评测清单中实际被 `read` 的文件数；它只说明是否到达关键位置，不说明关系理解正确。
- 末键轮次与扩张：最后一次读到清单文件的模型轮次，以及之后仍执行的工具、搜索与模型调用；用于观察“证据已足够后是否继续调查”。
- 无效调查代理：重复工具调用、空搜索、搜索有候选但后续未读该候选的搜索次数之和；未读候选总数只作为调查广度信号，不单独判为无效。
- 引用交付分类：离线报表从 `run.answer` 重新解析，不直接累加旧 Trace 的正则匹配次数；按独立源码引用去重（同一 `path+line` 只计一次；Markdown 链接的文字和目标不重复计数）。生产核验器只承认本轮 `read` 原文；本报告额外把未核实引用分为“搜索输出可见”“读取文件但行区间未交付”“读取与搜索均未出现”“无行号”四类。另统计原始路径的绝对/缺段/重复根/无法归一化问题。`semanticProof=false`，引用核验通过不等于语义正确。
- 结论可信度：按题库 checks 逐题阅读答案与源码抽查；不把引用数量当作正确性评分。
- 调查成本：模型调用、工具调用、Token 与耗时；汇总的是上下文累计 Token，不同轮次长度差异会放大该数字。

## 逐题结果

### native-frontend

**问题**：Mifos X 前端在创建贷款申请、发起审批、放款和还款时，分别调用哪些服务方法和后端接口？API 地址前缀、租户标识和认证是如何统一处理的？

| 组 | 重复 | 关键文件 | 末键轮次 | 末键后工具/搜索 | 模型调用 | 工具调用 | 读取文件 | 搜索 | 空搜索 | 无读取搜索 | 重复调用 | 无效代理 | 引用 确认/搜索见/未交付/无行号 | Token | 耗时 s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none | 1 | 7/8 | 14 | 26/14 | 32 | 52 | 19 | 20 | 0 | 6 | 0 | 6 | 23/22/1/6 | 744,483 | 234.3 |
| none | 2 | 8/8 | 14 | 36/22 | 40 | 58 | 22 | 22 | 2 | 5 | 0 | 7 | 33/9/0/2 | 1,131,863 | 292.0 |
| backbone | 1 | 8/8 | 11 | 17/12 | 19 | 48 | 19 | 19 | 2 | 5 | 0 | 7 | 22/18/0/5 | 539,025 | 142.2 |
| backbone | 2 | 7/8 | 17 | 5/3 | 23 | 39 | 12 | 18 | 1 | 3 | 0 | 4 | 30/14/1/2 | 327,716 | 260.8 |

**关键关系覆盖（按 checks 阅读答案）**：四次运行都覆盖了创建（组件 submit → `createLoansAccount` → `POST /loans`）、审批/放款（`loanActionButtons` → `POST /loans/{id}?command=…`）、还款（模板类型解析 → `submitLoanActionButton` → `POST /loans/{id}/transactions?command=…`）、前缀/租户/认证三类统一处理，以及各业务域 service 与环境配置来源。check 6“前端只组装与发送、业务规则在后端”没有被四次答案逐字声明，但也没有任何一次把业务规则归到前端，按“未违背”记为部分覆盖。backbone r1 额外给出批量审批/批量放款与 `POST /batches`，超出题目要求；none r2 额外下钻到 Fineract 的 `LoansApiResource`/`LoanTransactionsApiResource`，同样超范围。

**结论可信度**：四次答案的核心方法与端点与冻结源码一致。抽查 `loans.service.ts` 的 `createLoansAccount`、`loanActionButtons`、`submitLoanActionButton`、`getLoansAccountTemplateResource` 及认证拦截器均与引用相符。backbone r2 仍有 1 条引用读取文件但行区间未交付（`login.component.ts:99-103`，内容与源码 `reloadSettings` 基本相符）；`channel-service-repo/.../ChannelController.java:38` 由答案自标为未核实，属于跨系统补充。引用统计修正后没有发现读取与搜索均未出现的路径。none r1 有 1 条引用行区间越过已读文件末尾。没有发现凭空编造的路径或方法。

**观察**：

- 关键文件覆盖 7/8 或 8/8，说明本轮的关键位置都能被找到；缺失的是 `create-loans-account.component.ts` 的 `read`，但该文件的 167/183 行在 grep 输出中出现过。
- 末键后仍执行 5—36 次工具、3—22 次搜索；none r2 在关键文件齐备后还花了 26 次模型调用和 1.13M Token，主要用在 Fineract 后端端点和需求核对上。回答确实因此更细，但题目只要求前端调用链，属于超出必要范围的扩张。
- A/B 在答案正确性上没有可见差异；B 组平均 Token（433k）低于 A 组（938k），但 B r2 只读了 12 个文件且引用更少，重复内差异很大，不能把成本下降归因于 Business Context。

### native-lifecycle

**问题**：在当前项目中，一笔贷款申请从提交、审批到放款，账户状态如何流转？状态定义和流转校验分别在代码哪里实现？请结合 REST 入口说明每次动作是如何触发状态变化的？

| 组 | 重复 | 关键文件 | 末键轮次 | 末键后工具/搜索 | 模型调用 | 工具调用 | 读取文件 | 搜索 | 空搜索 | 无读取搜索 | 重复调用 | 无效代理 | 引用 确认/搜索见/未交付/无行号 | Token | 耗时 s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none | 1 | 3/6 | 6 | 72/48 | 52 | 85 | 20 | 53 | 8 | 14 | 0 | 22 | 21/8/0/2 | 2,080,351 | 533.7 |
| none | 2 | 3/6 | 10 | 61/39 | 26 | 114 | 21 | 61 | 16 | 14 | 0 | 30 | 37/11/0/2 | 1,035,551 | 339.1 |
| backbone | 1 | 4/6 | 102 | 14/7 | 110 | 248 | 33 | 107 | 35 | 16 | 4 | 55 | 38/2/0/3 | 6,628,987 | 738.2 |

**关键关系覆盖（按 checks 阅读答案）**：两次运行都覆盖了 `LoanStatus` 状态值、`DefaultLoanLifecycleStateMachine` 的 `LOAN_APPROVED`/`LOAN_DISBURSED`/`REPAID_IN_FULL` 来源校验、`POST /loans` 与 `POST /loans/{id}?command=approve|disburse`，以及状态机在服务层的触发点。两次都没有覆盖 check 5：command 经 `CommandWrapperBuilder` 构造为 `entity=LOAN` 的命令、再按 `@CommandType` 路由到 `LoanApplicationApprovalCommandHandler` 这一层；两次都未读取 `CommandWrapperBuilder.java`、`CommandHandlerProvider.java`、`LoanApplicationApprovalCommandHandler.java`，也没有搜索这些类名。答案停在“经命令总线走到服务层”的概括，未说明命令路由机制。

**结论可信度**：两次答案与源码抽查一致；都主动指出一个未核实闭环：`FineractClient.createLoan` 的 payload 只有 `submittedOnDate`、没有 `approvedOnDate`，而状态机只允许 200→300 放款，因此中台若未补审批命令，disburse 可能被拒绝并停留在 RETRY_NEEDED。该疑点与当前冻结源码相符，且答案标明“未核实闭环”，没有把它当成已确认故障。引用没有出现读取与搜索都未交付的路径/行号。

**观察**：

- 两次都花了大量调查在中台与渠道层（none r2：loan-middle 10 次读取/23 次搜索，channel 2/3；none r1：loan-middle 6/11，channel 2/4），但题目要求的是 Fineract 借据状态与 REST 触发链；这部分跨系统内容不算错误，却挤占了关键命令路由的调查预算。
- 无效调查代理 22/30（占工具调用 26% 左右），空搜索 8/16、搜索后未读候选 14/14；none r2 的 61 次搜索中有 16 次无结果，是四次运行中最高的浪费。
- 两次重复都漏同一组命令路由文件，说明这不是随机波动，而是当前 Pi 对该类“REST→命令→处理器”关系链的系统性缺口；同时说明“关键文件清单”里的导航信息本身没有进入模型，模型也未从问题中自行推出需要找 Handler。
- backbone r1 读到 4/6：多读了 `CommandWrapperBuilder.java`，但答案仍未提到该文件；仍缺 `CommandHandlerProvider.java` 与 `LoanApplicationApprovalCommandHandler.java`，check 5 依然未覆盖。该次运行同时把 33 个文件、248 次工具、107 次搜索和 6.63M Token 投入调查，空搜索 35 次、重复调用 4 次、无效代理 55，是三组已完成的 lifecycle 运行中成本最高的一次。Business Context 没有带来该关系链的覆盖，反而伴随显著的范围扩张；单次样本，不能据此断言因果。

## 针对性修复：命令链核实（2026-11-16）

**根因**：Pi 启动时使用 `--no-context-files` 和自建 system prompt，工作区已有的调查契约（`CLAUDE.md` 中“找到第一个入口并不代表问题已回答”“根据实际调用、事件、路由继续调查”）没有进入 Pi；原提示只有通用工具规则，模型在 lifecycle 题上停在“经命令总线/服务层”，没有继续定位 Handler。

**改动**：

- `PiAgentRuntime._system_prompt` 增加关系清单与分层链路要求：入口、路由/命令/事件、实现、状态变化、例外逐项核实；Spring 沿 Controller → Command/事件 → Handler/注册表 → Service/状态机，Angular 沿组件 → Service → 拦截器/路由；题目要求的各项有源码依据后停止，不为背景扩大范围。
- `usage.systemPromptSha256` 记录本轮系统提示指纹（含资料模式）；修复批次为 `c12fd22d7ef4c3a5`。

**修复后运行**：

| 组 | 重复 | 状态 | 关键文件 | 末键后工具/搜索 | 模型调用 | 工具调用 | 搜索 | 空搜索 | 无效代理 | 引用 确认/搜索见/未交付/无行号 | Token | 耗时 s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| none | 1 | completed | 6/6 | 23/19 | 74 | 104 | 64 | 16 | 26 | 30/8/0/1 | 3,413,575 | 542.7 |
| none | 2 | completed | 4/6 | 108/75 | 105 | 139 | 86 | 30 | 52 | 31/4/1/1 | 6,162,668 | 895.6 |
| backbone | 1 | failed（额度） | 5/6 | 60/40 | 100 | 123 | 71 | 12 | 41 | — | — | 813.1 |
| backbone | 2 | failed（额度） | 0/6 | — | 4 | 0 | 0 | 0 | 0 | — | — | 16.2 |

**结论**：

- 逐项复核后，两轮都是 4 项通过、检查项 5 部分通过（[checks.md](../../../validation-projects/complex-loan/evaluations/pi-lifecycle-fix-ab-20261116/raw-traces/checks.md)）。r1 覆盖 `CommandWrapperBuilder → 命令总线 → @CommandType → DisburseLoanCommandHandler`，但缺少 `LoanApplicationApprovalCommandHandler` 的审批分发；r2 覆盖命令总线、注册表和审批/放款 Handler，但没有明确指出 `CommandWrapperBuilder`。因此“2/2 命令链覆盖”只能降级为关系调查改善，不能判定 check 5 完整通过。
- 第二轮关键文件 4/6（`CommandWrapperBuilder`、`CommandHandlerProvider` 未 `read`，但通过 grep 输出核对了相关行；`LoanApplicationApprovalCommandHandler` 已读），成本 6.16M Token、895.6 秒，接近超时；末键后仍有 108 次工具、30 次空搜索。修复提升的是关系覆盖，不是成本或停止稳定性。
- `backbone` 两轮返回上游 `502 ... code 1005 exceed quota limit`；第一轮在失败前已完成 100 次模型调用但无最终答案。这是供应商额度问题，不能算 Business Context 或修复本身的效果。
- 修复后第二个 `none` 答案有 1 条错误模块路径引用（`fineract-loan/.../LoanTransactionValidatorImpl.java`；实际文件在 `fineract-provider/...`，同一内容另以 basename 引用并被读取确认），属于路径精度问题，不是凭空内容。

**下一步**：额度恢复后补跑基线剩余 9 次与修复后的 `backbone` 两轮；再用 `native-frontend` 做一次修复后回归，确认新增提示没有扩大前端题成本。

## 模型切换：gpt-6-luna（2026-11-16）

用户要求把问答模型改为 `deepseek/gpt-6-luna`。已写入 `.env.example`，本地 `.env` 同时设置 `BUSINESS_CODE_PI_MODEL=deepseek/gpt-6-luna`；Pi CLI 的 `--model` 探针和生产评测链路均已确认实际模型为 `deepseek/gpt-6-luna`。

为确认切换后的完整链路，使用生产 `QueryService + PiAgentRuntime` 跑了一次 `native-frontend / none / r1`（结果快照见 `validation-projects/complex-loan/evaluations/pi-gpt6luna-smoke-20261116/`）：

| 指标 | gpt-6-luna 冒烟 | glm-5.3-flash 基线（none r1/r2） |
| --- | --- | --- |
| 状态 | completed | completed ×2 |
| 模型调用 / 工具 | 13 / 11 | 32/52、40/58 |
| 读取文件 / 关键文件 | 2 / 2 个（清单 8 个） | 19–22 / 7–8 个 |
| Token / 耗时 | 187,615 / 436.1 s | 744k–1.13M / 234–292 s |
| 引用（去重）/确认/搜索见/未交付 | 15 / 4 / 11 / 0 | 45/22/18/0、44/33/9/0 |
| 原始路径格式（绝对/缺段/重复根/无法归一化） | 15 / 2 / 2 / 0 | 0 / 0 / 0 / 0 |

答案覆盖创建入口和 `POST /loans`，但审批/放款只覆盖批量任务页路径，未读 `approve-loan.component.ts`、`disburse.component.ts`、`make-repayment.component.ts`、`ApiPrefixInterceptor`、`AuthenticationInterceptor` 和 `environment.ts`；答案自述批量 endpoint、还款 command、API 前缀 provider 未核实。逐项复核为 1 项通过、4 项部分通过、1 项未通过（[checks.md](../../../validation-projects/complex-loan/evaluations/pi-gpt6luna-smoke-20261116/raw-traces/checks.md)），明显浅于同题 glm 结果。

引用解析此前把 Markdown 链接的文字和目标各计一次，17 个链接被计成 34 条；按独立源码引用去重后是 15 条（两处相同 path+line 重复）。17 个目标都是临时绝对路径，其中缺段 2 条、重复 `repos/.../CONV-.../repos/...` 前缀 2 条，合计 4 条格式错误；归一化后 0 条无法映射。生产运行现在会在最终答案层把这些路径改成工程相对路径，原始 Trace 保留模型原文。该问题不影响模型选择本身，但旧答案的链接可读性和核验稳定性不合格。

结论：`gpt-6-luna` 的生产链路可用，但这次冒烟不支持“与 glm-5.3-flash 质量等价”的假设；它更便宜、调用更少，但调查深度、关键文件覆盖和引用精度都下降。已有 glm 批次不能续跑或混算，gpt-6-luna 需要重新建立基线后再判断是否适合作为默认问答模型。另外，本冒烟与 glm 基线不仅模型不同，系统提示也已变更，现有数据只能说明退化迹象，不能用于严格证明模型优劣。

### Codemode 执行统计

全部已归档 Trace 共记录 117 次 codemode 调用，114 次完成、3 次错误。glm 的复杂 lifecycle 调查中 codemode 多次成功组合 `read/grep/find`；gpt-6-luna 的单次失败是模型生成了不完整的 JavaScript（`tools.ls({...` 未闭合），执行器返回 `SyntaxError` 且未执行任何内部工具，Pi 随后改用 ls/grep/read。因此 Codemode 的整体可用性可以验收通过；单次语法错误是工具参数生成问题，不是扩展加载或检索工具不可用。

### 评测与引用体系修正（2026-11-16）

- `verify_references` 现在按 Markdown 链接和独立源码引用去重，同一 path+line 只保留一条；支持缺段、重复 `repos/...` 前缀的路径归一化，并把绝对工作区路径还原为工程相对路径。
- Pi 最终答案输出前会调用 `normalize_answer_paths`，只改写非规范的绝对路径，不改变短文件名或正常相对引用；原始模型答案仍保留在 Trace 调用记录中。
- `evaluate_pi.py` 的 grep 解析只取行首路径，不再把 `path-260- content` 上下文行或内容片段当成候选文件；引用统计新增总数、独立引用去重、格式问题（绝对/缺段/重复根/无法归一化/其中格式错误数）。
- 新增 `review` 命令：逐项复核使用 `pass/partial/fail`，要求答案原句必须出现在 `answer.md`、证据必须是工程相对路径，并生成单次与批次 `checks.md`。
- Trace 元数据区分“模型调用摘要”和“完整请求快照”：Pi 的调用记录不再把空的 `messages/tools` 标成 `modelSnapshotAvailable`，并记录 `recordKind=message_summary`、`requestSnapshotAvailable=false`、`snapshotStage=pi_message_summary`；轨迹页会提示只保存摘要。
- 这些改动只修正评测与展示，不改变 Pi 的调查策略；也不能把修正后的指标当成答案质量提升。下一步仍需在同源码、同提示、同模型设置下重跑配对测试。

## 下一步（2026-11-16 复核后）

1. 用修正后的评测器重跑同配置配对：固定源码、提示指纹、模型与评分标准；gpt-6-luna 需要新批次，不能与 glm 批次混算或续跑。
2. 额度恢复后补跑基线剩余 9 次与修复后的 `backbone` 两轮；先补同模型对照，再判断 Business Context 是否有价值。
3. 对两类真实失败分别处理：遗漏关键关系（如 lifecycle 审批 Handler）用轻量的目标覆盖检查；证据已充分却继续调查（末键后仍大量搜索）先分析后续工具是否带来新证据，不先开发新的 Coordinator 或完整 Agent Loop。
4. 完成上述对照后，再决定是否启用 Evidence Ledger / Answer Closure；当前不作为默认前置。
