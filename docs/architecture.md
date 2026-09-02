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

Entry Anchor 不保存 `symbol_id`、限定类名、方法、文件、行号或调用链。每次问答由 `EntryResolver` 在指定应用的最新索引中返回 `RESOLVED`、`MULTIPLE` 或 `NOT_FOUND`。已解析入口优先作为调查起点；如果匹配业务卡片没有已解析入口（全部失效或尚未维护），Agent 最多发起一次当前索引搜索来恢复调查，不修改知识，也不会把旧入口当成事实。

### 可重建代码索引

`code_file`、`code_symbol`、`code_fact` 和 `cross_application_edge` 属于当前代码索引。代码同步后可以重建；跨应用边由当前 HTTP、Feign 等代码证据实时重建，不需要人工维护业务版本。

### 单次运行数据

`query_agent_run`、checkpoint、工具调用、源码片段和 Runtime Evidence 只服务于当前问答与回放。普通实现细节不会自动升格为 Business Knowledge 或 Entry Anchor。

## Baseline 导入

```text
扫描 Markdown
→ 模型结构化（需要无模型时，显式选择 Markdown 解析）
→ 验证实体、关系和 Entry Anchor 的同节原文依据
→ 保存业务知识、业务关系和入口锚点
→ 结束
```

基线刷新不调用 `CodeMatcher`，也不生成业务—代码持久关联。业务知识只保存入口锚点；入口之后的代码关联全部由 Query Agent 基于当前索引实时调查。

## Query Agent 检索顺序

```text
理解问题
→ 搜索 Business Knowledge
→ 读取 FLOW/CAPABILITY 的 Entry Anchor
→ 按 application + entryName 解析当前 Symbol
→ 实时读取入口源码和 Code Fact
→ 沿本地调用、HTTP/RPC 和 cross_application_edge 调查
→ 证据不足时按明确的字段、表、Symbol 或关系目标继续调查
→ 输出事实、链路、冲突和未知项
```

调查结束后由 `AnswerPolicy` 统一收口：`SUFFICIENT` 且有已验证事实、无冲突时为 `FULL`，允许可选的 Model Composer；`INSUFFICIENT` 有事实为 `PARTIAL`，无事实为 `UNKNOWN`；`CONFLICT` 为 `CONFLICT`。后三类仍是正常完成的查询，由确定性回答和渲染分支处理，不转换成异常；只有 Agent 执行异常才进入 `ERROR`。

Anchor 是调查起点，不是可直接采信的实现结论。一个流程可以有多个入口，Planner 根据问题选择；只要仍有入口解析成功，就不会扩大到全局搜索；入口全部失效、同名冲突或尚未维护时会明确报告，并允许一次当前索引搜索作为恢复路径。

`businessFlow` 只承载业务基线证据，`technicalFlow` 只承载当前代码证据。代码候选没有加载并验证 Evidence 前，不能成为回答事实。

## 管理端与用户端

用户端只有 Query Agent：回答问题、展示实时技术链路和 Evidence。管理端负责导入/刷新业务基线、查看业务关系和维护入口锚点。Knowledge 页面不展示代码关联、Symbol、Method、Line 或代码 Evidence；这些内容只在问答调查结果中出现。

## 数据库迁移

打开数据库时会自动删除早期 MVP 遗留的 `business_code_mapping` 和
`business_code_mapping_observation` 表及其索引。它们不再属于当前数据模型；如需保留历史记录，请在升级前自行导出。
同样，早期 `functional_*` 功能知识表会在首次打开时一次性清理；当前系统只保留
`business_entity`、`business_relation_v2` 和 `business_entry_anchor` 作为业务知识来源。

## 当前限制

- Web 索引采用保守静态模式，动态 URL、运行时路由和无法唯一解析的依赖注入不会强行连边；
- 当前跨应用边主要覆盖 HTTP 和 Feign，MQ/Job 入口可通过 Anchor 开始调查，但自动协议识别仍有限；
- 业务基线默认需要模型；无模型导入必须显式选择 Markdown 解析，不会在模型失败后自动切换；
- 入口锚点目前只接受人工基线，暂不自动学习新的 Anchor Candidate；
- 跨文档业务实体合并仍需人工确认。
