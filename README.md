# Business Code Agent

一个面向多仓库老项目的业务代码问答工作台。查询由 Claude Code 负责理解问题、搜索业务资料、读取真实源码、追调用链并回答；Python 只负责项目同步、知识维护、工作区准备、运行记录和前端 API。

## 查询主链

```text
用户问题
  ↓
Claude Code Runtime（可恢复 Session）
  ↓
自行搜索 knowledge/baseline、requirements、repos
  ↓
读取真实源码并继续调查
  ↓
正常对话回答 + 原始 Tool Event
```

Python 不再做 Intent 分类、Retriever、EntryResolver、Evidence Loop、StructuredFact、Sufficiency 或 Answer Policy。`code_file`、`code_symbol`、`code_fact`、`cross_application_edge` 仍保留给同步、索引、知识管理和浏览页面，查询主链不调用它们。

## 前置条件

- Python 3.11+
- Node.js 20+
- Git（同步内网仓库时需要）
- 已安装并登录 Claude Code CLI，且 `claude` 在 PATH 中；也可用 `CLAUDE_CODE_COMMAND` 指定路径

```bash
claude --help
claude auth status
```

查询只启用 `Read`、`Glob`、`Grep`，并禁止 `Edit`、`Write`、`Bash`、`Task` 等修改型工具。Claude 只会在生成的工作区内读文件。

## 一键启动

### macOS

```bash
chmod +x start-mac.sh
./start-mac.sh --project-config project.config.json
```

### Windows

双击 `start-windows.bat`，或在 PowerShell 执行：

```powershell
.\scripts\start-windows.ps1 -ProjectConfig .\project.config.json
```

启动器会创建 Python 环境、同步并增量索引 Git 仓库、构建前端，然后启动 `http://127.0.0.1:8082/`。端口被占用时会停止监听该端口的旧进程再启动。重复启动不会复制源码。

## 项目配置

```bash
cp project.config.example.json project.config.json
```

至少配置项目、仓库和业务基线目录。`gitUrl` 必须是部署机可以访问的内网地址；不配置 `localPath` 时，仓库会放在 `repositoryRoot/<id>`。

```json
{
  "project": {"id": "loan-system", "name": "贷款核心系统"},
  "startup": {"database": ".data/knowledge.db", "port": 8082},
  "repositoryRoot": ".data/repositories",
  "knowledge": {"baselineRoot": "knowledge/baseline"},
  "requirementsRoot": "requirements",
  "repositories": [
    {"id": "loan-core", "gitUrl": "ssh://git@git.company.local/loan/core.git", "branch": "main"},
    {"id": "loan-job", "gitUrl": "ssh://git@git.company.local/loan/job.git", "branch": "develop"}
  ],
  "systems": [{"id": "loan", "name": "贷款系统"}],
  "applications": [{
    "id": "loan-core-app", "name": "贷款核心服务", "systemId": "loan",
    "repositoryId": "loan-core", "sourceRoot": ".", "type": "BACKEND",
    "language": "java", "framework": "spring-boot"
  }]
}
```

同步规则：首次运行克隆；本地与远端一致则跳过；远端可快进时自动更新；本地有未提交修改或发生分叉时停止并提示人工处理。

## Claude Runtime 配置

查询不读取旧的 `BUSINESS_CODE_MODEL_*` LangChain 配置，而是直接调用本机 Claude Code：

```dotenv
CLAUDE_CODE_COMMAND=claude
CLAUDE_CODE_TIMEOUT=600
CLAUDE_CODE_READ_TOOLS=Read,Glob,Grep
```

Claude Code 的登录、模型和 API 凭据按其 CLI 规则配置（例如 `claude auth login` 或企业网关设置）。

## 工作区

每个项目自动生成：

```text
.data/agent-workspaces/<project-id>/
├── CLAUDE.md
├── knowledge/baseline/  → knowledge.baselineRoot
├── requirements/        → requirementsRoot
└── repos/<repo-id>/     → 已同步仓库
```

目录优先使用软链接，Windows 无法创建软链接时使用目录 Junction；不会复制或修改源码。`CLAUDE.md` 只包含资料位置、调查原则和只读约束。Entry Anchor 作为业务文档中的导航提示，由 Claude 自行判断是否使用，不由 Python 强制路由。

## 业务知识和需求

管理员仍可在“业务知识维护”中刷新 `knowledge/baseline`，并使用 `business_entity`、`business_relation_v2`、`business_entry_anchor` 浏览和维护结构化结果。需求原文放在 `requirements/`，查询阶段直接由 Claude 搜索，不经过 Requirement RAG。

业务基线示例：

```markdown
# 提款业务基线

## 提款主流程

渠道提交提款申请，中台完成业务编排，核心完成放款。

### 调查入口

- channel-h5 | PAGE | WithdrawApply.vue
- loan-middle | CONTROLLER | MiddleWithdrawController
```

## API

