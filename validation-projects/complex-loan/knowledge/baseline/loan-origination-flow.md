# 贷款申请流程（Fineract 原生：创建 → 审批 → 放款）

核对日期：2026-09-06。适用系统：Fineract 贷款中台服务（fineract-backend），Mifos Web 前端发起请求。范围：Fineract 1.14.0 借据层的原生生命周期；融商贷编排视角见[提款支付流程](drawdown-payment-flow.md)。

## 主要阶段

1. 创建贷款申请：前端提交，后端校验产品条件；提交成功后账户进入"已提交待审批"，还款计划在此时首次生成。另有只试算不建账户的用法。
2. 审批：状态机校验通过才允许审批；只有从"已提交待审批"才能转入"已审批"。审批调整放款明细或日期时会重算还款计划，未调整则不重算。
3. 放款：从"已审批"转入"生效"。放款时会按实际放款信息重算还款计划，并重放已有交易保持一致。
4. 反向与替代分支：拒绝、客户撤回、撤销审批、撤销放款。

关键关系：还款计划的权威时点是"提交时首次生成＋放款时重算"，审批只在明细变更时重算——不要把"放款才生成计划"或"计划永不变化"当作本项目约定。动作类请求统一走命令机制，由命令注解路由到对应处理器，而不是在 REST 层写业务。

## 业务术语

- 提交/撤回：submit / withdrawn by applicant。
- 审批/撤销审批：approve / undo approval。
- 放款：disburse（本项目说"放款"，不说"提款"）；放入储蓄账户放款：disburse to savings；撤销放款：undo disbursal。
- 还款计划：repayment schedule；试算：calculateLoanSchedule。

## 调查入口

- fineract-backend | CONTROLLER | LoansApiResource
- fineract-backend | ENTRY_CLASS | DefaultLoanLifecycleStateMachine
- fineract-backend | ENTRY_CLASS | LoanScheduleAssembler
- fineract-backend | ENTRY_CLASS | LoanWritePlatformServiceJpaRepositoryImpl
- mifos-web | ENTRY_CLASS | loans.service.ts

## 覆盖边界

覆盖到放款完成、账户生效。未覆盖：放款触发的会计分录、储蓄账户联动、费用与担保人处理、多笔放款细节（tranche 的编排用法见融商贷提款流程）；涉及时回到源码确认。
