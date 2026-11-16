调查完成。下面按“状态定义 → 流转校验 → REST 动作触发”三条线作答，全部结论均给出源码依据。

## 一、全景：一笔贷款的账户状态在三个层面并存流转

本项目的"账户状态"不是单一状态机，而是三层并存（渠道展示状态只是映射，不产生新状态）：

```
授信层(中台 CreditApplicationOrder)      提款业务单(中台 DrawdownOrder 三套子状态)         Fineract 借据账户(LoanStatus)
PROCESSING → APPROVED/REJECTED          PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING    100 提交 → 200 审批 → 300 生效
(外部模拟审批回调驱动)                    → SUCCESS | FAILED                              → 600 结清 | 700 超额
                                        ├ paymentStatus: TO_SUBMIT/PROCESSING/SUCCESS/FAILED/RESULT_UNCONFIRMED
                                        └ bookingStatus: NONE/TO_BOOK/BOOKED/RETRY_NEEDED
```

渠道侧原始状态保留、对外合并为 `PROCESSING`。

## 二、状态定义在哪里

| 层面 | 状态定义 | 位置 |
|---|---|---|
| Fineract 借据账户 | `SUBMITTED_AND_PENDING_APPROVAL(100)/APPROVED(200)/ACTIVE(300)/CLOSED_OBLIGATIONS_MET(600)/CLOSED_WRITTEN_OFF(601)/CLOSED_RESCHEDULE_OUTSTANDING_AMOUNT(602)/WITHDRAWN(400)/REJECTED(500)/OVERPAID(700)` | `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:27-37` |
| 中台提款业务单 | `Status{PROCESSING, EXTERNAL_SUCCESS_BOOKING_PENDING, SUCCESS, FAILED, CANCELLED}` + `PaymentStatus{TO_SUBMIT, PROCESSING, SUCCESS, FAILED, RESULT_UNCONFIRMED}` + `BookingStatus{NONE, TO_BOOK, BOOKED, RETRY_NEEDED}`，终态判定 `terminal()` | `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java:18,21,24,47` |
| 中台还款订单 | `Status{PROCESSING,SUCCESS,FAILED}`、`DeductionStatus{TO_SUBMIT,PROCESSING,SUCCESS,FAILED,RESULT_UNCONFIRMED}`、`Type{DUE,EARLY_PRINCIPAL,EARLY_SETTLE}` | `repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentOrder.java:14-18` |
| 中台授信申请单 | `Status{PROCESSING, APPROVED, REJECTED}` | `repos/loan-middle-repo/src/main/java/com/rsd/middle/credit/CreditApplicationOrder.java:17` |
| 渠道展示状态 | 中台 `SUCCESS/FAILED` 直传，其余（含 `EXTERNAL_SUCCESS_BOOKING_PENDING`）统一显示 `PROCESSING`，原始状态存 `middleRawStatus` | `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:198-202` |

## 三、流转校验在哪里

**1. Fineract 借据层：`DefaultLoanLifecycleStateMachine`（事件×当前状态的白名单校验）**

`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java`：

- `getNextStatus()`（:110-232）是核心校验表，每种 `LoanEvent`（枚举定义 `LoanEvent.java:26` 起）只允许从指定状态出发，不合法时返回 `null`、状态不变（非法跳转被静默拒绝）：
  - `LOAN_CREATED` 且当前无状态 → 100（:112-114）
  - `LOAN_APPROVED`：仅 100→200（:124-129）
  - `LOAN_DISBURSED`：仅 200（或 600）→300（:134-142）
  - `REPAID_IN_FULL`：仅 300/700→600（:161-164）
- `determineAndTransition()`（:55-72）用于还款类动作：按余额事实自动判定，`determineTransitionFromActive`（:350-361）中“全部义务履行完毕→600 结清、多缴→700 超额”。
- 借据创建时的初始状态 100 由 `Loan` 构造直接写死：`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/Loan.java:560`。

**2. 中台业务单层：状态迁移内嵌在编排服务的守卫中**

