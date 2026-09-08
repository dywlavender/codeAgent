# Claude Code Runtime 架构

本文记录当前实现。后续涉及知识主干、知识图谱和代码调查的整改，遵循 [项目整改指导方向](knowledge-backbone-direction.md)；本文中的现有实现边界不构成对后续技术方案的固定限制。

## 目标

CodeAgent 不是另一套代码问答 Agent，而是给成熟 Coding Agent 准备正确资料和运行环境。Claude Code 负责理解问题、决定查什么、读取源码、追调用链、判断是否继续调查以及组织最终回答。

```text
用户问题
   ↓
QueryService
   ├─ 获取 Conversation / Claude Session
   ├─ 准备只读 Workspace
   └─ 调用 AgentRuntime
          ↓
   ClaudeCodeRuntime（claude -p --output-format stream-json）
          ↓
   Read / Glob / Grep
   ┌───────────────┬───────────────┬───────────────┐
   │ business docs │ requirements  │ source repos  │
   └───────────────┴───────────────┴───────────────┘
          ↓
   Tool Event + 最终回答
```

## Python 的边界

Python 只保留：

- 项目配置、Git 同步和代码索引；
- 业务基线、业务关系和 Entry Anchor 的管理页面；
- 工作区链接和固定 `CLAUDE.md`；
- Query Conversation、Run、Event、Feedback 持久化；
- Claude Code 子进程调用、超时和错误转换；
- HTTP/SSE API 和前端展示。

Python 不再执行问题分类、业务检索、Anchor 路由、Code Candidate 生成、证据循环、结构化事实、充分性评估或答案策略。

## Workspace

工作区由 `WorkspaceManager` 生成，默认位置为项目配置旁的 `.data/agent-workspaces/<project-id>`：

```text
<workspace>/
├── CLAUDE.md
├── knowledge/baseline/  → 配置的 baselineRoot
├── requirements/        → 配置的 requirementsRoot（可选）
└── repos/<repo-id>/     → 同步后的仓库目录
```

目录优先使用软链接；Windows 无法创建软链接时使用 Junction。链接始终指向已同步目录，不复制源代码。刷新工作区只重建链接和 `CLAUDE.md`，不会改动仓库。

`CLAUDE.md` 只写资料位置、源码必须实际读取、Anchor 只是导航提示、只读限制等稳定规则，不写固定 Agent Workflow。

业务基线可额外提供 `project-overview.md`。Runtime 每轮读取当前总览，连同检索范围通过 `--append-system-prompt` 提供；总览正文不写入 `CLAUDE.md`，其余业务流程文档保留在资料目录中供模型按需搜索。这是轻量项目上下文，不执行 Python 侧问题分类、图谱检索或固定调查流程。

查询和实验共用三种资料模式：无主干工作区只挂载正常项目资料（源码、README、需求等）；有主干工作区再挂载人工业务主干；AST 工作区挂载用户手动生成的 AST 结构资料，不挂载人工业务主干。三种模式分别位于注册工程的 `workspaces/<project-id>/modes/{none,backbone,ast}`（兼容单工程仍使用 `agent-workspaces`），模式变化不会复用另一模式的 Claude 会话。

AST 由 `evaluation/ast_service.py` 管理。服务启动、读取评测就绪状态、进入 AST 页面、切换 AST 问答模式和启动实验都只读取状态，不生成 AST；只有用户在 AST 管理页点击生成或重新生成才会创建新版本。源码变化会将当前版本标为待更新，历史查询和已开始的实验仍引用它们各自记录的版本。

## 工程注册与隔离

兼容启动方式仍可用 `--db` 加 `--project-config` 运行单个工程。平台模式通过
`ProjectRegistry` 登记多个工程，并由 `ProjectContext` 为当前请求提供配置文件、数据库和资料目录：

```text
<platform-data>/
├── registry.json
└── projects/<project-id>/
    ├── project.json
    ├── knowledge.db
    ├── ast/
    ├── evaluation-suites/
    ├── evaluations/
    ├── snapshots/
    └── workspaces/
```

每个注册工程使用独立数据库，因此会话、索引、业务知识、题库和评测批次不会因为切换配置而共用。请求可以使用规范路径
`/api/projects/<project-id>/...`；为兼容现有页面，也接受 `X-Project-Id` 或 `projectId`。注册工程生成的会话和运行编号带工程前缀，收到其他工程的会话编号会拒绝，不能把旧会话改写到当前工作区。

