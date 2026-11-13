# Business Code Agent

一个面向多仓库老项目的业务代码问答工作台。生产主问答由项目自有 LangChain 1.2 Agent 调用配置的模型，管理上下文、搜索资料、读取源码、追踪关系并回答；Python 同时提供工程边界、只读工具和运行记录。

后续知识主干、词云（知识图谱）及问答调查的整改，以 [知识主干与知识图谱：项目整改指导方向](docs/spec/knowledge-backbone-direction.md) 为依据：帮助模型建立项目整体认识、理解业务全流程及术语关系、掌握调查入口，减少无关探索和关键流程遗漏。下文描述当前实现，不代表该目标已经完成或收益已经验证。

当前内容核查与有／无主干的真实模型对照见 [2026-09-05 验证报告](docs/records/2026-09/knowledge-backbone-validation-20260905.md)。本轮尚未证明现有主干能稳定减少调查成本。

后续已完成 [项目总览＋按需流程的两层改写与复测](docs/records/2026-09/knowledge-backbone-rewrite-20260905.md)，有局部改善，仍未证明稳定的整体效率优势。

## 查询主链

```text
用户问题
  ↓
LangChain 1.2 Agent（工程内可恢复会话）
  ↓
Read / Glob / Grep，以及显式准备索引后的 SearchCode
  ↓
读取真实源码并继续调查
  ↓
无工具整理答案（可选 facts 分层与程序检查）
```

可选混合检索使用函数／类单元、BM25 与向量并发召回（权重 0.6／0.4）、Top-50 Cross-Encoder 和有行数限制的上下文。重排序目标预算为 200ms，超预算会明确记录并保留融合顺序，是否达标需以实测为准。索引由显式命令创建和基于 Git diff 增量更新。

默认 `BUSINESS_CODE_AGENT_ANSWER_MODE=plain`。可选 `facts` 实验将接口返回数据、展示映射和页面使用分开，核对本轮已交付原文、行号及有限的一致性，检查记录随运行保存。两次真实固定证据复验仍有对象归属和引用遗漏，尚未通过默认启用验收；业务语义和跨系统绑定需要独立复核。修改模式后重启服务。详见[事实分层复验记录](docs/records/2026-10/answer-facts-validation-20261031.md)。

默认 `BUSINESS_CODE_AGENT_WORKFLOW=coordinated`：程序按问题目标和新到达的证据安排文件定位、混合检索、契约核对和缺口补读，模型可继续搜索并追加调查。文件题直接读原文，字段题不强制展开无关分支；状态和跨系统问题按需补齐依据。`basic`／`tracked` 保留作对照，默认答案仍为 plain。调度实现不等于业务答案正确率已验证。

主问答不设调查轮数、总时长、自动补查、入口或任务总数上限；可随时手动停止。上下文容量、单次原文大小与网络请求超时继续管理。旧 `BUSINESS_CODE_AGENT_MAX_CALLS`／`BUSINESS_CODE_AGENT_TIMEOUT` 不再限制生产调查，有界评测由脚本的 `--max-calls`／`--max-seconds` 显式指定。

每轮问答点击“查看调用轨迹”，可查看公开模型请求／响应、工具定义、完整工具结果、程序调度原因、调查状态与错误，支持筛选和导出 JSON，运行中及失败／取消记录也可查看。完整归档位于工程数据目录 `query-traces/<runId>.json`，不随工作区刷新删除；旧运行优先复用已有 `.langchain-traces/`，缺失的旧请求不补造。记录不包含认证信息或模型私有思考。

已准备的契约索引可增加 `FindCapability`、`TraceContract`、`TraceResponseFields`、`TraceBusinessRules`，覆盖 Spring HTTP、RestClient 配置连接、受支持的返回对象字段及相应实体成员的 setter 写入点。默认 `BUSINESS_CODE_AGENT_CONTRACTS=auto`、`BUSINESS_CODE_AGENT_RETRIEVAL=auto`，只复用已有索引，不自动创建或更新；files/off 保留文件对照，hybrid/on 可显式启用。未解析连接自动形成源码补读任务，补读不会隐式重建索引。`ANSWER_MODE=contracts` 仍为实验性收尾，不保证整体正确或改动收益。

“知识图谱 → 调用链”可直接浏览已准备索引中的接口 → 服务方法 → HTTP 客户端 → 远端接口，点击节点或连线查看源码依据，也可从远端接口继续展开。支持接口搜索、缩放和平移；原业务知识图保留在“业务关系”。浏览无需启用问答实验，不调用模型或生成 AST，当前显示的是已解析的返回依赖调用关系。

```bash
python scripts/evaluation/prepare_contract_index.py --project-config project.config.json
```

索引保存在当前工程数据目录的 `contract-index/contracts.sqlite`。重复命令按文件变化增量解析并保存 Git diff 行范围；解析器升级时显式加 `--reparse`。注册工程也可用 `--project-registry <注册表路径> --project-id <工程编号>`。当前只支持有源码与明确配置绑定的窄范围结构，不把缺失字段或未连接端点解释成不存在。此前真实回归的异常路径解释和调查成本仍有问题，详见[业务工作流与实测记录](docs/records/2026-10/contract-business-workflow-20261031.md)；新调度的质量收益需按[检索诊断约定](docs/spec/retrieval-evaluation.md)单独验证。

