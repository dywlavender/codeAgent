---
id: application-withdrawal
name: 贷款申请与提款
aliases:
  - 申请提款
  - 提款校验
tags:
  - 贷款申请
  - 提款
---

# 功能说明

贷款申请确定还款方式，提款时校验并使用申请阶段形成的业务数据，结算批任务处理后续状态。

## 业务场景

- 客户提交贷款申请
- 客户发起提款
- 批任务处理待结算记录

## 工程与入口

| 工程 | 类型 | 入口类 |
|---|---|---|
| application-service | 服务 | ApplicationController |
| withdrawal-service | 服务 | WithdrawalController |
| settlement-job | 批任务 | SettlementJob |

## 关键表

| 表名 | 数据作用 |
|---|---|
| loan_application | 保存贷款申请及还款方式 |

## 补充说明

该文档用于验证单仓库多工程的入口定位和跨工程检索。
