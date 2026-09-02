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
    Query Agent 调查循环
      ├─ search_business（理解语义）
      ├─ search_code（导航索引）
      ├─ read_source（读取最终事实）
      ├─ find_references / follow_call / follow_integration
      └─ 按当前问题继续或停止
          ↓
    Answer + 本次源码引用
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

`query_agent_run`、checkpoint、工具调用、源码引用和 Runtime Evidence 只服务于当前问答与回放。源码正文只在模型调查期间进入上下文；持久化的 `sourceReferences` 仅保留 `sourceId`、文件和行范围，普通实现细节不会自动升格为 Business Knowledge 或 Entry Anchor。

## Baseline 导入

```text
扫描 Markdown
→ 模型结构化（需要无模型时，显式选择 Markdown 解析）
→ 验证实体、关系和 Entry Anchor 的同节原文依据
→ 保存业务知识、业务关系和入口锚点
→ 结束
```

基线刷新不调用 `CodeMatcher`，也不生成业务—代码持久关联。业务知识只保存入口锚点；入口之后的代码关联全部由 Query Agent 基于当前索引实时调查。

## Query Agent 调查循环

```text
理解问题
→ Business Knowledge / Entry Anchor 定位起点
→ search_code 找候选 Symbol
→ read_source 读取真实源码
→ 根据源码和当前问题选择 follow_call / find_references / follow_integration
→ 再 search / read，直到模型判断已经足够
→ 输出直接回答和源码引用
```

代码索引（`code_file`、`code_symbol`、`code_fact`、`cross_application_edge`）只负责导航，不直接成为回答事实。只有模型实际调用 `read_source` 后，系统才创建本次查询的 `SRC-*` 引用；代码 claim 必须引用该引用。`find_references`、`follow_call` 和 `follow_integration` 返回的只是下一步导航信息，模型需要继续读取相关源码后才能下结论。

模型可用的第一版工具只有六类：`search_business`（业务卡片、关系和入口提示）、`search_code`（Symbol/Fact 导航摘要）、`read_source`（真实文件行范围）、`find_references`（调用方）、`follow_call`（应用内调用目标）和 `follow_integration`（HTTP/RPC/MQ 跨应用边）。工具结果不会把索引摘要伪装成源码事实。

启用模型时，`LangChainQueryInvestigator` 负责自主 Tool Calling 循环，直接根据用户问题、业务上下文和已读取源码组织答案；不再要求先把源码转换成 `StructuredFact`，也不由固定 `EvidenceSufficiencyEvaluator` 决定调查是否完成。`StructuredFact`、Evaluator 和旧 `AnswerPolicy` 仍保留给无模型/兼容路径，避免离线部署和历史调用立即失效。

回答的 `answerType` 由调查结果表达为 `FULL`、`PARTIAL`、`CONFLICT` 或 `UNKNOWN`。引用校验是硬边界：未被 `read_source` 返回的源码引用会被拒绝；模型可以把无法确认的内容放入 `unknowns`，但不能根据类名、方法名或调用关系猜测实现。

Anchor 是调查起点，不是可直接采信的实现结论。一个流程可以有多个入口，Planner 根据问题选择；只要仍有入口解析成功，就不会扩大到全局搜索；入口全部失效、同名冲突或尚未维护时会明确报告，并允许一次当前索引搜索作为恢复路径。

`businessFlow` 只承载业务知识 claim，`technicalFlow` 只承载源码调查 claim。需要链路时调查 integration，需要逻辑时重点读取条件、分支、计算和异常，不再对所有问题强行闭合完整调用链。

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
