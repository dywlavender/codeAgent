# 功能知识文档模板

一份文档描述一个可以独立提问的业务功能。人工只登记稳定的业务语义和检索入口；流程、规则、调用关系及源码位置由 Agent 从当前代码分析。

```markdown
---
id: customer-status-change
name: 客户状态变更
aliases:
  - 客户状态修改
tags:
  - 客户管理
  - 状态管理
---

# 功能说明

用于维护客户当前业务状态。

## 业务场景

- 内管人员修改客户状态
- 批任务刷新客户状态

## 工程与入口

| 工程 | 类型 | 入口类 |
|---|---|---|
| customer-admin | 内管 | CustomerStatusController |
| customer-service | 服务 | CustomerStatusServiceImpl |
| customer-batch | 批任务 | CustomerStatusRefreshJob |

## 关键表

| 表名 | 数据作用 |
|---|---|
| customer_info | 保存客户当前状态 |
| customer_status_log | 保存状态变更记录 |

## 补充说明

可选，只填写无法从代码中判断的稳定业务背景。
```

## 填写约束

- `id` 在知识目录内唯一，后续不要随意修改。
- “工程”可以填写独立仓库的工程 ID，也可以填写单仓库中的模块目录名。
- 入口类只填类名，不填写包名、文件路径或方法。
- 同一功能可以登记多个工程入口。
- 关键表只登记直接承载功能核心数据的表，并用一句话说明数据作用。
- 不人工填写流程、规则、调用链、SQL 和字段结构。

页面点击“更新知识库”后，系统扫描 `project.config.json` 中 `knowledge.root` 指定的目录。入口存在多个匹配或无法定位时会明确标记，不由模型自行选择。
