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
- `POST /api/query`：单次问答
- `POST /api/query/stream`：SSE 流式问答，转发 Claude 的真实事件
- `GET /api/query/{runId}`：回答、事件、用量和反馈
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

## 数据库

查询运行数据只有：

- `query_conversation`：运行时、Claude Session 和工作区
- `query_message`：前端历史和审计消息
- `query_run`：问题、状态、回答、耗时、用量
- `query_event`：Claude stream-json 事件
- `query_feedback`：人工反馈

打开旧数据库时会自动把旧 `query_agent_*` 运行记录迁移为只读的 `LEGACY_QUERY_AGENT` 历史，然后删除 `agent_run`、`query_agent_run`、步骤、工具调用和 checkpoint 表。旧 `functional_*`、mapping 和治理遗留表也会按现有迁移规则清理。

## 离线/内网部署

应用包可以在构建机生成 Windows 和 Linux 两个 ZIP；部署机仍可访问内网 Git 和 Python 包源时，使用对应启动器同步最新仓库。详见 [离线部署方案](docs/offline-deployment.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
cd frontend && npm run build
```

新增测试只覆盖 Runtime、Workspace、QueryService 三层；不再保留旧自研 Query Agent 的分类、证据评估和 LangGraph 测试。

更完整的设计说明见 [当前架构](docs/architecture.md)。
