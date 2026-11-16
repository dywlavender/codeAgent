# 贷款账户状态流转全景

本项目里“一笔贷款”的状态分**两层**：融商贷业务层（渠道申请单 → 中台提款/还款业务单）和 Fineract 借据层（`m_loan` 账户）。中台是业务终态的唯一裁判，Fineract 借据状态只反映借据本体生命周期。

```
客户前端            渠道 channel-service         中台 loan-middle                  外部模拟器            Fineract
   │ POST /api/drawdowns │                        │                                │                   │
   ├───────────────────►│ 渠道规则校验+映射        │                                │                   │
   │                    ├──POST /middle/api/v1/drawdowns──►│                       │                   │
   │                    │  displayStatus=PROCESSING│ 额度校验→逐层预占→创建借据──────┼──────────────────►│ 100 提交
   │                    │                         │ 提交付款 POST /sim/api/v1/payments─►│                   │
   │                    │                         │ ◄──回调 /callbacks/external-result──┤ 付款成功           │
   │                    │                         │ EXTERNAL_SUCCESS_BOOKING_PENDING │                   │
   │                    │                         │ 入账 POST /loans/{id}?command=disburse──────────────►│ 300 生效
   │                    │ ◄──GET 查询(展示状态合并)─│ SUCCESS + 逐层实占               │                   │
```

## 一、状态定义在代码哪里

**中台提款业务单（三组并行状态）** — `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java`
- `Status`: `PROCESSING / EXTERNAL_SUCCESS_BOOKING_PENDING / SUCCESS / FAILED / CANCELLED`（`DrawdownOrder.java:19`，终态判定 `terminal()` 在 `:35`）
- `PaymentStatus`（外部付款指令）: `TO_SUBMIT / PROCESSING / SUCCESS / FAILED / RESULT_UNCONFIRMED`（`:23`）
- `BookingStatus`（中台入账）: `NONE / TO_BOOK / BOOKED / RETRY_NEEDED`（`:26`）

**中台还款订单** — `repos/loan-middle-repo/src/main/java/com/rsd/middle/repay/RepaymentOrder.java:13-17`：`Status = PROCESSING/SUCCESS/FAILED`，`DeductionStatus = TO_SUBMIT/PROCESSING/SUCCESS/FAILED/RESULT_UNCONFIRMED`。

**中台授信申请** — `CreditApplicationOrder.java:17`：`PROCESSING / APPROVED / REJECTED`。

**渠道展示状态（无枚举，映射规则）** — `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:198-203`：中台 `SUCCESS/FAILED` 直传，其余（含 `EXTERNAL_SUCCESS_BOOKING_PENDING`）统一展示 `PROCESSING`，原始状态保留在申请单 `middleRawStatus`（`drawdownDetail`，`ChannelService.java:127-137`）。

**Fineract 借据状态** — `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:24`：`100 提交待审批 / 200 审批通过 / 300 生效(ACTIVE) / 400 客户撤销 / 500 拒绝 / 600 义务履行结清 / 601 核销 / 602 重置结清 / 700 超额(OVERPAID)`。事件枚举在同模块 `domain/LoanEvent.java:24`（`LOAN_CREATED/LOAN_APPROVED/LOAN_DISBURSED/REPAID_IN_FULL/...`）。

## 二、流转校验在代码哪里

**Fineract 层（唯一的状态机）** — `repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java:36`
- 转移合法性集中在 `getNextStatus()`（`:111` 起）：`LOAN_APPROVED` 仅允许从 **100→200**（`:119-123`）；`LOAN_DISBURSED` 仅允许从 **200→300**（`:134-139`）；`REPAID_IN_FULL` 仅允许 **300/700→600**（`:161-164`）；`LOAN_REJECTED` 仅 **100→500**。`getNextStatus` 返回 null 时 `internalTransition`（`:79`）不落任何变更——非法跳转即被拒绝。
- 结清/超额不由命令触发，而是每次入账后 `determineAndTransition()`（`:55`）按余额自动判定：全部义务履行完→600，多缴→700（`determineTransitionFromActive`，`:352-366`）。
- 动作前的预检（dry-run）在 `LoanApplicationTransitionValidator.java:155`（拒绝）与 `:216`（撤销），用 `dryTransition` 校验当前状态是否允许该事件。
- 业务服务里的触发点：审批 `LoanScheduleAssembler.java:1507`（`transition(LOAN_APPROVED)`）、放款 `LoanWritePlatformServiceJpaRepositoryImpl.java:596`（`transition(LOAN_DISBURSED)`）、还款后自动判定 `LoanAccountDomainServiceJpa.java:877`。

**中台层（业务单状态守卫，内联在各编排方法）** — `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownService.java`
- 申请校验：报价过期 `:125-128`、按版本快照校验金额/期限 `:129-130`、申请时点多层额度重校验 `:131-134`（额度口径在 `LimitService.availableForDrawdown`，`LimitService.java:74`，冻结为 0、四层取最小）。
- 结果确认幂等：终态直接忽略 `:188-190`；付款成功重复回调 `:197-199`。
- 入账幂等：已 `BOOKED` 跳过 `:219-221`；入账前先查借据是否已 ACTIVE，防止重复放款 `:223-225`。
- 还款侧对应守卫在 `RepaymentService.java:92-95`（终态幂等）、`:114-117`（成功幂等）。

**渠道层（不做业务状态校验）** — 只做渠道开放规则（`ChannelService.java:77-91`）与展示映射，业务状态合法性完全由中台裁决。

## 三、每次 REST 动作如何触发状态变化