## 前置条件

- Python 3.11+
- Node.js 20+
- Git（同步内网仓库时需要）
- `.env` 中配置兼容模型的 `BUSINESS_CODE_MODEL_BASE_URL`、`NAME` 和 `API_KEY`；参考 `.env.example`
- Claude CLI 仅为历史评测、部分知识维护和显式兼容运行所需

```bash
python -m pip install -e '.[tree-sitter,retrieval]'
python scripts/evaluation/prepare_hybrid_index.py --project-config project.config.json --download-models
```

主问答默认组合使用基础资料、业务补充、已有代码地图和可用的手动 AST 版本，按问题需要搜索和读取；AST 不可用时继续使用其他资料。互斥资料分组只用于历史兼容与显式对照，页面不再三选一。

问答工具只搜索和读取本轮授权的资料。coordinated 默认 auto 复用已准备的混合索引；`BUSINESS_CODE_AGENT_RETRIEVAL=files` 保留文件工具基线，`hybrid` 可显式启用 SearchCode。修改设置后重启服务。索引过期条目会提示重新读取。不会在服务启动或问答时自动生成 AST、下载模型或重建索引。已有索引再次执行上述准备命令时只嵌入变化片段；模型已存在时无需 `--download-models`。

## 一键启动

在项目根目录运行。Windows 使用 Git Bash。

```bash
bash ./start.sh
```

根目录已有 `project.config.json` 时会自动读取，支持 UTF-8（含 BOM）。启动器默认创建或复用项目内的 Python 环境，同步并增量索引 Git 仓库，构建前端，然后启动 `http://127.0.0.1:8082/`。端口被占用时会停止监听该端口的旧进程再启动。重复启动不会复制源码。

可以用 `--python` 复用已有 Python 3.11+ 环境。macOS 或 Linux 传 `--python "/path/to/venv/bin/python"`，Windows 传 `--python "D:/venvs/code-agent/Scripts/python.exe"`。长期使用同一个环境时，在工程配置的 `startup` 中添加 `python`，值为解释器路径。相对路径以项目根目录为准，命令行参数优先。启动时会显示实际使用的 Python 版本和路径。

新机器的环境安装、项目包生成和首次部署请按[Windows 部署步骤](docs/spec/offline-deployment.md)从上到下执行。

## 项目配置

```bash
cp project.config.example.json project.config.json
```

至少配置项目、仓库和业务补充知识目录。`gitUrl` 必须是部署机可以访问的内网地址；不配置 `localPath` 时，仓库会放在 `repositoryRoot/<id>`。旧配置中的 `baselineRoot` 仍兼容。

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

查询、评测和业务补充知识结构化都直接调用本机 Claude Code：

```dotenv
CLAUDE_CODE_COMMAND=claude
CLAUDE_CODE_TIMEOUT=600
CLAUDE_CODE_READ_TOOLS=Read,Glob,Grep
```

Claude Code 的登录、模型和 API 凭据按其 CLI 规则配置（例如 `claude auth login` 或企业网关设置）。

## 工作区

每轮问答（含续聊）通过 `--append-system-prompt` 显式传入当前真实资料目录及检索规则，与 `--add-dir` 使用同一份来源清单。跨仓库分别搜索，不自动授权共同上级目录；错误路径可改查已授权目录，正确路径仍被拒绝时报告该来源的访问问题。该约束属于 Agent 提示规则，不是工具调用的强制路径重写。

若业务基线目录存在 `project-overview.md`，每轮同时提供其当前内容，帮助 Agent 了解项目范围、系统职责和业务目录。具体流程文档继续按需读取；总览提供调查地图，当前实现仍需源码确认。编写方式见 [业务基线使用指南](docs/spec/business-baseline-guide.md)。

用户侧“项目资料”展示 Agent 的真实资料目录、目录可读/空/缺失状态及目录授权配置，支持分类筛选、路径复制和刷新。刷新不会拉取代码或导入知识；目录授权配置不等于模型访问实测通过。结构化业务知识在管理员“业务知识维护”中查看，代码索引接口仍保留，但不再作为用户侧一级浏览入口。

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

## 部署

Windows 部署包包含项目源码、启动脚本、配置模板和手册。用 Python 3.11+ 运行 `scripts/offline/build_offline_bundle.py --target windows` 生成 ZIP。解压后打开包内 `README.md` 查看环境要求和配置步骤。详见 [Windows 部署步骤](docs/spec/offline-deployment.md)和 [Windows 启动与参数参考](docs/spec/windows-usage.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
cd frontend && npm test && npm run build
```

问答测试覆盖 Runtime、Workspace、QueryService、进度事件归一化及前端 SSE/步骤归并；不再保留旧自研 Query Agent 的分类、证据评估和 LangGraph 测试。

更完整的设计说明见 [当前架构](docs/spec/architecture.md)。

知识主干的持续验证见[自动对照验证指南](docs/spec/knowledge-backbone-automation.md)：可配置题库，一条命令完成真实模型对照、源码评分初评和报告生成。
