# 还款处理流程（Fineract 原生：计划生成与还款交易）

核对日期：2026-09-06。适用系统：Fineract 贷款中台服务（fineract-backend），Mifos Web 前端发起请求。范围：Fineract 1.14.0 原生的还款计划维持与还款交易处理；融商贷编排视角见[还款结清流程](repayment-settlement-flow.md)。

## 主要阶段

1. 计划维持：还款计划按产品参数生成（提交时首次生成、放款时重算）；回溯性交易会触发计划重算并重放既有交易保持一致。
2. 发起还款：前端从还款模板取得交易类型，按交易命令提交还款。
3. 命令路由：动作经命令框架路由到对应处理器，而不是在 REST 类里直接写业务。
4. 校验与分配：先校验新交易的合法性，再按产品上的分配策略选择处理器；处理器按"提前于期次、按时、逾期"三种场景把金额拆分到费用、罚息、利息、本金，顺序随策略不同而不同。
5. 状态收尾：入账后状态机自动判定结清相关状态（义务履行完毕结清、全款还清、超额缴款），产品容差影响判定；不是人工设置状态。
6. 其他交易类型走同一入口不同命令：利息减免、核销、提前终止、回收款、超额退款等。

关键关系：还款不是直接改余额字段，而是经策略处理器按期次拆分；"结清"是入账后系统自动判定的结果。回答"还款后状态/额度如何变化"必须追到处理器实现与状态机判定，不能只看交易落库。

## 业务术语

- 还款：repayment；提前于期次还款：payment in advance；逾期还款：late repayment。
- 结清（义务履行完毕）：closed obligations met；超额缴款：overpaid；核销：write-off；提前终止：foreclosure。
- 分配策略：transaction processing strategy（产品字段 transactionProcessingStrategyCode）。
- 还款计划期次：repayment installment。

## 调查入口

- fineract-backend | CONTROLLER | LoanTransactionsApiResource
- fineract-backend | ENTRY_CLASS | LoanRepaymentCommandHandler
- fineract-backend | ENTRY_CLASS | LoanTransactionProcessingServiceImpl
- fineract-backend | ENTRY_CLASS | DefaultLoanLifecycleStateMachine
- mifos-web | PAGE | make-repayment.component.ts

## 覆盖边界

覆盖还款交易的入口、路由、分配与状态判定。未覆盖：罚息与利率重算的完整规则、会计分录生成、坏账核销后的恢复；分配顺序以源码中具体策略实现为准，主干不复制逐条规则。