服务启动、工程切换、查询模式切换和实验启动都只读取 AST 状态。注册表登记只创建工程边界和空的托管目录，不会同步源码、索引、导入业务资料或生成 AST。业务基线和需求原文必须通过显式资料导入步骤进入工程目录；源码仓库仍按配置引用外部目录，源码同步和索引仍是后续独立任务。

登记不会覆盖已有工程资料，也不会自动迁移旧数据库、题库或评测记录；已有数据需要保留时，应显式指定原数据目录或执行资料迁移命令。导入后的业务基线和需求原文由工程目录托管，查询、知识维护和实验均使用该目录；源码仓库仍由配置明确指定，若多个工程共享同一外部仓库，应显式改为独立源码快照或接受共享。

资料迁移示例：

```text
python -m business_code_agent.cli project-import-materials \
  --registry .data/platform --project-id loan-system \
  --baseline-root knowledge/baseline --requirements-root requirements
```

项目示例题库也由配置显式声明，例如 `evaluation.examples: ["evaluations/cases.json"]`；未声明时平台仍可使用上传入口，但不会按贷款演示目录猜测题库。

注册与同步示例：

```text
python -m business_code_agent.cli project-register --registry .data/platform --config project.config.json
python -m business_code_agent.cli sync-project --project-registry .data/platform --project-id loan-system --config project.config.json
python -m business_code_agent.cli serve-query --db .data/unused.db --project-registry .data/platform
```

已有单工程数据需要保留时，应在登记时显式指定原数据目录；系统不会猜测并搬移历史库。

## Runtime

`AgentRuntime` 是最小接口：

```python
ask(question, *, workspace, session_id=None, event_callback=None)
```

`ClaudeCodeRuntime` 用 `subprocess` 启动当前安装的 `claude` CLI，使用 `--output-format stream-json` 解析事件。默认参数：

```text
--print
--output-format stream-json
--verbose
--permission-mode dontAsk
--tools Read,Glob,Grep
--disallowed-tools Edit,Write,Bash,NotebookEdit,Task
--add-dir <workspace>
```

首次问题不带 `--resume`；已有会话则传入保存的 Session ID。Runtime 不拼接 Python 历史，也不把索引摘要注入提示词。所有工具事件按原始事件类型和压缩后的 payload 保存，前端直接展示真实调查过程。

## Conversation / Run / Event

```text
query_conversation
  id, runtime, runtime_session_id, workspace_id, scope_json, mode, ast_version_id,
  created_at, updated_at

query_run
  id, conversation_id, runtime, runtime_session_id, question, status,
  answer, error, usage_json, scope_json, mode, ast_version_id,
  started_at, completed_at, duration_ms

query_event
  id, run_id, sequence, event_type, payload_json, created_at

query_message
  conversation_id, run_id, role, content, created_at
query_feedback
  run_id, rating, comment, created_at
```

一次请求的顺序：

1. 根据无主干／有主干／AST 模式创建或读取对应 Conversation，准备模式工作区；模式、调查范围或 AST 资料版本变化时清空可恢复的 Claude Session；
2. 创建 `running` 的 Query Run，并保存用户消息；
3. 调用 Runtime，实时保存并转发事件；
4. 成功后保存 Session、回答、用量和 assistant 消息；
5. 失败后将 Run 标记为 `failed`，保存错误事件，不生成 assistant 消息。

`query_message` 用于前端历史和审计，不是模型 Memory。模型 Memory 只由 Claude Session 提供。

## API 与 SSE

`POST /api/query` 返回一次性 JSON，`POST /api/query/stream` 返回：

```text
event: event   data: {"eventType":"tool_use", ...}
event: result  data: {"runId":"...", "answer":"...", ...}
event: error   data: {"error":"..."}
```

事件由 Claude CLI 产生，Python 不把它改写为 `UNDERSTAND`、`EVALUATE`、`EVIDENCE_GAP` 等旧阶段名。前端只展示用户问题、最终回答、事件时间线、历史和反馈。

## 代码索引边界

