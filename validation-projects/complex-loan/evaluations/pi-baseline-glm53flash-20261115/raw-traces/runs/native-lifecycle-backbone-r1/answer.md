已核实完各层实现。以下按“三层状态体系 → 状态定义 → REST 动作触发的流转 → 流转校验 → 证据缺口”组织回答。

# 一、总体：一层申请，三层账户状态

一笔贷款（提款）的业务状态分散在三层，各层独立流转、独立校验：

| 层 | 状态主体 | 职责 |
| --- | --- | --- |
| 渠道层 | 渠道申请单展示状态 | 只做展示映射，合并中台多个内部状态为 `PROCESSING`，原始状态留底 |
| 中台层 | 提款业务单（含付款指令、入账两个子状态） | 业务编排与**业务终态的唯一裁判**，附额度预占/实占/释放 |
| Fineract 层 | 借据（m_loan） | 贷款账户生命周期（100→200→300→600），非法跳转被状态机拒绝 |

# 二、状态定义在哪里

**中台业务单** —— `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java`
- `:18` 业务单状态 `Status { PROCESSING, EXTERNAL_SUCCESS_BOOKING_PENDING, SUCCESS, FAILED, CANCELLED }`
- `:21` 付款指令状态 `PaymentStatus { TO_SUBMIT, PROCESSING, SUCCESS, FAILED, RESULT_UNCONFIRMED }`
- `:24` 入账状态 `BookingStatus { NONE, TO_BOOK, BOOKED, RETRY_NEEDED }`
- `:47` `terminal()` 终态判定（SUCCESS/FAILED/CANCELLED），是所有幂等分支的依据

**Fineract 借据** —— `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:27-38`：`100 提交待审批 → 200 审批通过 → 300 生效(ACTIVE) → 600 义务结清 / 601 核销 / 602 重 scheduled / 700 超额`。

**渠道展示状态** —— 没有独立枚举：映射逻辑在 `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:186-191` `displayOf()`（SUCCESS/FAILED 直传，其余一律 `PROCESSING`），结果落在 `ChannelApplicationRecord.displayStatus` 与 `middleRawStatus`（`ChannelApplicationRecord.java:17-18`）。

**外部模拟系统** —— `repos/external-simulator-repo/src/main/java/com/rsd/simulator/ExtTransaction.java:23-26`：外部流水 `PROCESSING → SUCCESS|FAILED`，场景枚举 `SYNC_SUCCESS/SYNC_REJECT/ASYNC_SUCCESS_DELAY/ASYNC_FAIL/NO_CALLBACK_NEED_QUERY`。

# 三、状态流转全貌与 REST 动作触发点

```
授信 CR-   PROCESSING ──外部审批回调/查单──▶ APPROVED/REJECTED ──▶ effectuate 建授信/改额度
                                        （冻结/解冻是运营操作，不走审批）

提款 DD-   PROCESSING ──付款成功──▶ EXTERNAL_SUCCESS_BOOKING_PENDING ──Fineract放款成功──▶ SUCCESS
                ├─付款失败──▶ FAILED（释放预占）
                └─入账失败──▶ TO_BOOK/RETRY_NEEDED（补偿任务重试）

Fineract借据  100 ──▶ 200 ──command=disburse──▶ 300(ACTIVE) ──还款/结清判定──▶ 600 或 700
```

## 1. 客户提交：`POST /api/drawdowns?channelId=`（ChannelController.java:28-30）

入口在 `ChannelService.applyDrawdown`（`ChannelService.java:83-99`）：先校验渠道规则（`PRODUCT_NOT_OPEN`/`CHANNEL_AMOUNT_LIMIT`/`CHANNEL_TERM_LIMIT`，`:85-95`），然后**代替客户串联两次中台调用**——先 `POST /middle/api/v1/drawdowns/quote`（生成 `QT-` 报价快照，30 分钟有效，状态 `UNUSED`，`DrawdownService.java:48-72`），再 `POST /middle/api/v1/drawdowns`。

