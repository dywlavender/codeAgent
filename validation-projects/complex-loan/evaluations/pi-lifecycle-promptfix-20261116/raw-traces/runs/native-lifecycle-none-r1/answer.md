调查完成。以下是基于源码的完整结论。

# 贷款申请从提交到放款的状态流转

## 一、总览：三层状态体系，同一笔业务并行推进

| 层 | 状态机 | 定义位置 |
|---|---|---|
| Fineract 贷款账户（借据层） | `LoanStatus`：100 提交 / 200 审批 / 300 生效 / 600 结清 / 700 超额 等 | `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:24-37` |
| 中台提款业务单 | `PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING → SUCCESS / FAILED`（提交前可 `CANCELLED`） | `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java:24`（`PaymentStatus`、`BookingStatus` 子状态在同文件 26、28 行） |
| 渠道申请单（展示状态） | 中台处理中状态对外统一为 `PROCESSING`，成功/失败直传 | `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:205-210`（`displayOf`） |

主流转（对应需求 §四约定）：**创建借据（100 提交）→ approve（200 审批）→ disburse（300 生效）→ 还款入账后自动判定 → 600 结清**；非法跳转由状态机拒绝。外部付款成功 ≠ 放款完成，"外部已付、入账未完成"在中台是显式的 `EXTERNAL_SUCCESS_BOOKING_PENDING`。

## 二、状态定义与流转校验的实现位置

**1. 状态定义**：`LoanStatus.java:24-37` 枚举编码 100/200/300/303/304/400/500/600/601/602/700，并提供 `isApproved()`/`isClosed()` 等谓词（57-131 行）。

**2. 流转校验（核心）**：`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java`
- `getNextStatus()`（110-224 行）是守卫表：`LOAN_APPROVED` 仅允许来自 100（124-127 行）；`LOAN_DISBURSED` 仅允许来自 200/600（134 行）；`REPAID_IN_FULL` 仅允许来自 300/700（161 行）；`LOAN_REPAYMENT_OR_WAIVER` 可把 600/700 拉回 300（156 行）。目标态不在允许集内则返回 null，状态保持不变——即"非法跳转被拒绝"。
- `determineAndTransition()`（55-70 行）+ `determineTransitionFromActive()`（约 377 行）：还款后依据 `isRepaidInFull && isAllChargesPaid` 自动判定转 600，多缴转 700，**没有显式命令**，由还款入账触发。

**3. 事件产生点**（状态机被谁调用）：
- 创建：`Loan.java:560` 直接置 `SUBMITTED_AND_PENDING_APPROVAL`（100），构造即提交。
- 审批：`LoanScheduleAssembler.java:1506` `transition(LoanEvent.LOAN_APPROVED, loan)`。
- 放款：`LoanWritePlatformServiceJpaRepositoryImpl.java:596` `transition(LoanEvent.LOAN_DISBURSED, loan)`；放款前 `canDisburse()`（3521-3532 行）先做 `dryTransition` 预检。
- 还款：`LoanAccountDomainServiceJpa.makeRepayment` → `LoanDownPaymentHandlerServiceImpl.java:169` `determineAndTransition(...)` 完成结清自动判定。
- 命令与处理器按 `@CommandType(entity="LOAN", action=...)` 注册，经 `CommandHandlerProvider.getHandler()`（`fineract-core/.../commands/provider/CommandHandlerProvider.java:91`）由 `SynchronousCommandProcessingService.java:147,316` 分发。

**4. 入口级状态校验**：`LoanTransactionValidatorImpl.validateDisbursement()`（`fineract-provider/.../serialization/LoanTransactionValidatorImpl.java:123,172-176`）要求单笔借据"已审批未放款"，否则报 `loan.disbursal.account.is.not.approve.not.disbursed.state`。

## 三、REST 入口 → 状态变化逐动作说明

**① 客户提交（渠道 → 中台 → Fineract，借据 100）**
- 前端入口：`ChannelController.java:28` `POST /api/drawdowns` → `ChannelService.applyDrawdown`（`ChannelService.java:79-107`）做渠道规则校验（金额/期限上限），再两步调中台：quote → apply。
- 中台入口：`DrawdownController.java:66` `POST /middle/api/v1/drawdowns` → `DrawdownService.apply`（`DrawdownService.java:106-141`）：额度逐层预占 → `fineract.createLoan(...)` 创建借据 → `submitPayment` 提交付款，业务单置 `PROCESSING`。
- Fineract 侧：`FineractClient.java:56-88` `POST /loans` → `LoansApiResource.java:549-581` → `LoanApplicationSubmittalCommandHandler`（`@CommandType(entity="LOAN", action="CREATE")`）→ `submitApplication`（`LoanApplicationWritePlatformServiceJpaRepositoryImpl.java:135`）→ Loan 初始状态 100（`Loan.java:560`）。