**① 提交（申请）**
1. `POST /api/drawdowns?channelId=`（`ChannelController.java:28`）→ 渠道校验开放规则后，内部先 `quote` 再 `applyDrawdown` 调中台（`ChannelService.java:96-104`），落渠道申请单 `displayStatus=PROCESSING`。
2. `POST /middle/api/v1/drawdowns`（`DrawdownController.java:66`）→ `DrawdownService.apply`（`DrawdownService.java:123`）：校验通过后业务单置 **PROCESSING + TO_SUBMIT + NONE**（`:145-147`）→ `limitService.preOccupy` 逐层预占（`:156`，`LimitService.java:100`）→ `fineract.createLoan` 经 `POST /fineract-provider/api/v1/loans` 创建借据（`FineractClient.java:55-80`；借据初始为 **100 提交态**，`Loan.java:560`）→ `submitPayment` 调 `POST /sim/api/v1/payments`（`SimulatorController.java:35`）。同步成功就地进入结果确认，受理中则停在 PROCESSING 等回调/查单（`DrawdownService.java:166-176`）。

**② 外部付款结果（异步，两条收敛通道）**
- 回调：模拟器 `POST /middle/api/v1/callbacks/external-result`（`ExternalResultController.java:55`）→ `case "PAYMENT"`（`:60`）→ `onPaymentResult`（`DrawdownService.java:189`）：成功则付款态=SUCCESS、业务单 **PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING**、入账态置 TO_BOOK（`:198-200`）并同步尝试入账；失败则 **→FAILED** 并 `releasePreOccupied` 逐层释放预占（`:206-208`，`LimitService.java:163`）。
- 查单：回调未送达时由补偿任务对超时 PROCESSING 主动 `GET /sim/api/v1/payments/{extSerialNo}`（`CompensationTask.java:64-71` → `queryAndSettle`，`DrawdownService.java:246`），结果走与回调同一处理函数。
- 提交失败（TO_SUBMIT）由补偿任务重提（`CompensationTask.java:61-62`）。

**③ 放款（入账）**
`book()`（`DrawdownService.java:218`）调用 `POST /loans/{id}?command=disburse`（`FineractClient.java:89-97`）→ Fineract `LoansApiResource`（`@Path("/v1/loans")`，`LoansApiResource.java:188`）经命令总线走到 `LoanWritePlatformServiceJpaRepositoryImpl.java:596` 执行 `transition(LOAN_DISBURSED)`，状态机校验 **200→300**，借据变 **ACTIVE(300)**。成功后业务单 **EXTERNAL_SUCCESS_BOOKING_PENDING → SUCCESS**、入账态=BOOKED、`confirmOccupy` 预占转实占（`DrawdownService.java:228-233`，`LimitService.java:131`）；失败置 RETRY_NEEDED 由补偿任务重试（`:236-238`、`CompensationTask.java:73-86`）。

**④ 审批（借据层命令，本项目链路通常不单独调用）**
Fineract 原生入口是 `POST /loans/{id}?command=approve` → `approveApplication`（`LoanApplicationWritePlatformServiceJpaRepositoryImpl.java:557`）→ `transition(LOAN_APPROVED)`（`LoanScheduleAssembler.java:1507`），即 **100→200**。但融商贷链路中“审批”发生在中台（授信申请走外部模拟审批，`CreditService.java:104-115` 按回调结果置 APPROVED/REJECTED），`FineractClient.java:20-21` 注释明确“审批命令本设计不需要单独调用，创建时直接提交为经审批口径”。

**⑤ 还款与结清**
- `POST /api/repayments`（渠道，`ChannelController.java:38`）→ `POST /middle/api/v1/repayments`（`RepaymentController.java:34`）→ 订单 **PROCESSING/TO_SUBMIT** → `POST /sim/api/v1/deductions` 扣款（`RepaymentService.java:74-83`）→ 回调/查单确认（`onDeductionResult`，`:92-110`；扣款失败订单 **→FAILED** 且无任何额度变化）→ `book()` 调 `POST /loans/{id}/transactions?command=repayment`（`FineractClient.java:131-141`）入账，读回本金/利息/费用分配明细（`:120-125`），订单 **→SUCCESS**，并按本金部分 `restoreByPrincipal` 逐层恢复额度（`:133`，`LimitService.java:191`）。
- 每笔还款交易落库后 Fineract 自动 `determineAndTransition`（`LoanAccountDomainServiceJpa.java:877`）：余额清零且费用结清 → **300→600 结清**；多缴 → **→700 超额**（`DefaultLoanLifecycleStateMachine.java:352-366`）。提前结清金额经 `GET /loans/{id}?command=calculatePrepayment` 试算（`FineractClient.java:104-113`）。

## 四、一个值得注意的证据缺口

`FineractClient.java:20-21` 注释称创建借据时“直接提交为经审批口径（submittedOnDate+approvedOnDate）”，但 `createLoan` 的实际 payload（`FineractClient.java:56-79`）**只含 `submittedOnDate`，未见 `approvedOnDate`**；而状态机要求 `LOAN_DISBURSED` 只能从 **200 APPROVED** 发起（`DefaultLoanLifecycleStateMachine.java:134-135`）。按此推断，若借据停留在 100 态，中台的 disburse 调用会被 Fineract 拒绝并转入 RETRY_NEEDED 重试。源码内未找到打通该环节的显式逻辑（如审批命令调用或 Fineract 侧豁免），此点**未核实闭环**，建议作为后续排查点：要么补 `approvedOnDate`，要么链路中存在本调查未覆盖的审批调用。