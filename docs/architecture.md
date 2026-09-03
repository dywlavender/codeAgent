# Claude Code Runtime 架构

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
  id, runtime, runtime_session_id, workspace_id, created_at, updated_at

query_run
  id, conversation_id, runtime, runtime_session_id, question, status,
  answer, error, usage_json, started_at, completed_at, duration_ms

query_event
  id, run_id, sequence, event_type, payload_json, created_at

query_message
  conversation_id, run_id, role, content, created_at
query_feedback
  run_id, rating, comment, created_at
```

一次请求的顺序：

1. 创建或读取 Conversation，准备工作区；
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

## 运行限制

- 必须在部署机安装并认证 Claude Code CLI；
- Claude 只能读取和搜索工作区文件，第一版不能修改源码或执行 Bash；
- 工作区只提供文件视图，不做 Requirement RAG 或 Python 侧检索；
- Entry Anchor 可能过时，Claude 可以将其作为起点并自行搜索其他文件；
- Claude 的回答质量取决于源码、业务基线和需求原文的可读性，管理员仍需维护资料目录。