中台 `DrawdownService.apply`（`DrawdownService.java:123-162`）一次性完成状态初始化与预占：
- 建单即 `PROCESSING` + `PaymentStatus.TO_SUBMIT` + `BookingStatus.NONE`（`:145-147`）
- 报价转 `USED`（`:152-153`）
- 逐层预占额度 `limitService.preOccupy`（`:156-157`）
- 调 Fineract `POST /loans` 创建借据（`FineractClient.java:55-73`），借据在 Fineract 侧落成 **100 提交状态**（`Loan.java:560` 构造器硬编码 `SUBMITTED_AND_PENDING_APPROVAL`），`loanAccountNo` 记回业务单（`:158-160`）
- 立即提交付款 `submitPayment`（`:161`，方法体 `:164-181`）：同步 `SUCCESS/REJECTED` 就地结算；受理中保持 `PROCESSING`；提交异常保留 `TO_SUBMIT` 等补偿

渠道侧生成 `CHN-` 单号保存映射，**首次返回固定 `displayStatus=PROCESSING`**（`ChannelService.java:97-99`）。

## 2. 外部结果回调：`POST /middle/api/v1/callbacks/external-result`（ExternalResultController.java:43-70）

- `type=PAYMENT` → `DrawdownService.onPaymentResult`（`DrawdownService.java:186-213`）：
  - 成功：`paymentStatus=SUCCESS` → 业务单进入显式可恢复状态 `EXTERNAL_SUCCESS_BOOKING_PENDING`，`bookingStatus=TO_BOOK`（`:198-200`），随后同步尝试 `book()`；
  - 失败：业务单 `FAILED` 并释放预占 `limitService.releasePreOccupied`（`:206-208`）。
- `type=APPROVAL` → `CreditService.onApprovalResult`（`CreditService.java:104-116`）：授信申请单 `PROCESSING → APPROVED/REJECTED`，`APPROVED` 触发 `effectuate`（`:118-145`）建授信/提额/降额并落 `restricted` 标志。

**关键语义**：回调成功≠业务成功——`EXTERNAL_SUCCESS_BOOKING_PENDING` 就是为“外部已付款、中台未入账”设的显式中间态。

## 3. 中台入账（放款）：`book()` 内调 Fineract `POST /loans/{id}?command=disburse`

`DrawdownService.book`（`DrawdownService.java:216-243`）：
- 幂等前置：`bookingStatus==BOOKED` 直接返回（`:218-220`）；调 Fineract 前先 `isLoanActive` 查借据是否已 300，防重复放款（`:222-227`）
- 执行 `disburseLoan`（`FineractClient.java:89-95`）→ Fineract `LoansApiResource.stateTransitions`（`LoansApiResource.java:608-631`，command 路由 `:1325-1326`）→ `LoanWritePlatformServiceJpaRepositoryImpl.disburseLoan`（`:314` 起）：`canDisburse` 状态机 dryTransition 校验（`:370`，实现 `:3521-3532`）→ 生成放款交易（`:393-399`）→ 重算还款计划（`:556-559`）→ `loanLifecycleStateMachine.transition(LOAN_DISBURSED, loan)`（`:596`）→ 借据 **200→300(ACTIVE)**
- 成功后中台收敛：`bookingStatus=BOOKED`、业务单 `SUCCESS`、逐层预占转实占 `confirmOccupy`（`DrawdownService.java:228-233`）；失败转 `RETRY_NEEDED`（`:236-239`）

状态机的转移校验在 `DefaultLoanLifecycleStateMachine.getNextStatus`（`DefaultLoanLifecycleStateMachine.java:110-216`）：`LOAN_DISBURSED` 只允许从 `200（APPROVED）`或 `600/700` 进入（`:134-139`），非法来源返回 null 即拒绝。

## 4. 补偿查单（定时任务，不是 REST）：CompensationTask.compensate（CompensationTask.java:53-89）

三条线把中间态推向终态：`TO_SUBMIT` 重提付款（`:57-58`）；`PROCESSING` 超时主动查单 `queryAndSettle`（`:60-67`，实现 `DrawdownService.java:246-253`）；`TO_BOOK/RETRY_NEEDED` 重试 `book`（`:69-77`）。查单与回调最终走同一个 `onPaymentResult` 通道，这是“重复回调/重复查单不产生新记录”的实现基础。

## 5. 结果查询：`GET /middle/api/v1/drawdowns/{bizOrderNo}`（DrawdownController.java:71-74）→ 渠道 `GET /api/drawdowns/{channelRefNo}`（ChannelController.java:33-36）

