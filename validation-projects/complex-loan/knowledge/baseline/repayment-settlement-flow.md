# 还款结清流程（订单 → 扣款 → 入账 → 额度恢复）

核对日期：2026-09-06。适用系统：渠道后端、贷款中台、外部模拟服务、Fineract。范围：当期还款、提前部分还本、提前结清的入口与链路；逾期识别与代偿是后续计划。

## 主要阶段

1. 发起：客户经渠道提交还款（当期、提前部分还本、提前结清三类）；提前类可先经中台向 Fineract 试算应还构成。
2. 建单与扣款：中台创建还款订单，向模拟服务提交扣款（业务单号幂等）；扣款结果经回调或补偿查单确认。
3. 入账：扣款成功后中台调 Fineract 完成还款入账；金额如何拆分到费用、罚息、利息、本金由 Fineract 的产品分配策略决定，中台只读取并保存分配结果，不在多个系统重复判断分配。
4. 额度恢复：只有分配中的本金部分逐层恢复授信占用；担保覆盖层按覆盖比例折算；利息、费用不恢复任何层。
5. 结果展示：渠道聚合中台状态；扣款失败则还款失败，不产生任何额度变化。

关键关系："扣款成功"与"入账完成"是两个状态，中间的可恢复间隙由补偿任务收敛；结清状态是 Fineract 自动判定的结果，不是人工设置的字段。回答"还了钱额度为什么没恢复"类问题时，先分清是扣款未成功、入账未完成，还是恢复金额与预期口径不同（利息费用不恢复）。

## 业务术语

- 提前部分还本：EARLY_PRINCIPAL；提前结清：EARLY_SETTLE；当期：DUE。
- 分配明细：一笔还款在费用/罚息/利息/本金上的拆分结果，来自 Fineract 交易。
- 本金恢复：按分配中的本金部分减少各层占用，台账原因记为 PRINCIPAL_REPAID。

## 调查入口

- loan-middle | ENTRY_CLASS | RepaymentService
- loan-middle | ENTRY_CLASS | LimitService
- loan-middle | ENTRY_CLASS | CompensationTask
- external-simulator | ENTRY_CLASS | SimulatorService
- fineract-backend | CONTROLLER | LoanTransactionsApiResource

## 覆盖边界

本流程覆盖还款订单到额度恢复。未覆盖：逾期还款处理、代偿与追偿、多付退款（后续计划）；分配顺序的逐条规则在 Fineract 各分配策略实现中，主干不复制。
