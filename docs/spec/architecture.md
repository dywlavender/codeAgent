# 问答运行时架构

本文记录当前实现。后续涉及知识主干、知识图谱和代码调查的整改，遵循 [项目整改指导方向](knowledge-backbone-direction.md)；本文中的现有实现边界不构成对后续技术方案的固定限制。

## 目标

生产主问答采用项目自有 LangChain 1.2 Agent。模型连接来自 `.env`，Agent 负责规划工具调用、阅读代码、追踪关系和组织答案；Python 提供工具、上下文管理、预算、资料范围和运行记录。原 Claude CLI 运行时保留为显式兼容选项及历史评测／知识维护能力。

```text
用户问题
   ↓
QueryService
   ├─ 获取工程 Conversation / Agent Session
   ├─ 准备只读 Workspace
   └─ 调用 AgentRuntime
          ↓
   LangChainRuntime（create_agent + 配置的兼容模型）
          ↓
   Read / Glob / Grep / 可选 SearchCode
   ┌───────────────┬───────────────┬───────────────┐
   │ business context │ code map │ requirements │ source repos │
   └──────────────────┴──────────┴─────────────┴──────────────┘
          ↓
   无工具整理答案（可选 facts：分层事实 → 程序检查 → 答案）
   （原始 Tool Event 与检查记录分别保留）
```

## Python 的边界

Python 负责：

- 项目配置、Git 同步和代码索引；
- 业务补充知识、业务关系和可选调查入口的管理页面；
- 工作区链接和固定 `CLAUDE.md`；
- Query Conversation、Run、Event、Feedback 持久化；
- LangChain 模型／工具循环、会话上下文、取消、调用和总时长预算；
- 原文引用、事实层次和已观察返回字段的一致性检查及答案组装；
- HTTP/SSE API 和前端展示。

主问答默认 `WORKFLOW=coordinated`：程序按问题目标安排定位、契约核对和补查，模型仍可选择工具和追加 Investigation。`coordinator.py` 根据每轮真实工具结果更新事项，明确文件直接读取，标识用 Grep，业务描述用 SearchCode／契约候选；相关入口选定后按需取得字段、分支及对应系统前端代码。自动动作去重，每批最多 3 个独立动作并发，不限制动作、入口或调查任务总数。主问答不设模型调用次数和调查总时长上限，也不设有限的 LangGraph 循环步数。事项区分原文已读、结构证据已交付及缺口，不能据此认定完整控制流或业务语义正确。basic/tracked 保留为显式对照。默认 `ANSWER_MODE=plain`，facts/contracts 收尾仍为实验能力；按答题模式在调查结束后整理答案。

`query/langchain_agent/` 的 `runtime.py` 负责调查与预算，`coordinator.py` 调度检索，`files.py`／`context.py` 管理范围内文件与会话，`investigation.py` 维护调查待办和补读，`trace.py` 保存公开快照；`evidence.py` 建立引用表，`answering.py` 定义最终输入，`verification.py` 校验可选事实协议并渲染答案。`query/navigation.py` 从配置生成职责导航，不解析业务源码。`codeintel/hybrid/` 实现显式结构分块／索引、局部 ONNX 向量与 Cross-Encoder、并发混合召回和上下文组装。索引位于当前工程 `data_root/retrieval-index`，本地模型由显式准备命令下载；问答和启动不会自动建索引。coordinated 默认 `RETRIEVAL=auto`、`CONTRACTS=auto`，仅发现已有索引时提供 SearchCode 与契约工具；显式 files/off 保留文件对照，hybrid/on 可显式要求对应能力。来源仍受当前工程和资料模式限制。

以上边界指生产问答。独立检索诊断由 `codeintel/retrieval.py` 提供词项基线和实验性上下文选择，`evaluation/retrieval.py` 冻结资料、复用候选并计量逐题覆盖增减，`retrieval_service.py` 管理工程内批次与本地对照，`fixed_context.py` 执行关闭工具的固定证据答题，`context_runtime.py` 可接入 `.env` 中的兼容模型接口；它们不接管主问答调查，也不生成 AST。接口和使用约定见[检索诊断](retrieval-evaluation.md)。

`codeintel/structure.py` 可只读复用工程已有代码索引的方法位置，经冻结原文、声明、结束位置和更新时间核对后，由显式操作保存成检索批次内的版本。它不调用解析器或更新索引，不创建 AST；方法之外和未通过核对的文件仍保留文本窗口。方法单元、整段保留及候选原文预算匹配仅作为独立实验能力，生产默认不变。

