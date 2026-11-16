调查完成。以下按"两层账户状态"（Fineract 借据状态 + 中台业务单状态）梳理流转、代码位置与 REST 触发链。

# 一、状态流转总览

一笔提款在系统中同时驱动两条状态线，最终在 Fineract 借据上收敛为标准贷款生命周期：

```
渠道展示状态:   PROCESSING ──────────────────────────→ SUCCESS | FAILED
                (displayOf 合并映射)                       (直传)

中台业务单:     PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING → SUCCESS | FAILED
   (DrawdownOrder.Status)   │付款失败: FAILED+释放预占        │入账失败: RETRY_NEEDED(补偿重试)
   附属: PaymentStatus(TO_SUBMIT/PROCESSING/SUCCESS/FAILED)
         BookingStatus(NONE/TO_BOOK/BOOKED/RETRY_NEEDED)

Fineract 借据:  100 提交 → (200 审批) → 300 生效(ACTIVE) → 600 结清 / 700 超额
   (LoanStatus)   ↑创建借据     ↑本项目未单独调用      ↑disburse命令     ↑还款后自动判定
```

外部模拟服务侧是简单终态：`PROCESSING → SUCCESS | FAILED`（`repos/external-simulator-repo/src/main/java/com/rsd/simulator/ExtTransaction.java:19-25`）。

# 二、状态定义在哪实现

**1. Fineract 借据状态（标准定义）**
- 状态枚举：`repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:22-35` —— 100 SUBMITTED_AND_PENDING_APPROVAL / 200 APPROVED / 300 ACTIVE / 400 撤回 / 500 拒绝 / 600 义务履行结清 / 601 核销 / 602 重 scheduling 结清 / 700 OVERPAID。
- 事件枚举：`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanEvent.java:20-50`（LOAN_CREATED、LOAN_APPROVED、LOAN_DISBURSED、REPAID_IN_FULL、LOAN_OVERPAYMENT 等）。

**2. 中台业务单状态**
- `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java:20-30`：`Status{PROCESSING, EXTERNAL_SUCCESS_BOOKING_PENDING, SUCCESS, FAILED, CANCELLED}` + `PaymentStatus` + `BookingStatus` 三组枚举；`terminal()` 判定终态（:44-46）。
- 还款订单：`repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentOrder.java:14-19`（`Status{PROCESSING,SUCCESS,FAILED}`、`DeductionStatus`）。

**3. 渠道展示状态**
- `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelApplicationRecord.java:19-24`：`displayStatus`（对外合并视图）+ `middleRawStatus`（原始状态留档）。

# 三、流转校验在哪实现

**Fineract 状态机（核心校验）**：`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java`
- `getNextStatus()`（:110-218）是流转白名单：如 LOAN_APPROVED 只允许 from=100（:117-121）、LOAN_DISBURSED 只允许 from∈{200,600}（:130-136）、REPAID_IN_FULL 只允许 from∈{300,700}（:158-161）。非法来源返回 null，状态不动。
- `internalTransition()`（:82-105）执行置状态并发布 `LoanStatusChangedBusinessEvent`（:90）。
- `determineAndTransition()`（:55-73）：还款/调整类交易后按余额**自动判定**状态——`determineTransitionFromActive()`（:349-363）在 `isRepaidInFull && isAllChargesPaid` 时转 600（REPAID_IN_FULL），`isOverpaid` 时转 700（LOAN_OVERPAYMENT）。
- `dryTransition()` 试算：被用作前置校验——审批防重（`LoanApplicationValidator.java:2153-2159` "already approved"）、放款可行性（`LoanWritePlatformServiceJpaRepositoryImpl.java:3521-3529` `canDisburse`）、容差结清（:3311-3314）。

**放款的状态前置校验**：`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/serialization/LoanTransactionValidatorImpl.java:177-182` —— 单笔放款必须 `loan.isApproved() && loan.isNotDisbursed()`，否则抛 `error.msg.loan.disbursal.account.is.not.approve.not.disbursed.state`。

