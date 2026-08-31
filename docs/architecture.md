# 当前架构

## 核心原则

> CodeAgent 的核心是 Agent，不是代码知识库。

业务知识帮助 Agent 理解问题；Project Context 告诉 Agent 系统、应用和仓库边界；Entry Anchor 帮助 Agent 找到调查起点。入口之后的 Method、调用链、HTTP/RPC/MQ、SQL、代码行和源码片段，都由 Agent 基于当前代码索引在本次问答中实时调查，不作为长期业务知识。

```text
用户问题
  ↓
Question Agent
  ├─ Business Knowledge（业务语义）
  ├─ Project Context（系统 / 应用 / 仓库）
  └─ Entry Anchor（可选调查起点）
          ↓
    Runtime Investigation
          ↓
    当前 Code Fact / 源码 / 跨应用边
          ↓
    Runtime Evidence → Answer
```

## 数据分层

### 长期业务知识

`business_baseline_source`、`business_entity`、`business_relation_v2` 保存人工基线结构化结果，类型只有 `SYSTEM`、`BUSINESS_TERM`、`CAPABILITY`、`FLOW`、`RULE` 和 `RELATION`。模型只能结构化原文，所有字段必须能回到同一 Markdown 小节的原文证据。

### Project Context

`software_system`、`application`、`repository`、`application_code_file` 描述代码归属和部署边界。它们不等于业务知识中的 `SYSTEM`。一个流程可以有多个应用入口，同名类通过 `application_id` 隔离。

### Entry Anchor

`business_entry_anchor` 是唯一允许长期保存的代码导航提示，且只挂在 `FLOW` / `CAPABILITY` 上：

```text
business_type + business_id
application_id
entry_type（PAGE / CONTROLLER / JOB / CONSUMER / ENTRY_CLASS / OTHER）
entry_name
source_type（HUMAN / AI_CANDIDATE）
status
```

Entry Anchor 不保存 `symbol_id`、限定类名、方法、文件、行号或调用链。每次问答由 `EntryResolver` 在指定应用的最新索引中返回 `RESOLVED`、`MULTIPLE` 或 `NOT_FOUND`。解析失败只触发普通代码搜索，不修改知识。

### 可重建代码索引

`code_file`、`code_symbol`、`code_fact` 和 `cross_application_edge` 属于当前代码索引。代码同步后可以重建；跨应用边由当前 HTTP、Feign 等代码证据实时重建，不需要人工维护业务版本。

### 单次运行数据

`query_agent_run`、checkpoint、工具调用、源码片段和 Runtime Evidence 只服务于当前问答与回放。普通实现细节不会自动升格为 Business Knowledge 或 Entry Anchor。

## Baseline 导入

```text
扫描 Markdown
→ 模型结构化或保守解析
→ 验证实体、关系和 Entry Anchor 的同节原文依据
→ 保存业务知识、业务关系和入口锚点
→ 结束
```

基线刷新不再调用 `CodeMatcher`，也不生成普通 Business→Code Mapping。旧 `business_code_mapping`、`business_code_mapping_observation` 表暂时保留用于历史兼容，但不参与默认导入、问答、UI 或 workspace 统计；不自动把旧 Mapping 批量转换成 Anchor。

## Query Agent 检索顺序

```text
理解问题
→ 搜索 Business Knowledge
→ 读取 FLOW/CAPABILITY 的 Entry Anchor
→ 按 application + entryName 解析当前 Symbol
→ 实时读取入口源码和 Code Fact
→ 沿本地调用、HTTP/RPC 和 cross_application_edge 调查
→ 证据不足时退化到字段、表或全局代码搜索
→ 输出事实、链路、冲突和未知项
```

Anchor 是优先级提示，不是搜索边界。一个流程可以有多个入口，Planner 根据问题选择；入口失效、同名冲突或代码重构都不会让 Agent 直接相信旧实现。

`businessFlow` 只承载业务基线证据，`technicalFlow` 只承载当前代码证据。代码候选没有加载并验证 Evidence 前，不能成为回答事实。

## 管理端与用户端

用户端只有 Query Agent：回答问题、展示实时技术链路和 Evidence。管理端负责导入/刷新业务基线、查看业务关系和维护入口锚点。Knowledge 页面不展示普通 Code Mapping、Symbol、Method、Line 或代码 Evidence；这些内容只在问答调查结果中出现。

## 兼容说明

旧 `functional_*`、`business_code_mapping*` 及其显式管理接口暂时保留，便于已有数据库和客户端平滑迁移。它们不应作为新功能的主链路；后续确认没有历史依赖后再清理。

## 当前限制

- Web 索引采用保守静态模式，动态 URL、运行时路由和无法唯一解析的依赖注入不会强行连边；
- 当前跨应用边主要覆盖 HTTP 和 Feign，MQ/Job 入口可通过 Anchor 开始调查，但自动协议识别仍有限；
- 无模型时的基线结构化是保守解析，不等价于完整语义理解；
- 入口锚点目前只接受人工基线，暂不自动学习新的 Anchor Candidate；
- 跨文档业务实体合并仍需人工确认。
