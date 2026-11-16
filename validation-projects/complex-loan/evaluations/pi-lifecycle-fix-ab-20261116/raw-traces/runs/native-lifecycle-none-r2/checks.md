# 逐项检查复核：native-lifecycle / none / r2

- Run：`RUN-f69cd00d55d147ccb218335746d1ed3c`；模型：zcode/glm-5.3-flash；状态：completed
- 结论：通过 4、部分通过 1、未通过 0

## 1. 状态定义在 LoanStatus 枚举（fineract-core 模块），列出 SUBMITTED_AND_PENDING_APPROVAL(100)、APPROVED(200)、ACTIVE(300)、CLOSED_OBLIGATIONS_MET(600) 等关键状态

**结论：通过**

答案原句：
> 状态枚举：`repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:22-35` —— 100 SUBMITTED_AND_PENDING_APPROVAL / 200 APPROVED / 300 ACTIVE / 400 撤回 / 500 拒绝 / 600 义务履行结清 / 601 核销 / 602 重 scheduling 结清 / 700 OVERPAID。

源码/资料依据：
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:22-35

说明：答案完整列出 LoanStatus 关键状态和枚举路径。

## 2. 流转校验在 DefaultLoanLifecycleStateMachine（fineract-loan 模块），按 LoanEvent 事件判定，不是在 REST 层硬编码

**结论：通过**

答案原句：
> `getNextStatus()`（:110-218）是流转白名单：如 LOAN_APPROVED 只允许 from=100（:117-121）、LOAN_DISBURSED 只允许 from∈{200,600}（:130-136）

源码/资料依据：
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:110-218
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanEvent.java:20-50

说明：答案说明状态机按 LoanEvent 与当前状态白名单校验，非法来源返回 null。

## 3. LOAN_APPROVED 只允许从 SUBMITTED_AND_PENDING_APPROVAL 转入 APPROVED；LOAN_DISBURSED 从 APPROVED（或结清后）转入 ACTIVE

**结论：通过**

答案原句：
> `getNextStatus()`（:110-218）是流转白名单：如 LOAN_APPROVED 只允许 from=100（:117-121）、LOAN_DISBURSED 只允许 from∈{200,600}（:130-136）

源码/资料依据：
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:117-136

说明：答案明确给出审批与放款的合法来源状态。

## 4. 创建走 POST /loans（LoansApiResource），审批/放款走 POST /loans/{loanId}?command=approve|disburse

**结论：通过**

答案原句：
> Fineract：`POST /loans`（`LoansApiResource.java:549-584`
> Fineract 入口：`POST /loans/{id}?command=approve` → `stateTransitions()` :1323-1324 装配 `approveLoanApplication`
> Fineract 入口：`POST /loans/{id}?command=disburse`（`stateTransitions()` :1325-1326）

源码/资料依据：
- repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:549-584
- repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1323-1326

说明：创建、审批、放款三个 REST 入口均有答案原句和源码依据。

## 5. command 经 CommandWrapperBuilder 构造为 entity=LOAN 的命令，按 @CommandType 注解路由到对应 Handler（如 LoanApplicationApprovalCommandHandler）

**结论：部分通过**

答案原句：
> - `LoansApiResource.stateTransitions()` 把 `?command=` 参数装配成 `CommandWrapper`
> `CommandHandlerProvider.getHandler()`（`fineract-core/.../provider/CommandHandlerProvider.java:91`）按 `@CommandType(entity, action)` 注解匹配 Handler——即 LOAN/CREATE→`LoanApplicationSubmittalCommandHandler`、LOAN/APPROVE→`LoanApplicationApprovalCommandHandler`、LOAN/DISBURSE→`DisburseLoanCommandHandler`

源码/资料依据：
- repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1313-1350
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/PortfolioCommandSourceWritePlatformServiceImpl.java:54-77
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/service/SynchronousCommandProcessingService.java:100-147
- repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/commands/provider/CommandHandlerProvider.java:91
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/LoanApplicationApprovalCommandHandler.java:32
- repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/DisburseLoanCommandHandler.java:35

说明：覆盖命令装配、命令总线、@CommandType 注册和审批/放款 Handler；但没有明确指出 CommandWrapperBuilder，且本轮未读取该类，检查项要求的构造节点缺位。

## 其它问题

- 关键文件 4/6，CommandWrapperBuilder 未读取；答案只概括为 CommandWrapper。
- 答案对 CommandHandlerProvider 等使用 `fineract-core/.../` 省略路径；另有 1 条 `LoanTransactionValidatorImpl` 模块路径错误（实际在 fineract-provider），其内容在另一路径被读取确认。