**② 审批动作（100 → 200）**
- REST：`POST /loans/{id}?command=approve` → `LoansApiResource.stateTransitions`（`LoansApiResource.java:627-656` 入口，1313-1345 行把 `approve` 映射为 `approveLoanApplication` CommandWrapper）→ `LoanApplicationApprovalCommandHandler`（`@CommandType(entity="LOAN", action="APPROVE")`）→ `approveApplication`（`LoanApplicationWritePlatformServiceJpaRepositoryImpl.java:557`）→ `LoanScheduleAssembler.assembleLoanApproval`（`LoanScheduleAssembler.java:1506`）执行 `transition(LOAN_APPROVED)`。状态机守卫：仅 100 可审批（`DefaultLoanLifecycleStateMachine.java:124`）。
- 运营前端触发：mifos-web 审批按钮 `loan-account-actions.component.html:82-83` → `ApproveLoanComponent.submit()`（`approve-loan.component.ts:129-145`）→ `LoansService.loanActionButtons`（`loans.service.ts:339-342`，`POST /loans/{id}?command=approve`）。

**③ 放款动作（200 → 300，受托支付由中台编排）**
- 中台在外部付款成功后入账：回调 `POST /middle/api/v1/callbacks/external-result`（`ExternalResultController.java:55`）或补偿查单（`CompensationTask.java:23` 起的 `@Scheduled` 任务）→ `DrawdownService.onPaymentResult`（`DrawdownService.java:163-183`）：付款成功 → 业务单转 `EXTERNAL_SUCCESS_BOOKING_PENDING`、`bookingStatus=TO_BOOK` → `book()`。
- `book()`（`DrawdownService.java:190-211`）：先 `fineract.isLoanActive()` 幂等防重，再 `fineract.disburseLoan()` → `POST /loans/{id}?command=disburse`（`FineractClient.java:90-98`）→ `stateTransitions` 映射 `disburse` → `DisburseLoanCommandHandler`（`@CommandType(entity="LOAN", action="DISBURSE")`）→ `disburseLoan()`（`LoanWritePlatformServiceJpaRepositoryImpl.java:314`）：`validateDisbursement` 状态/金额校验（322 行）→ `canDisburse` 预检（370 行）→ 重算还款计划 → `transition(LOAN_DISBURSED)`（596 行）借据转 300 → 中台置业务单 `SUCCESS`、`BOOKED`，额度逐层预占转实占（`limitService.confirmOccupy`）。入账失败则 `RETRY_NEEDED` 由补偿任务重试。

**④ 还款与结清（300 → 600/700，自动判定）**
- `ChannelController.java:37` `POST /api/repayments` → 中台 `RepaymentController.java:34` → `RepaymentService.apply/onDeductionResult/book`（`RepaymentService.java:64-140`）：扣款成功后 `fineract.repayLoan()` → `POST /loans/{id}/transactions?command=repayment`（`FineractClient.java:117-127`）→ `LoanRepaymentCommandHandler` → `makeLoanRepayment...`（`LoanWritePlatformServiceJpaRepositoryImpl.java:1072`）→ `LoanAccountDomainServiceJpa.makeRepayment`（166/215 行）→ `LoanDownPaymentHandlerServiceImpl.java:169` `determineAndTransition`：还清即 600，多缴即 700（`DefaultLoanLifecycleStateMachine.determineTransitionFromActive`）；中台读取分配明细、仅按本金部分恢复额度（`RepaymentService.java:118-125`）。
- 渠道查询 `GET /api/drawdowns/{channelRefNo}`（`ChannelController.java:33`）→ `drawdownDetail` 聚合展示状态并保留 `middleRawStatus`（`ChannelService.java:112-122,205-210`）。

## 四、已发现的矛盾点（证据缺口，请留意）

`FineractClient.java:20-21` 注释称"创建时直接提交为经审批口径（submittedOnDate+approvedOnDate）"，**但 `createLoan` 的 payload（56-77 行）只有 `submittedOnDate`，没有任何 `approvedOnDate` 字段，中台代码也没有任何调用 `command=approve` 的地方**（全库 grep `approve` 仅命中授信审批场景）。而 Fineract 侧创建后固定为 100（`Loan.java:560`），且放款入口校验要求 200 状态（`LoanTransactionValidatorImpl.java:172-176`）。也就是说，按当前中台源码直接执行 `command=disburse`，会被 Fineract 拒绝（除非创建时人工带上 `approvedOnDate` 且装配链路确实消费它——`LoanAssemblerImpl.assembleFrom(JsonCommand)` 前 253 行未读到审批字段消费逻辑，此处**未完全核实**装配器后半段，标注为证据缺口）。结论：**源码层面"审批"这一步在中台链路中缺位，100→200 的转换只能由 mifos-web 运营页（或手工调 `command=approve`）补齐**，这与需求"中台创建借据后按命令完成放款"的描述存在实现差距。