# 逐项检查复核：native-lifecycle / none / r1

- Run：`RUN-d8d7d719928e486ba7ea89efb228342a`；模型：zcode/glm-5.3-flash；状态：completed
- 结论：通过 4、部分通过 1、未通过 0

## 1. 状态定义在 LoanStatus 枚举（fineract-core 模块），列出 SUBMITTED_AND_PENDING_APPROVAL(100)、APPROVED(200)、ACTIVE(300)、CLOSED_OBLIGATIONS_MET(600) 等关键状态

**结论：通过**

答案原句：
> 100 提交 → 200 审批 → 300 生效
> 600 结清 | 700 超额

源码/资料依据：
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:27-37

说明：答案列出 Fineract 借据层 100/200/300/600/700 状态，并引用 LoanStatus 枚举。

## 2. 流转校验在 DefaultLoanLifecycleStateMachine（fineract-loan 模块），按 LoanEvent 事件判定，不是在 REST 层硬编码

**结论：通过**

答案原句：
> **1. Fineract 借据层：`DefaultLoanLifecycleStateMachine`（事件×当前状态的白名单校验）**

源码/资料依据：
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:110-232
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanEvent.java:26

说明：答案说明状态机按 LoanEvent 与当前状态做白名单校验，不是 REST 层硬编码。

## 3. LOAN_APPROVED 只允许从 SUBMITTED_AND_PENDING_APPROVAL 转入 APPROVED；LOAN_DISBURSED 从 APPROVED（或结清后）转入 ACTIVE

**结论：通过**

答案原句：
> `LOAN_APPROVED`：仅 100→200（:124-129）
> `LOAN_DISBURSED`：仅 200（或 600）→300（:134-142）

源码/资料依据：
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:124-142

说明：答案明确给出两个状态迁移的来源状态与目标状态。

## 4. 创建走 POST /loans（LoansApiResource），审批/放款走 POST /loans/{loanId}?command=approve|disburse

**结论：通过**

答案原句：
> `fineract.createLoan` 调 `POST /loans` 创建借据（Fineract 借据落入 100 状态，`FineractClient.java:55-78`）
> Fineract 侧链路：REST 入口 `POST /v1/loans/{loanId}?command=...` 的 `stateTransitions`

源码/资料依据：
- repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:608-630
- repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1313-1345
- repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractClient.java:55-78
- repos/loan-middle-repo/src/main/java/com/rsd/middle/fineract/FineractClient.java:89-95

说明：答案给出创建走 POST /loans、放款走 POST /loans/{id}?command=disburse 的 REST 链路；审批入口在本轮中台链路缺位，但检查项要求的 REST 动作已说明。

## 5. command 经 CommandWrapperBuilder 构造为 entity=LOAN 的命令，按 @CommandType 注解路由到对应 Handler（如 LoanApplicationApprovalCommandHandler）

**结论：部分通过**

答案原句：
> `CommandWrapperBuilder.disburseLoanApplication`（`CommandWrapperBuilder.java:1164-1171`）→ 命令总线 `PortfolioCommandSourceWritePlatformServiceImpl.logCommandSource`（`PortfolioCommandSourceWritePlatformServiceImpl.java:54-78`）→ 按 `@CommandType(entity="LOAN", action=...)` 注册表路由（`CommandHandlerProvider.java:41-62,91-100`）→ `DisburseLoanCommandHandler`

源码/资料依据：
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/CommandWrapperBuilder.java:1164-1171
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/PortfolioCommandSourceWritePlatformServiceImpl.java:54-78
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/provider/CommandHandlerProvider.java:91
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/DisburseLoanCommandHandler.java:35

说明：覆盖 CommandWrapperBuilder、命令总线、@CommandType 注册与放款 Handler；但没有给出检查项示例中的 LoanApplicationApprovalCommandHandler 审批分发，审批命令链未闭环。

## 其它问题

- 关键文件 6/6 已读取，但答案对审批命令 Handler 的分发说明缺失。