`repository`、`code_file`、`code_symbol`、`code_fact`、`cross_application_edge` 仍是同步/索引/浏览基础设施。它们帮助管理端查看代码，但 Claude Query 直接在工作区搜索和读取源码，不调用 `BusinessTools`、`EntryResolver` 或索引 Retriever。

`business_baseline_source`、`business_entity`、`business_relation_v2`、`business_entry_anchor` 继续服务知识维护。Query 不查询这些表，而是读取它们对应的 Markdown 文件；Anchor 原文是 Claude 的可选导航提示。

## 数据库迁移

打开数据库时：

- 旧 `query_agent_run` 会迁移为 `query_run`，历史运行标记为 `LEGACY_QUERY_AGENT`；
- 旧 `agent_run`、`query_agent_step`、`query_tool_call`、`query_checkpoint` 被删除；
- 旧 `query_feedback` 会重建为引用 `query_run` 的表；
- `functional_*`、旧 mapping、治理 proposal 表按既有迁移规则清除。

新库永远只创建 `query_run` 和 `query_event`，不会重新引入旧 Query Agent 表。

## 效果验证（评测子系统）

同题「有无知识主干」的对照验证由 `business_code_agent/evaluation/` 实现，命令行脚本与页面共用同一执行逻辑：

```text
business_code_agent/evaluation/
├── scoring.py   评分解析与引文校验（满足项必须引用候选答案原文）
├── harness.py   单任务执行：冻结工作区、Claude 调用、工具轨迹、独立会话初评
├── runner.py    批次编排：题库校验、资料冻结、并发执行、取消、按评分来源汇总
├── report.py    report.md / summary.json 生成与导出视图
└── service.py   EvaluationService：后台线程、state.json 持久化、题库/复核/快照
```

- 每个批次是 `.data/evaluations/eval-<时间戳>/` 下的一个目录：`protocol.json`（冻结的题库、设置与仓库快照）、`inputs/`（冻结代码与主干文档）、`runs/<run-id>/`（逐次结果、事件、评分及修订历史）、`state.json`（页面进度）、`results.json`、`reviews.json`（人工复核修订）。
- 新建批次固定本轮题目、参考答案、评分要点、源码以及资料版本。三组默认是无主干、有主干、AST；AST 组只引用 AST 管理页已经生成的版本，`protocol.json` 记录版本号和独立生成耗时，实验启动不会隐式生成 AST。
- 启用自动初评时，所有启用案例必须先有固定评分要点；没有要点的案例会在启动前列出并阻止批次。用户可以明确选择“仅答题”，此时不创建空评审，也不把质量指标当作零分。
- `EvaluationService` 在 HTTP 请求之外的后台线程运行批次（批内并发 2），每个任务落盘后更新状态；页面约 2 秒轮询批次摘要，任务结束停止轮询。服务重启时运行中的批次标记为 `interrupted`，保留已完成结果，不自动追加调用。
- 取消停止安排新任务并取消受本批次管理的模型调用；答题失败不评分，评分失败不记零分。质量汇总按明确评分来源（模型初评 / 复核记录）分别计算配对分母，不把未复核答案的模型分数混入复核视图。
- “重试失败答题”创建独立子批次，只复制原批次冻结的 `protocol.json` 与 `inputs/`，并按案例、轮次、模式精确保留失败任务；不重新读取当前题库、源码或 AST 状态，原批次结果不被覆盖。三组汇总使用共同完成样本，各两组比较独立构造自己的配对集合。
- 页面入口为 `#/evaluation`（「效果验证」）；启动、停止、重新初评、题库修改和复核保存复用管理员凭证，读取接口公开。
- CLI 入口保留：`scripts/evaluate_backbone.py`（对照执行与 `--rejudge`）、`scripts/backbone_report.py`（报告重生成）调用同一模块。结果目录兼容旧脚本读取。

## 运行限制

- 必须在部署机安装并认证 Claude Code CLI；
- Claude 只能读取和搜索工作区文件，第一版不能修改源码或执行 Bash；
- 工作区只提供文件视图，不做 Requirement RAG 或 Python 侧检索；
- Entry Anchor 可能过时，Claude 可以将其作为起点并自行搜索其他文件；
- Claude 的回答质量取决于源码、业务基线和需求原文的可读性，管理员仍需维护资料目录。