**命令分发与注册**：REST → 命令 → Handler 的链路是
- `LoansApiResource.stateTransitions()` 把 `?command=` 参数装配成 `CommandWrapper`（`repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1313-1350`）；
- `commandsSourceWritePlatformService.logCommandSource()`（`fineract-core/.../PortfolioCommandSourceWritePlatformServiceImpl.java:54-77`）→ `SynchronousCommandProcessingService.executeCommand()`（:100-147）→ `findCommandHandler()`（:254，最终 ：316 `commandHandlerProvider.getHandler(entity, action)`）；
- `CommandHandlerProvider.getHandler()`（`fineract-core/.../provider/CommandHandlerProvider.java:91`）按 `@CommandType(entity, action)` 注解匹配 Handler——即 LOAN/CREATE→`LoanApplicationSubmittalCommandHandler`、LOAN/APPROVE→`LoanApplicationApprovalCommandHandler`、LOAN/DISBURSE→`DisburseLoanCommandHandler`（分别见 `fineract-loan/.../handler/` 下三个文件 ：30/:31/:32 的注解）。

**中台侧校验**：终态幂等（`DrawdownService.onPaymentResult` 开头 `order.terminal()` 直接返回，:169-171）、放款幂等（`book()` 先 `fineract.isLoanActive()` 防重复放款，:185-189）、入账重试上限由 `CompensationTask` 控制（`bookingRetryMax`，:85-88）。

# 四、REST 入口逐动作触发链

**动作 0：提交贷款申请（借据 100）**
- 客户前端 → 渠道：`POST /api/drawdowns?channelId=`（`ChannelController.java:27-30`）→ `ChannelService.applyDrawdown`（`ChannelService.java:94-124`）先校验渠道规则（CHANNEL_AMOUNT_LIMIT/CHANNEL_TERM_LIMIT/PRODUCT_NOT_OPEN，:104-112），转 quote→apply 两步调中台。
- 中台：`POST /middle/api/v1/drawdowns`（`DrawdownController.java:66-71`）→ `DrawdownService.apply`（`DrawdownService.java:105-141`）：报价有效期/版本校验 → 多层额度预占 `limitService.preOccupy`（:134）→ `fineract.createLoan`（:137）→ `submitPayment`（:140）。业务单初始 `PROCESSING`/`TO_SUBMIT`（:124-127）。
- Fineract：`POST /loans`（`LoansApiResource.java:549-584`，`createLoanApplication` 命令 ：577-580）→ LOAN/CREATE Handler → `submitApplication()`（`LoanApplicationWritePlatformServiceJpaRepositoryImpl.java:133-176`，发布 LoanCreatedBusinessEvent :168）。借据初始状态硬编码为 100：`Loan.java:560`（构造器 `this.loanStatus = LoanStatus.SUBMITTED_AND_PENDING_APPROVAL`），状态机上 LOAN_CREATED 从 null→100（`DefaultLoanLifecycleStateMachine.java:112-114`）。
- 运营界面直连等价入口：mifos-web 的 `loanActionButtons` 统一 `POST /loans/{id}?command=xxx`（`repos/mifos-web-repo/src/app/loans/loans.service.ts:339-342`）。

**动作 1：审批（100→200）**
- Fineract 入口：`POST /loans/{id}?command=approve` → `stateTransitions()` :1323-1324 装配 `approveLoanApplication` → LOAN/APPROVE Handler（`LoanApplicationApprovalCommandHandler.java:31`）→ `approveApplication()`（`LoanApplicationWritePlatformServiceJpaRepositoryImpl.java:555-580`）→ 实际状态迁移在 `LoanScheduleAssembler.assembleLoanApproval()` :1507 `transition(LoanEvent.LOAN_APPROVED, loan)` → 100→200。
- **本项目实际链路**：中台 `FineractClient` 没有任何 approve 调用；类注释声称"创建时直接提交为经审批口径（submittedOnDate+approvedOnDate）"（`FineractClient.java:26-28`）——但 `createLoan` 实际载荷只有 `submittedOnDate`（`FineractClient.java:57-75`），**与注释不符**。按 Fineract 校验（`LoanTransactionValidatorImpl.java:177-182`），停在 100 的借据直接 `command=disburse` 会被拒绝，需先补 approve 命令或创建时带 `approvedOnDate`。此为当前实现的矛盾点/证据缺口，未发现中台侧补救代码。

