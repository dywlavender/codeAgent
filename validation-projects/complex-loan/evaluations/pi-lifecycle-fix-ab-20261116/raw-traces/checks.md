# Pi 逐项检查复核汇总

| 案例 | 模式 | 重复 | 通过 | 部分通过 | 未通过 |
| --- | --- | --- | --- | --- | --- |
| native-lifecycle | none | 1 | 4 | 1 | 0 |
| native-lifecycle | none | 2 | 4 | 1 | 0 |

## native-lifecycle / none / r1

1. **通过** — 状态定义在 LoanStatus 枚举（fineract-core 模块），列出 SUBMITTED_AND_PENDING_APPROVAL(100)、APPROVED(200)、ACTIVE(300)、CLOSED_OBLIGATIONS_MET(600) 等关键状态
   - 答案：100 提交 → 200 审批 → 300 生效 … 600 结清 | 700 超额
   - 依据：repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:27-37
   - 说明：答案列出 Fineract 借据层 100/200/300/600/700 状态，并引用 LoanStatus 枚举。
2. **通过** — 流转校验在 DefaultLoanLifecycleStateMachine（fineract-loan 模块），按 LoanEvent 事件判定，不是在 REST 层硬编码
   - 答案：**1. Fineract 借据层：`DefaultLoanLifecycleStateMachine`（事件×当前状态的白名单校验）**
   - 依据：repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:110-232；repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanEvent.java:26
   - 说明：答案说明状态机按 LoanEvent 与当前状态做白名单校验，不是 REST 层硬编码。
3. **通过** — LOAN_APPROVED 只允许从 SUBMITTED_AND_PENDING_APPROVAL 转入 APPROVED；LOAN_DISBURSED 从 APPROVED（或结清后）转入 ACTIVE
   - 答案：`LOAN_APPROVED`：仅 100→200（:124-129） … `LOAN_DISBURSED`：仅 200（或 600）→300（:134-142）
   - 依据：repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:124-142
   - 说明：答案明确给出两个状态迁移的来源状态与目标状态。
4. **通过** — 创建走 POST /loans（LoansApiResource），审批/放款走 POST /loans/{loanId}?command=approve|disburse
   - 答案：`fineract.createLoan` 调 `POST /loans` 创建借据（Fineract 借据落入 100 状态，`FineractClient.java:55-78`） … Fineract 侧链路：REST 入口 `POST /v1/loans/{loanId}?command=...` 的 `stateTransitions`
   - 依据：repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:608-630；repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1313-1345；repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractClient.java:55-78；repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractClient.java:89-95
   - 说明：答案给出创建走 POST /loans、放款走 POST /loans/{id}?command=disburse 的 REST 链路；审批入口在本轮中台链路缺位，但检查项要求的 REST 动作已说明。
5. **部分通过** — command 经 CommandWrapperBuilder 构造为 entity=LOAN 的命令，按 @CommandType 注解路由到对应 Handler（如 LoanApplicationApprovalCommandHandler）
   - 答案：`CommandWrapperBuilder.disburseLoanApplication`（`CommandWrapperBuilder.java:1164-1171`）→ 命令总线 `PortfolioCommandSourceWritePlatformServiceImpl.logCommandSource`（`PortfolioCommandSourceWritePlatformServiceImpl.java:54-78`）→ 按 `@CommandType(entity="LOAN", action=...)` 注册表路由（`CommandHandlerProvider.java:41-62,91-100`）→ `DisburseLoanCommandHandler`
   - 依据：repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/CommandWrapperBuilder.java:1164-1171；repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/PortfolioCommandSourceWritePlatformServiceImpl.java:54-78；repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/provider/CommandHandlerProvider.java:91；repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/DisburseLoanCommandHandler.java:35
   - 说明：覆盖 CommandWrapperBuilder、命令总线、@CommandType 注册与放款 Handler；但没有给出检查项示例中的 LoanApplicationApprovalCommandHandler 审批分发，审批命令链未闭环。

## native-lifecycle / none / r2

1. **通过** — 状态定义在 LoanStatus 枚举（fineract-core 模块），列出 SUBMITTED_AND_PENDING_APPROVAL(100)、APPROVED(200)、ACTIVE(300)、CLOSED_OBLIGATIONS_MET(600) 等关键状态
   - 答案：状态枚举：`repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:22-35` —— 100 SUBMITTED_AND_PENDING_APPROVAL / 200 APPROVED / 300 ACTIVE / 400 撤回 / 500 拒绝 / 600 义务履行结清 / 601 核销 / 602 重 scheduling 结清 / 700 OVERPAID。
   - 依据：repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:22-35
   - 说明：答案完整列出 LoanStatus 关键状态和枚举路径。
2. **通过** — 流转校验在 DefaultLoanLifecycleStateMachine（fineract-loan 模块），按 LoanEvent 事件判定，不是在 REST 层硬编码
   - 答案：`getNextStatus()`（:110-218）是流转白名单：如 LOAN_APPROVED 只允许 from=100（:117-121）、LOAN_DISBURSED 只允许 from∈{200,600}（:130-136）
   - 依据：repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:110-218；repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanEvent.java:20-50
   - 说明：答案说明状态机按 LoanEvent 与当前状态白名单校验，非法来源返回 null。
3. **通过** — LOAN_APPROVED 只允许从 SUBMITTED_AND_PENDING_APPROVAL 转入 APPROVED；LOAN_DISBURSED 从 APPROVED（或结清后）转入 ACTIVE
   - 答案：`getNextStatus()`（:110-218）是流转白名单：如 LOAN_APPROVED 只允许 from=100（:117-121）、LOAN_DISBURSED 只允许 from∈{200,600}（:130-136）
   - 依据：repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:117-136
   - 说明：答案明确给出审批与放款的合法来源状态。
4. **通过** — 创建走 POST /loans（LoansApiResource），审批/放款走 POST /loans/{loanId}?command=approve|disburse
   - 答案：Fineract：`POST /loans`（`LoansApiResource.java:549-584` … Fineract 入口：`POST /loans/{id}?command=approve` → `stateTransitions()` :1323-1324 装配 `approveLoanApplication` … Fineract 入口：`POST /loans/{id}?command=disburse`（`stateTransitions()` :1325-1326）
   - 依据：repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:549-584；repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1323-1326
   - 说明：创建、审批、放款三个 REST 入口均有答案原句和源码依据。
5. **部分通过** — command 经 CommandWrapperBuilder 构造为 entity=LOAN 的命令，按 @CommandType 注解路由到对应 Handler（如 LoanApplicationApprovalCommandHandler）
   - 答案：- `LoansApiResource.stateTransitions()` 把 `?command=` 参数装配成 `CommandWrapper` … `CommandHandlerProvider.getHandler()`（`fineract-core/.../provider/CommandHandlerProvider.java:91`）按 `@CommandType(entity, action)` 注解匹配 Handler——即 LOAN/CREATE→`LoanApplicationSubmittalCommandHandler`、LOAN/APPROVE→`LoanApplicationApprovalCommandHandler`、LOAN/DISBURSE→`DisburseLoanCommandHandler`
   - 依据：repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1313-1350；repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/PortfolioCommandSourceWritePlatformServiceImpl.java:54-77；repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/SynchronousCommandProcessingService.java:100-147；repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/provider/CommandHandlerProvider.java:91；repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/LoanApplicationApprovalCommandHandler.java:32；repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/DisburseLoanCommandHandler.java:35
   - 说明：覆盖命令装配、命令总线、@CommandType 注册和审批/放款 Handler；但没有明确指出 CommandWrapperBuilder，且本轮未读取该类，检查项要求的构造节点缺位。