## 包结构与依赖方向

后端按职责分包，依赖方向固定为 `api → 领域服务 → storage`，`shared/` 可被任何层引用：

| 包 | 职责 |
| --- | --- |
| `api/` | 唯一 HTTP 入口：服务器装配、路由分发、SSE |
| `query/` | 问答运行时、工作区准备、运行记录 |
| `knowledge/` | 业务补充知识、业务关系、调查入口与图谱 |
| `codeintel/` | 代码解析、索引、匹配、代码地图、跨系统调用边 |
| `requirement/` | 需求原文领域 |
| `evaluation/` | 效果验证与 AST 结构资料 |
| `platform/` | 工程配置与注册表 |
| `storage/` | 数据库结构与迁移 |
| `shared/` | 跨包共用工具 |

同包内使用相对 import；跨包使用绝对 import（`business_code_agent.<包>.<模块>`），使跨包依赖在代码中可见。领域包不反向 import `api`；`cli.py` 只做参数解析和装配，不承载业务逻辑。这些规则由 `tests/test_package_boundaries.py` 静态校验（禁止跨包相对 import、领域包反向引用 `api/`、HTTP 层直接依赖 `storage/`、`storage/` 与 `shared/` 依赖领域包），`tests/test_import_paths.py` 校验 import 目标文件存在；两者都不需要安装可选运行时依赖。

`storage/connections.py` 提供不应用主库 schema 的通用 SQLite 连接工厂，供契约索引等独立快照库复用；独立快照不得走 `schema.connect()`，后者会创建并迁移主库表。HTTP 层不直接调用 `storage`：请求级主库连接由 `ProjectContext.open_db()` 提供。

为断开包级双向依赖，`query/` 不引用 `evaluation/` 与 `knowledge/`，`codeintel/` 不引用 `requirement/`；已断的边由 `tests/test_package_boundaries.py` 校验。只读 AST 资料投影位于 `platform/ast_material.py`，生成与源码新鲜度管理仍归 `evaluation/ast_service.py`，HTTP 组合根通过 `ast_material_factory` 注入完整服务；调查锚点解析与需求文档解析分别下沉到 `shared/entry_anchors.py`、`shared/docparse.py`，原领域模块保留兼容导出。

`codeintel/contracts/` 管理显式准备的工程契约索引：`java.py` 生成窄范围 Java 表达式与局部分支结构，`resolve.py` 绑定调用、HTTP 配置、返回字段与实体成员，`rules.py` 连接真实 setter 写入点与相应返回成员，`index.py` 保存 SQLite 快照并增量更新，`query.py` 在资料范围、当前原文与预算内交付证据包。解析仅出现在准备命令中，不从问答调用。`query/langchain_agent/contracts.py` 适配工具与本轮补查状态，`contract_answer.py`、`business_answer.py` 提供可选的结构事实、业务分支解读和条件化建议；代码解析领域层不依赖 LangChain。

根目录的 `start.sh` 是 macOS、Linux 与 Windows Git Bash 的共用启动入口。实现位于 `scripts/launcher/`，由选定的 Python 读取 UTF-8 工程配置并启动服务。打包脚本位于 `scripts/offline/`，评测脚本位于 `scripts/evaluation/`。

## Workspace

工作区由 `WorkspaceManager` 生成，默认位置为项目配置旁的 `.data/agent-workspaces/<project-id>`：

```text
<workspace>/
├── CLAUDE.md
├── .codeagent-project-navigation.json → 当前工程配置中的应用职责导航
├── .langchain-traces/ → 每轮公开模型输入、工具定义、响应与调查状态
├── knowledge/business-context/  → 项目特有的业务补充知识
├── knowledge/generated-code-map/ → 自动生成的结构导航（按模式挂载）
├── requirements/        → 配置的 requirementsRoot（可选）
└── repos/<repo-id>/     → 同步后的仓库目录
```

目录优先使用软链接；Windows 无法创建软链接时使用 Junction。链接始终指向已同步目录，不复制源代码。刷新工作区只重建链接和 `CLAUDE.md`，不会改动仓库。

`CLAUDE.md` 保留为工作区资料规则文件，LangChain 与兼容运行时均读取它；文件名不表示主问答依赖 Claude CLI。它只写资料位置、源码核对、导航边界和只读规则。

