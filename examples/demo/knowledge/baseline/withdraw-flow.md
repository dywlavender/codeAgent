# 贷款提款业务演示

## 提款流程

申请阶段确定还款方式并写入 `repayType`，提款阶段读取申请数据并校验 `repayType`。提款不能重新选择还款方式，校验不通过时拒绝提款。

### 调查入口

- demo-application | ENTRY_CLASS | ApplyService
- demo-application | ENTRY_CLASS | WithdrawService

## 提款规则

提款阶段必须沿用申请阶段确定的 `repayType`，当前产品只允许值 `A`，不满足时拒绝提款。