`ChannelService.drawdownDetail`（`ChannelService.java:111-121`）拉取中台原始状态，`displayOf` 映射为展示状态并**同时保存原始状态**（`:116-119`）——查询不触发状态变化，只做视图收敛。

## 6. 还款链（对照）：`POST /middle/api/v1/repayments`（RepaymentController.java:34-38）

`RepaymentService.apply`（`RepaymentService.java:50-70`）建单 `PROCESSING/TO_SUBMIT` → 扣款成功 `onDeductionResult`（`:85-104`）→ `book`（`:107-147`）调 Fineract `POST /loans/{id}/transactions?command=repayment`（`FineractClient.java:129-142`）入账，读取 `principalPortion/interestPortion/feePortion` 分配明细（`FineractClient.java:145-165`），**只按本金部分恢复额度**（`RepaymentService.java:135-141`）。Fineract 端入账后由 `determineAndTransition` 自动判定 300→600（全部结清）或→700（多缴）（`DefaultLoanLifecycleStateMachine.java:76-102` 与 `:283-307`）。

## 7. 运营通道（mifos-web）：借据层的 approve/disburse 按钮

`repos/mifos-web-repo/src/app/loans/loans-view/loan-accounts-button-config.ts:72-75`（Approve）与 `:91-94`（Disburse）按借据当前状态出按钮，最终经 `loans.service.ts` 的 `loanActionButtons`（`:339-342`）发 `POST /loans/{id}?command=approve|disburse`。Fineract 的 approve 命令在 `LoanScheduleAssembler.assembleLoanApproval`（`LoanScheduleAssembler.java:1502-1521`）内执行 `transition(LOAN_APPROVED, loan)`（`:1506`），状态机要求来源必须是 100（`DefaultLoanLifecycleStateMachine.java:124-127`）。

# 四、流转校验的实现位置汇总

| 校验类型 | 位置 |
| --- | --- |
| 渠道规则（金额上限/期限/开放开关） | `ChannelService.applyDrawdown`，`ChannelService.java:85-95` |
| 报价有效期 | `DrawdownService.java:124-127`（`QUOTE_EXPIRED`） |
| 产品金额/期限/还款方式 | `validateAmountAndTerm`，`DrawdownService.java:272-282` |
| 多层额度余量（申请时点重校） | `DrawdownService.java:130-134`（`LIMIT_INSUFFICIENT`） |
| 业务幂等（终态不重入） | `DrawdownOrder.terminal()`（`:47`）+ `onPaymentResult`/`book` 各分支 |
| Fineract 借据状态机 | `DefaultLoanLifecycleStateMachine.getNextStatus`（`:110-216`），disburse 前校验 `canDisburse`（`LoanWritePlatformServiceJpaRepositoryImpl.java:3521-3532`）、参数级校验 `validateDisbursement`（`LoanTransactionValidatorImpl.java:123`，其中“须已审批未放款”在 `:178-186`） |
| 展示状态合并（不丢原始状态） | `ChannelService.displayOf`（`:186-191`）+ `drawdownDetail` 落库（`:111-121`） |

# 五、证据缺口（如实标注，未核实）

1. **借据 100→200 的衔接缺口**：中台 `FineractClient.createLoan` 的 payload（`FineractClient.java:55-73`）只带 `submittedOnDate`，**没有 `approvedOnDate`**；而该类注释（`:21`）声称“创建时直接提交为经审批口径（submittedOnDate+approvedOnDate）”。按 Fineract 源码，创建后借据固定为 100（`Loan.java:560`），`command=disburse` 的校验要求 `loan.isApproved() && loan.isNotDisbursed()`（`LoanTransactionValidatorImpl.java:178-186`），状态机也不允许从 100 直接放款。我在 `loan-middle-repo` 全库未检索到任何调用 Fineract `approve` 命令的代码（`FineractClient` 中无该方法）。即：**中台如何让借据到达 200，在当前源码中没有实现证据**——这是注释与实现之间的不一致，建议作为待确认项。
2. **`CANCELLED` 无入口**：状态枚举与需求契约（“提交前可 CANCELLED”）都定义了该状态，但 loan-middle 源码中没有任何把业务单置为 `CANCELLED` 的端点或写点，当前代码不可达。
3. 需求与代码注释多次引用 `docs/接口契约-阶段2.md`、`docs/三系统边界设计.md`，本轮授权资料根中不含 `docs/` 目录，未能核对这些文档原文。