- 提款：`DrawdownService.onPaymentResult`（`repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownService.java:189-215`）——终态幂等守卫 `if (order.terminal()) return`（:190-191）；成功才允许 `PROCESSING→EXTERNAL_SUCCESS_BOOKING_PENDING` 并把 `bookingStatus` 置 `TO_BOOK`（:197-201）；失败才 `→FAILED` 并释放预占（:206-209）。`book()`（:218-243）——`BOOKED` 直接返回（:220-222）、先查借据是否已 ACTIVE 防重复放款（:223-225），失败转 `RETRY_NEEDED` 累加重试计数（:235-241）。
- 还款：`RepaymentService.onDeductionResult/book`（`repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentService.java:96-144`）同样的终态守卫与“扣款成功才入账”次序约束。

## 四、REST 入口 → 每次动作如何触发状态变化

**动作 0：授信申请与外部审批（贷款的前置）**
- 渠道入口 `POST /api/credit-applications`（`ChannelController.java:49-51`）→ 渠道准入校验后透传（`ChannelService.java:134-158`）→ 中台 `CreditService.apply/submitApproval`（`CreditService.java:54-92`）建 `PROCESSING` 单并提交模拟审批。
- 外部回调 `POST /middle/api/v1/callbacks/external-result`（`type=APPROVAL`，`ExternalResultController.java:59-91`，分发在 :74-78）→ `CreditService.onApprovalResult`（:104-115）：`PROCESSING → APPROVED/REJECTED`，批准额度生效**以中台账本为准**。

**动作 1：提交（报价＋申请）**
- 渠道入口 `POST /api/drawdowns`（`ChannelController.java:28-31`）→ `ChannelService.applyDrawdown`（`ChannelService.java:86-116`）：先做渠道规则校验（`PRODUCT_NOT_OPEN/CHANNEL_AMOUNT_LIMIT/CHANNEL_TERM_LIMIT`，:93-99），再字段转换成"quote→apply"两步调中台。
- 中台入口 `POST /middle/api/v1/drawdowns`（`DrawdownController.java:66-71`）→ `DrawdownService.apply`（:123-162）：
  - 流转校验：报价 30 分钟有效 `QUOTE_EXPIRED`（:124-127）、按版本快照校验金额/期限/还款方式（:128）、按申请时点重验四层额度 `LIMIT_INSUFFICIENT`（:129-133）；
  - 状态变化：建单即 `Status=PROCESSING / PaymentStatus=TO_SUBMIT / BookingStatus=NONE`（:145-147）→ 逐层预占额度（:156-157）→ `fineract.createLoan` 调 `POST /loans` 创建借据（Fineract 借据落入 100 状态，`FineractClient.java:55-78`）→ `submitPayment`（:166-186）提交外部付款，同步成功/拒绝就地结算，否则停在 `PROCESSING`。
- **"审批”在本设计中的位置**：中台不单独调用 Fineract 的 `command=approve`，`FineractClient.java:18-21` 注释说明“创建借据即审批放款一体（受托支付成功才登记）"。

**动作 2：外部付款结果确认（回调/查单共用通道）**
- 模拟服务按场景产生同步/异步结果（`repos/external-simulator-repo/src/main/java/com/rsd/simulator/SimulatorService.java:63-71`）。
- 回调入口 `POST /middle/api/v1/callbacks/external-result`（`ExternalResultController.java:59-66` 分发 `PAYMENT`）→ `DrawdownService.onPaymentResult`：付款成功即触发 `PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING`（这就是“外部已付款成功、中台入账未完成”的显式状态）并同步尝试入账；付款失败 → `FAILED` + 逐层释放预占。

