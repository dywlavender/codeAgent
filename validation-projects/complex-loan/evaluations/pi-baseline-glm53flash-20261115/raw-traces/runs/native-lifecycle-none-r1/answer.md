基于对需求文档和四个仓库源码的核实，这笔贷款的状态流转全景如下。

## 一、总体结构：三层状态模型

一笔贷款从提交到放款，实际由三层状态协同描述，各自有独立的定义和校验代码：

| 层 | 载体 | 状态定义位置 |
| --- | --- | --- |
| 账户层（Fineract 借据） | Loan 实体 | `repos/fineract-repo/fineract-core/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/LoanStatus.java:24` |
| 业务单层（中台提款单/还款单） | DrawdownOrder / RepaymentOrder | `repos/loan-middle-repo/src/main/java/com/rsd/middle/drawdown/DrawdownOrder.java:18-25` |
| 展示层（渠道申请单） | ChannelApplicationRecord | `repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelApplicationRecord.java:15-24` |

## 二、Fineract 借据状态定义与状态机（账户层）

**状态定义**：`LoanStatus.java:24-38` 定义了 100=SUBMITTED_AND_PENDING_APPROVAL（提交待审批）、200=APPROVED（审批通过）、300=ACTIVE（生效/已放款）、400=撤回、500=拒绝、600=义务履行结清、601=核销结清、602=重组结清、700=超额还款（OVERPAID）。

**流转校验（状态机）**：`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/DefaultLoanLifecycleStateMachine.java`
- 入口方法：`transition()`（:63，先更新汇总再迁移）、`determineAndTransition()`（:71，根据余额自动判定）、`dryTransition()`（:58，试算不落库）。
- 迁移规则在 `getNextStatus()`（:110 起）逐一校验来源状态，例如 LOAN_APPROVED 仅允许来自 100（:121-124）；LOAN_DISBURSED 仅允许来自 200/600（:133-136）；REPAID_IN_FULL 仅允许来自 300/700（:160-163）。非法来源时 `newState=null`，`internalTransition()`（:81）不执行任何变更——这就是"非法跳转被拒绝"的实现。同一文件 :227 起的 `determineTransition*` 系列负责还款后自动判定结清（600）/超额（700）。
- 事件枚举 `LoanEvent.java:24`（LOAN_CREATED、LOAN_APPROVED、LOAN_DISBURSED、REPAID_IN_FULL 等）。

**初始状态**：借据创建即固定为 100，`repos/fineract-repo/fineract-loan/src/main/java/org/apache/fineract/portfolio/loanaccount/domain/Loan.java:560`（`this.loanStatus = LoanStatus.SUBMITTED_AND_PENDING_APPROVAL`）。

## 三、REST 入口 → 动作 → 状态变化的触发链

**1. 创建借据（提交，100）**
- 入口：`POST /fineract-provider/api/v1/loans`（`LoansApiResource`，命令通过 `?command=` 分发）。
- 链路：`LoansApiResource` → `LoanApplicationWritePlatformServiceJpaRepositoryImpl.submitApplication()`（:132 起，调用 `loanAssembler.assembleFrom(command)`）→ Loan 构造置 100（`Loan.java:560`），发布 LoanCreatedBusinessEvent。

**2. 审批（100→200）**
- 入口：`POST /loans/{loanId}?command=approve`。REST 分发在 `repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/api/LoansApiResource.java:1319-1357`（`stateTransitions` 私有方法按 command 参数构造 CommandWrapper），公共端点声明在 :627。
- 链路：命令经 `commandsSourceWritePlatformService.logCommandSource()` 到审批服务，最终在 `LoanScheduleAssembler.java:1507` 触发 `loanLifecycleStateMachine.transition(LoanEvent.LOAN_APPROVED, loan)`——状态机校验当前为 100 才置 200。
- mifos-web 前端对应：`repos/mifos-web-repo/src/app/loans/loans-view/loan-account-actions/approve-loan/approve-loan.component.ts:127` 调 `loanActionButtons(loanId, 'approve', data)`，该方法在 `repos/mifos-web-repo/src/app/loans/loans.service.ts:339-342` 发出 `POST /loans/{loanId}?command=approve`。

**3. 放款（200→300）**
- 入口：`POST /loans/{loanId}?command=disburse`（同一 `stateTransitions` 入口，`LoansApiResource.java:1325`）。
- 前置校验：`repos/fineract-repo/fineract-provider/src/main/java/org/apache/fineract/portfolio/loanaccount/serialization/LoanTransactionValidatorImpl.java:176-181`——单笔借据必须"已审批且未放款"（`loan.isApproved() && loan.isNotDisbursed()`，其中 `isApproved()` 按状态 200 判定，`Loan.java:906-908`），否则报错拒绝。
- 状态变化：`LoanWritePlatformServiceJpaRepositoryImpl.disburseLoan()`（:537 起，重算还款计划、生成放款交易）末尾 :596 `transition(LoanEvent.LOAN_DISBURSED, loan)` → 状态机校验来源为 200 后置 300。前端对应 `disburse.component.ts:157`。