**动作 2：放款（→300 ACTIVE）**
- Fineract 入口：`POST /loans/{id}?command=disburse`（`stateTransitions()` :1325-1326）→ LOAN/DISBURSE Handler（`DisburseLoanCommandHandler.java:32`，调 `writePlatformService.disburseLoan`）→ `disburseLoan()`（`LoanWritePlatformServiceJpaRepositoryImpl.java:318-324`）：`validateDisbursement`（:322，状态校验见上）→ `canDisburse` 试算（:3521）→ 生成放款交易、按实际放款信息重算还款计划（:408-412 `regenerateScheduleOnDisbursement`）→ 最终 `loanLifecycleStateMachine.transition(LoanEvent.LOAN_DISBURSED, loan)`（:596）→ 300。
- **本项目触发点**：外部付款结果回调 `POST /middle/api/v1/callbacks/external-result`（`ExternalResultController.java:55-65`）→ `DrawdownService.onPaymentResult`：付款 SUCCESS 时业务单 `PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING`、BookingStatus→`TO_BOOK`，并同步尝试 `book()`（`DrawdownService.java:172-177`）；`book()` 调 `fineract.disburseLoan`（即上述 command=disburse，`FineractClient.java:91-99`）成功后业务单 `→ SUCCESS`、BookingStatus→`BOOKED`、额度预占转实占 `confirmOccupy`（:191-203）；失败则 `RETRY_NEEDED`，由补偿任务重试（`CompensationTask.java:80-88`）。付款 FAILED 则业务单 `→ FAILED` 并逐层释放预占（:178-183）。查单收敛 `queryAndSettle()` 与回调走同一 `onPaymentResult` 通道（:218-227）。
- 渠道查询 `GET /api/drawdowns/{channelRefNo}`（`ChannelController.java:32-35`）→ `ChannelService.drawdownDetail`（:130-143）实时回写 `displayStatus/middleRawStatus`；映射规则 `displayOf()`：`SUCCESS/FAILED` 直传，其余（含 EXTERNAL_SUCCESS_BOOKING_PENDING）统一 `PROCESSING`（:197-203）。

**动作 3：还款与结清（300→600/700，自动判定）**
- 中台：`POST /middle/api/v1/repayments` → `RepaymentService.apply`（订单 `PROCESSING`/扣款 `TO_SUBMIT`，:60-76）→ 扣款成功回调/查单 `onDeductionResult`（:92-110）→ `book()` 调 Fineract 入账（:112-146）。
- Fineract 入口：`POST /loans/{id}/transactions?command=repayment`（`FineractClient.repayLoan`，`FineractClient.java:127-140`）→ LOAN/REPAYMENT Handler → `LoanAccountDomainServiceJpa.makeRepayment`（事件选型 ：263）→ 交易落账后 `determineAndTransition(loan, date)`（:877）→ 状态机按余额自动判定：全部义务履行完→600（REPAID_IN_FULL），多缴→700（LOAN_OVERPAYMENT）（`DefaultLoanLifecycleStateMachine.java:349-363`）。
- 中台随后读取本金/利息/费用分配明细（`getRepaymentAllocation`，`FineractClient.java:142-160`）保存到订单，并只按本金部分逐层恢复额度（`RepaymentService.book()` :127-137 `limitService.restoreByPrincipal`）。

# 五、证据缺口（明确标注）

1. **审批跳变矛盾**：如上所述，`FineractClient.createLoan` 未传 `approvedOnDate`、中台也无 `command=approve` 调用，但 Fineract 放款校验要求 200——按源码逻辑该链路放款会被拒；与 `FineractClient.java:26-28` 注释意图不符。此点只能确认为代码现状，运行时是否有其他配置绕过未在源码中发现。
2. 渠道 `ChannelController` 未提供授信申请之外的对账查询实时刷新接口的主动推送——展示状态更新依赖客户查询时回写（`drawdownDetail`），属设计现状而非缺陷，供参考。