**动作 3：放款（中台入账 = Fineract 借据 200/100→300 + 预占转实占）**
- 入账由 `DrawdownService.book`（:218-243）触发：`fineract.disburseLoan` 调 `POST /loans/{id}?command=disburse`（`FineractClient.java:89-95`）；成功后 `BookingStatus=BOOKED`、业务单 `Status=SUCCESS`、`limitService.confirmOccupy` 逐层实占（:227-234）。
- Fineract 侧链路：REST 入口 `POST /v1/loans/{loanId}?command=...` 的 `stateTransitions`（`repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:608-630`，command 分发 :1313-1345）→ `CommandWrapperBuilder.disburseLoanApplication`（`CommandWrapperBuilder.java:1164-1171`）→ 命令总线 `PortfolioCommandSourceWritePlatformServiceImpl.logCommandSource`（`PortfolioCommandSourceWritePlatformServiceImpl.java:54-78`）→ 按 `@CommandType(entity="LOAN", action=...)` 注册表路由（`CommandHandlerProvider.java:41-62,91-100`）→ `DisburseLoanCommandHandler`（`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/DisburseLoanCommandHandler.java:35`）→ `LoanWritePlatformServiceJpaRepositoryImpl.disburseLoan`（`LoanWritePlatformServiceJpaRepositoryImpl.java:314-323`）→ 末尾 `loanLifecycleStateMachine.transition(LoanEvent.LOAN_DISBURSED, loan)`（:596），由状态机按 `DefaultLoanLifecycleStateMachine.java:134-142` 校验后置 300。

**动作 4：异步收敛（补偿任务，不引入消息中间件）**
- `repos/loan-middle-repo/src/main/java/com/rsd/middle/compensation/CompensationTask.java:45-90`：`TO_SUBMIT` 重提交（:49-50）、付款受理超时主动查单走与回调相同通道（:52-59）、`TO_BOOK/RETRY_NEEDED` 重试入账（:61-68）。所以 `EXTERNAL_SUCCESS_BOOKING_PENDING` 的单子不重付款、不判失败，由补偿继续入账。

**动作 5：查询与展示**
- `GET /api/drawdowns/{channelRefNo}`（`ChannelController.java:33-36`）→ `ChannelService.drawdownDetail`（:160-171）：取中台原始状态做 `displayOf` 映射（:198-202），多内部状态对外合并为 `PROCESSING`、原始状态留档。

**动作 6：还款与结清（借据 300→600/700）**
- 渠道 `POST /api/repayments`（`ChannelController.java:38-41`）→ 中台 `RepaymentService.apply`（:62-76，`PROCESSING+TO_SUBMIT`）→ 扣款成功后 `book` 调 `POST /loans/{id}/transactions?command=repayment`（`FineractClient.java:129-140`）→ Fineract 命令 `LOAN|REPAYMENT` 的 `LoanRepaymentCommandHandler`（`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/handler/LoanRepaymentCommandHandler.java:36`）→ 还款处理末端 `determineAndTransition`（`LoanDownPaymentHandlerServiceImpl.java:169`）→ 状态机按余额事实自动判定 `ACTIVE→600 结清 / 700 超额`（`DefaultLoanLifecycleStateMachine.java:322-324,350-361`）→ 中台读取分配明细、仅按本金部分逐层恢复额度（`RepaymentService.java:116-144`）。

## 五、必须指出的缺口（未闭环疑点）

中台注释声称“创建时直接提交为经审批口径（submittedOnDate+approvedOnDate）"（`FineractClient.java:18-21`），但 `createLoan` 的实际 payload（:57-72）**只有 `submittedOnDate`/`expectedDisbursementDate`，没有 `approvedOnDate`，也没有任何 `command=approve` 调用**。而状态机规定 `LOAN_DISBURSED` 仅允许从 `APPROVED(200)` 出发（`DefaultLoanLifecycleStateMachine.java:134-136`）——按此代码路径，从 100 直接 disburse 状态不会迁移到 300，中台 `isLoanActive` 将一直为 false（`FineractClient.java:82-86`），入账会反复转 `RETRY_NEEDED`。是否存在 Fineract 侧其他补偿路径使 100→300 可达，本轮未核实到证据；这是中台编排与 Fineract 状态机之间**明确标注的实现缺口**，而非已闭环的流转。