**4. 还款与结清（300→600/700）**
- 入口：`POST /loans/{loanId}/transactions?command=repayment`（`LoanTransactionsApiResource.java:566`）。
- 状态变化：`LoanWritePlatformServiceJpaRepositoryImpl.java:1733-1735`——若超额转 OVERPAID，否则 LOAN_REPAYMENT_OR_WAIVER；:3311-3313 当还清全部欠款时 `transition(LoanEvent.REPAID_IN_FULL, loan)` 置 600。注意：结清**不是独立命令**，而是还款入账后按余额自动判定。

## 四、中台业务单状态流转（业务层）——"提交到放款"的实际编排

**提款单**状态定义在 `DrawdownOrder.java:18-25`：业务状态 `PROCESSING → EXTERNAL_SUCCESS_BOOKING_PENDING → SUCCESS | FAILED`（提交前可 CANCELLED），外加付款指令状态（TO_SUBMIT/PROCESSING/SUCCESS/FAILED/RESULT_UNCONFIRMED）和入账状态（NONE/TO_BOOK/BOOKED/RETRY_NEEDED）三轨并行。编排逻辑在 `DrawdownService.java`：

1. **申请受理**：渠道 `POST /middle/api/v1/drawdowns`（`DrawdownController.java:66`）→ `DrawdownService.apply()`（:123）——校验报价有效性与额度、逐层预占 → `fineract.createLoan()`（`FineractClient.java:73`，触发 Fineract 状态 100）→ 提交付款。单据此时为 PROCESSING。
2. **外部结果确认**：回调 `POST /middle/api/v1/callbacks/external-result`（`ExternalResultController.java:55-64`）与补偿查单（`CompensationTask.java:68`）走同一入口 `onPaymentResult()`（`DrawdownService.java:189`）——付款 SUCCESS 则置 EXTERNAL_SUCCESS_BOOKING_PENDING + TO_BOOK 并就地尝试入账；FAILED 则释放预占、置 FAILED。
3. **入账（真正的"放款"）**：`book()`（`DrawdownService.java:218`）——先 `isLoanActive()` 查借据状态防重，未生效才调 `fineract.disburseLoan()`（`FineractClient.java:90`，即 Fineract 的 `command=disburse`，驱动借据 200→300），随后逐层预占转实占、单据置 SUCCESS。失败转 RETRY_NEEDED，由补偿任务重试（`CompensationTask.java:74-80`）。这就是需求"外部付款成功≠放款完成"的实现：Fineract 借据 ACTIVE + 实占完成才算 SUCCESS。
4. **还款单**：`RepaymentService.apply()`（:56，PROCESSING）→ 外部扣款 → `book()`（:114，`fineract.repayLoan()` 触发 Fineract 还款交易与结清判定，读取本金/利息/费用分配明细并按本金恢复额度）→ SUCCESS；扣款失败则 FAILED、无额度变化。

## 五、渠道展示状态（展示层）

渠道库不定义新状态机，只做映射：`ChannelService.displayOf()`（`repos/channel-service-repo/src/main/java/com/rsd/channel/ChannelService.java:195-203`）把中台 PROCESSING/EXTERNAL_SUCCESS_BOOKING_PENDING 统一对外展示为 `PROCESSING`，SUCCESS/FAILED 直传；原始状态保存在 `ChannelApplicationRecord.middleRawStatus`（`ChannelApplicationRecord.java:22-23`）。每次查询 `GET /api/drawdowns/{channelRefNo}`（`ChannelController.java:33`）都会刷新该映射。

## 六、完整时序（一笔贷款从提交到放款）

```
客户提交  POST /api/drawdowns (channel) → 中台 apply → 预占额度 + Fineract POST /loans (借据=100) → 提款单 PROCESSING
外部付款  成功回调/查单 → 提款单 EXTERNAL_SUCCESS_BOOKING_PENDING + TO_BOOK
中台入账  book() → Fineract command=disburse (借据 200→300) + 预占转实占 → 提款单 SUCCESS
后续还款  中台 apply → 外部扣款 → Fineract command=repayment → 借据 300→600(结清)/700(超额) → 额度按本金恢复
```

## 七、一个需要指出的实现疑点（证据缺口）

`FineractClient.java:19-21` 注释声称"创建时直接提交为经审批口径（submittedOnDate+approvedOnDate），无需单独审批命令"，但：

1. `createLoan()` 的实际 payload（`FineractClient.java:73-84`）**并不包含 `approvedOnDate`**，只有 `submittedOnDate`；
2. Fineract 创建链路（`Loan.java:560`、`LoanAssemblerImpl`）不读取该字段，借据固定落在 100；
3. 中台全库检索不到任何 `command=approve` 调用，`book()` 只调 `command=disburse`。

而 Fineract 放款前置校验（`LoanTransactionValidatorImpl.java:176-181`）要求借据处于 200 才允许 disburse。按当前源码逻辑推断，中台触发的放款会被 Fineract 以"not in approved state"拒绝，入账将永远停留在 RETRY_NEEDED。授权范围内没有找到 Fineract 侧的定制补丁或产品级跳过审批的配置证据，**中台与 Fineract 之间是否缺少审批命令调用，属于未核实的疑点**，建议进一步确认（例如 Fineract 产品配置是否允许免审批放款，或中台是否应补调 `command=approve`）。