- `GET /api/workspace`：项目、工作区、仓库和索引摘要
- `GET /api/runs`：最近运行
- `GET /api/conversations?limit=20&cursor=...`：按会话分页，返回 items 和 nextCursor
- `POST /api/query`：单次问答
- `POST /api/query/stream`：SSE 流式问答，将 Claude 输出转换成状态、工具步骤和文本增量
- `GET /api/conversations/{conversationId}`：按时间恢复完整多轮会话
- `GET /api/query/{runId}`：回答、事件、用量和反馈
- `POST /api/query/{runId}/cancel`：取消指定的运行，返回 cancelling 或已结束状态
- `POST /api/query/{runId}/feedback`：提交有帮助/需改进反馈
- `GET /api/code/search`、`/api/code/symbol/{id}`：代码索引浏览
- `/api/knowledge/*`：管理员知识导入与浏览

问答输入：

```json
{"question":"中台发起提款前具体校验哪些条件？","conversationId":"可选"}
```

返回：

```json
{
  "runId": "RUN-...",
  "conversationId": "CONV-...",
  "runtime": "CLAUDE_CODE",
  "sessionId": "...",
  "status": "completed",
  "answer": "...",
  "events": []
}
```

多轮对话只提交 `conversationId`。服务端保存 Claude 的 `sessionId` 并使用 `claude --resume`，不会把 Python 保存的历史消息重新拼进提示词。

### 对话页面与进度事件

左侧按会话归组，点击历史恢复该会话的全部问答；中间展示对话，底部输入框支持 Enter 发送、Shift + Enter 换行。中文输入法确认候选词不会触发发送。侧栏可收起，小屏使用抽屉式导航。

侧栏每页显示 20 个会话，点击“加载更多会话”继续浏览，不再受最近 30 次问答的限制。同一浏览器标签页刷新后会自动恢复当前会话；若任务仍为 running/cancelling，页面每秒读取最新进度，保留停止按钮，完成后恢复输入。重新打开历史中的运行任务也按此方式跟踪，不会重复调用模型。

同一会话同时只允许一个运行中或取消中的任务。后端通过 SQLite 事务检查，重复请求返回 HTTP 409（SSE 返回 CONVERSATION_BUSY 错误），不会额外写入问答或启动模型。不同会话仍可独立运行。

单轮问答失败显示在对应回答位置；刷新列表、恢复进度或停止任务等操作失败显示在输入框上方，历史中的失败记录不会遮住新的操作错误。

调查过程默认折叠，点击状态行可展开工具步骤和返回内容。工具调用与结果使用同一个 ID 更新，不产生两条重复步骤；失败的工具会单独标识，不自动视为成功。回答支持 Markdown 标题、列表、表格和代码块复制。

运行时启用 `--include-partial-messages`，文本按约 100ms 合并发送；文本块结束时立即发送剩余内容。最终回答以 result 为准，不截断。内部思考文本、签名及 token 估算增量不转发、不入库；同一思考阶段只发送一次状态。

SSE 的 `event` 消息使用以下持久化结构，`sequence` 在每次运行内递增：

| eventType | payload | 页面行为 |
| --- | --- | --- |
| status | phase、label | 更新当前阶段 |
| tool | id、name、input、status、output（可选） | 按 id 更新工具步骤 |
| text | id、text、mode（append / replace） | 按文本块追加或替换，避免完整消息重复显示 |
| error | error | 显示失败原因 |

SSE 的 `result` 消息保留完整回答、运行 ID、会话 ID、用量和事件。旧历史事件仍能浏览，无需迁移数据库。

点击“停止生成”会请求后端终止这一轮 Claude 进程。按钮显示“正在停止”，收到后端确认后才显示“已停止”；已收到的部分回答、工具记录和 Session 会保留，可以继续追问。取消的运行状态为 `cancelled`，不是失败或成功。任务已经完成时，取消不会覆盖结果。启动阶段点击停止会等运行 ID 到达后立即发送取消请求。

SSE 首先发送 `run` 消息（runId、conversationId），供前端关联取消请求。单纯关闭页面或断开网络不会取消后台任务。

## 数据库

查询运行数据只有：

- `query_conversation`：运行时、Claude Session 和工作区
- `query_message`：前端历史和审计消息
- `query_run`：问题、状态、回答、耗时、用量
- `query_event`：归一化后的状态、工具和文本事件（旧记录保留原格式）
- `query_feedback`：人工反馈

打开旧数据库时会自动把旧 `query_agent_*` 运行记录迁移为只读的 `LEGACY_QUERY_AGENT` 历史，然后删除 `agent_run`、`query_agent_run`、步骤、工具调用和 checkpoint 表。旧 `functional_*`、mapping 和治理遗留表也会按现有迁移规则清理。

## 离线/内网部署

应用包可以在构建机生成 Windows 和 Linux 两个 ZIP；部署机仍可访问内网 Git 和 Python 包源时，使用对应启动器同步最新仓库。详见 [离线部署方案](docs/offline-deployment.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
cd frontend && npm test && npm run build
```

问答测试覆盖 Runtime、Workspace、QueryService、进度事件归一化及前端 SSE/步骤归并；不再保留旧自研 Query Agent 的分类、证据评估和 LangGraph 测试。

更完整的设计说明见 [当前架构](docs/architecture.md)。