项目资料可提供简短的 `project-index.md`。Runtime 每轮只附加真实来源目录和短索引，不再把历史 `project-overview.md` 全文注入；业务补充知识和代码地图保留在资料目录中供模型按需搜索。默认使用轻量项目上下文；显式启用契约实验时额外提供只作导航的入口候选，由模型选择类型化工具核对，程序据具体未解析边生成补读任务。

主问答默认 `combined` 工作区，同时挂载正常项目资料（源码、README、需求等）、业务补充知识、已有自动代码地图和当前可用的手动 AST 版本。模型按问题需要检索和读取，页面只展示资料状态与仓库调查范围，不再要求互斥选择。AST 独立挂载到 `knowledge/ast/`，代码地图仍为 `knowledge/generated-code-map/`；两者均只用于导航。AST 未生成、待更新或暂不可用时不挂载旧结构，问答继续使用其余资料，页面说明状态。

每轮轨迹记录实际授权资料目录、AST 状态及采用版本。历史轮次只读取自身记录的 AST 版本，不从会话后续使用的版本补填；旧回答没有使用 AST 时仍保留空值。

历史兼容和显式对照仍支持 none/backbone/ast：分别为基础资料、基础资料加业务补充、基础资料加 AST（不含业务补充）。工作区位于注册工程的 `workspaces/<project-id>/modes/{combined,none,backbone,ast}`（兼容单工程仍使用 `agent-workspaces`），模型会话不跨资料模式或 AST 版本复用；旧答案与资料版本不被改写。

AST 由 `evaluation/ast_service.py` 管理。服务启动、读取评测就绪状态、进入 AST 页面、发起问答和启动实验都只读取状态，不生成 AST；只有用户在 AST 管理页点击生成或重新生成才会创建新版本。源码变化会将当前版本标为待更新，历史查询和已开始的实验仍引用它们各自记录的版本。

## 工程注册与隔离

兼容启动方式仍可用 `--db` 加 `--project-config` 运行单个工程。平台模式通过
`ProjectRegistry` 登记多个工程，并由 `ProjectContext` 为当前请求提供配置文件、数据库和资料目录：

```text
<platform-data>/
├── registry.json
└── projects/<project-id>/
    ├── project.json
    ├── knowledge.db
    ├── knowledge/business-context/
    ├── knowledge/generated-code-map/
    ├── ast/
    ├── evaluation-suites/
    ├── evaluations/
    ├── snapshots/
    ├── retrieval-index/
    ├── contract-index/
    ├── query-traces/
    └── workspaces/
```

每个注册工程使用独立数据库，因此会话、索引、业务知识、题库和评测批次不会因为切换配置而共用。请求可以使用规范路径
`/api/projects/<project-id>/...`；为兼容现有页面，也接受 `X-Project-Id` 或 `projectId`。注册工程生成的会话和运行编号带工程前缀，收到其他工程的会话编号会拒绝，不能把旧会话改写到当前工作区。

服务启动、工程切换、查询模式切换和实验启动都只读取 AST 状态。注册表登记只创建工程边界和空的托管目录，不会同步源码、索引、导入业务资料或生成 AST。业务补充知识和需求原文必须通过显式资料导入步骤进入工程目录；源码仓库仍按配置引用外部目录，源码同步和索引仍是后续独立任务。

登记不会覆盖已有工程资料，也不会自动迁移旧数据库、题库或评测记录；已有数据需要保留时，应显式指定原数据目录或执行资料迁移命令。导入后的业务补充知识和需求原文由工程目录托管，查询、知识维护和实验均使用该目录；源码仓库仍由配置明确指定。显式执行源码同步／索引后，可以根据索引生成 `knowledge/generated-code-map/`，但这不生成 AST 版本，也不包含人工业务解释；若多个工程共享同一外部仓库，应显式改为独立源码快照或接受共享。

资料迁移示例：

