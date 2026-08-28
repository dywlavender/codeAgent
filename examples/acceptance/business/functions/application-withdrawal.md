---
id: application-withdrawal
name: 申请还款方式与提款校验
aliases:
  - 提款校验
tags:
  - 贷款申请
  - 提款
  - repayType
---

# 功能说明

申请阶段生成还款方式，提款阶段读取并校验该数据。

## 业务场景

- 贷款申请生成还款方式
- 提款时校验还款方式

## 工程与入口

| 工程 | 类型 | 入口类 |
|---|---|---|
| application | 服务 | ApplyService |
| withdraw | 服务 | WithdrawService |

## 关键表

| 表名 | 数据作用 |
|---|---|
| withdraw_apply | 保存提款申请数据 |