```text
python -m business_code_agent.cli project-import-materials \
  --registry .data/platform --project-id loan-system \
  --business-context-root knowledge/business-context --requirements-root requirements
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

以上命令仅适用于显式 `BUSINESS_CODE_AGENT_RUNTIME=claude`。默认 LangChain 使用配置的兼容 Chat 模型、Read／Glob／Grep 及已准备时的 SearchCode。会话消息保存在模式工作区 `.langchain-sessions/`，最多保留最近六个完整用户回合；旧工具正文超过上下文预算时标为已废弃，保留工具调用与结果的配对。源码修改后历史 Read 原文作废，历史 SearchCode 原文在追问时重新取得。模式、范围、AST 版本或运行时改变不续用旧会话。页面恢复历史时保留原轮次资料信息，新的追问使用 combined；浏览器旧 queryMode 设置不再限制新问题。

LangChain 主问答不设调查轮数或总时长上限，旧环境变量 `BUSINESS_CODE_AGENT_MAX_CALLS`／`BUSINESS_CODE_AGENT_TIMEOUT` 不再生效。只有有界评测等显式调用者可传 `max_calls`／`max_seconds`，其中调用总数包含最后一次无工具答题；轨迹记录实际限额，无限制时为 null。手动停止可中断异步模型请求。上下文字符数、单次原文大小及单次模型网络请求超时继续管理。无工具答题不携带调查工具定义或工具调用历史，仅组装真实原文与必要对话背景。facts/contracts 调查结束后执行独立收尾。plain 优先接收自然答案，遇到只剩证据缺口或重复检索时，进入下述无工具收尾。无工具答题未返回有效答案时记录真实错误，不再误标为预算用尽。检查与渲染本身不调用模型，格式不合格不会自动重试。所有模型调用用量累计，工具原文完整保存在事件中。事实检查的协议、接受事实和 `semanticProof=false` 保存在 usage.verification；检查通过不代表语义解释正确。会话核对应答模式，事件通过 SSE 与 query_event 保存，最终回答在模型完成后输出。

调查与收尾接收工程职责和本轮无命中、截断等状态。tracked 和 coordinated 提供 Investigation，明确的 next_read 由 before_model 执行，配对 Read 消息随下一次模型请求交付。程序补读事件标为 origin=workflow。supported 表示所列原文端点已读。关系需不同文件或符号的引用，consumer 需前端职责配置及对应代码。不可查项和预算结束时的开放项保留为未确认。

`BUSINESS_CODE_AGENT_CONTRACTS=on` 只读取当前工程已准备的 `contract-index`，与混合检索开关独立。契约编号只在工程／索引版本内有效，会话范围包含该版本。`TraceResponseFields` 交付受支持字段、展示映射、连接和完整原文引用；每包默认最多 16,000 字符／260 行，事实和所需证据整体接纳，源码变化或超出授权仓库则停止交付相关事实。历史契约工具正文在下一用户回合废弃。未解析项中的具体源码位置自动成为本轮 Read 任务；`source_read` 不是关系证明。

实验性 `ANSWER_MODE=contracts` 保留一次无工具收尾，模型选择已核对契约、最多六段业务原文，并可引用已交付的业务分支编号给出解释。程序生成返回字段与字符串 switch 映射，检查解释所依赖的返回字段和分支原文确已交付，再输出模型解读；来源通过不表示解释正确。建议采用复用字段、调整映射、补查新字段与核对消费者四种选项；字段已返回时不建议重复新增，映射缺少相关判别输入时指出限制，消费端未核对时不保证单点收益或排序。格式错误不重试，仍保留已核对事实。

问题中的状态／失败／重试／区分／条件／改动等显式词触发分支资料需求（规则覆盖有限，不冒称通用语义分类）。字段核对后 before_model 自动调用 `TraceBusinessRules`，不消耗模型请求；模型也可显式调用。单纯字段查询不自动展开。状态、接口、消费端与改动前提由程序记录本轮事项，不依赖可选 Investigation 的创建。未登记同系统前端时记录 `not_registered_in_scope`，不把其他系统前端列作消费者。局部写入不证明全部调用链、提前返回、最终持久化或实际执行。

包含 catch 的解释仅对该分支明确写入的成员形成结论，先前值不作为异常后的保证。消费者未登记、其它系统前端搜索无命中只记录为缺口，不能判定页面不存在，也不按无命中次数强制收尾。正常资料及 Read／Grep 范围保持工程授权边界。收尾版本变化会使契约实验的旧会话上下文失效，默认文件工具会话范围不受该版本字段影响。

收尾将重复原文按文件和行号合并，保留未交付的行号空缺；同一行存在冲突时不静默选一份。证据包中仅传位置元数据，原文在来源目录中交付一次；丢弃来源与冲突随审计保存。普通 `plain` 加契约工具仍可作对照。两种实验保留正常源码、README、需求和业务资料，不自动下载模型或生成 AST。索引结构升级需要用户显式执行准备命令的 `--reparse`。

usage.investigation 保存调查状态，usage.retrievalPlan 保存程序检索事项，usage.tracePath 指向工作区公开快照。`query/traces.py` 在 QueryService 创建运行时建立工程内 `query-traces/<runId>.json`，随模型和工具事件原子更新，失败或取消也保留，并由 usage.traceArchive 引用；工作区清理不会删除归档。快照包含每次系统／用户／工具消息、公开响应、工具定义、用量、耗时、调度原因与状态，不记录认证信息或私有思考；它属于供应商序列化之前的框架输入，不是 HTTP 抓包。`GET /api/projects/<projectId>/query/<runId>/trace` 校验工程与运行归属；无新归档时复用工程工作区已有快照，否则仅返回已有历史事件。问答页 TraceDrawer 支持运行中查看、分类筛选及完整 JSON 导出。执行 completed 不表示全部业务关系已经确认。

收到答案时，程序有待执行的补查才恢复调查。关键事项仍开放但已无具体动作时，丢弃草稿并用已读原文和缺口发起一次无工具答题。程序和模型发起的同参数定位调用共用本轮去重记录。只剩重复定位且没有程序补查时，也进入无工具答题。跳过的调用和收尾原因可在轨迹中查看。plain 的业务结论由模型根据证据组织，程序在末尾附上未确认事项。契约实验另由源码绑定的结构事实约束接口字段部分。

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

1. 主问答默认组合使用资料，创建或读取 Conversation，准备 combined 工作区；显式对照仍可指定旧模式。模式、调查范围或可用 AST 资料版本变化时清空可恢复的模型会话；
2. 创建 `running` 的 Query Run，并保存用户消息；
3. 调用 Runtime，实时保存并转发事件；
4. 成功后保存 Session、回答、用量和 assistant 消息；
5. 失败后将 Run 标记为 `failed`，保存错误事件，不生成 assistant 消息。

`query_message` 用于前端历史和审计。LangChain 的工具会话状态由工程内 SessionStore 提供；历史兼容模式使用 Claude Session。

## API 与 SSE

`POST /api/query` 返回一次性 JSON，`POST /api/query/stream` 返回：

```text
event: event   data: {"eventType":"tool_use", ...}
event: result  data: {"runId":"...", "answer":"...", ...}
event: error   data: {"error":"..."}
```

LangChain 事件使用现有 status／tool／text 格式；兼容模式保留 CLI 事件。前端展示实际调查进度、最终回答、历史和反馈。

## 代码索引边界

`codeintel/contracts/graph.py` 提供只读调用链投影，复用显式准备的 `contract-index/contracts.sqlite` 与工程源码根目录。`GET /api/projects/<projectId>/call-chains` 列出接口；`/<contractId>?version=...` 返回节点与定向边；`/<contractId>/evidence?version=...&node=...` 或 `&edge=...` 返回有行数上限的索引源码片段。版本变化拒绝旧引用，工程身份由 ProjectContext 与快照共同核对，不使用可变的全局工程。

投影从已有 RETURN_CALL／HTTP_RESPONSE 中提取主链并去重，不构建新的 AST。前端 `features/graph/CallChainView.jsx` 负责接口选择、横向图布局、缩放平移和源码抽屉；现有 GraphPage 的业务图改为独立标签保留。源文件更新时连接标为过期，原文面板说明是索引快照。图仅覆盖已抽取的返回依赖关系，完整调用图／控制流／副作用调用不在本次实现内。

`repository`、`code_file`、`code_symbol`、`code_fact`、`cross_application_edge` 继续服务同步、索引和浏览。主问答文件工具直接读源码；SearchCode 使用独立显式准备的混合检索索引，不调用旧 BusinessTools 或 EntryResolver。结构与引用是导航，跨系统结论仍须核对源码连接。

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
- CLI 入口保留：`scripts/evaluation/evaluate_backbone.py`（对照执行与 `--rejudge`）、`scripts/evaluation/backbone_report.py`（报告重生成）调用同一模块。结果目录兼容旧脚本读取。

## 运行限制

- 生产问答需要安装项目依赖并配置模型兼容接口；历史 CLI 能力另需安装认证 Claude Code；
- 主问答工具只读取和搜索本轮授权的文件，不能修改源码或执行 Bash；
- 工作区挂载文件资料；显式启用混合检索时，SearchCode 按当前授权范围提供原文；
- 业务补充知识、代码地图和混合索引可能过时，Agent 必须核对当前源码；
- 回答质量仍取决于模型、资料和调查过程，框架或索引存在不代表业务链路已经答对。
