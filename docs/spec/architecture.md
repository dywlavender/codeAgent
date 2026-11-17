# 问答运行时架构

本文描述在线问答与配套平台的现行行为。资料与证据原则见[项目整改指导方向](knowledge-backbone-direction.md)；运行时迁移记录见[迁移计划](agent-migration-plan.md)。

## 问答主链

```text
Web / API → ProjectContext → QueryService
  → RunContext（工程、范围、资料、工作区、会话）
  → PiAgentRuntime → Pi RPC：模型、只读工具、上下文、会话
  → 引用和目标覆盖记录 → Answer / SSE / Trace
```

CodeAgent 负责授权与工作区装配、运行互斥、取消、事件转发和持久化。Pi 决定查什么、何时停止，并管理上下文压缩。业务知识、评测、AST 和契约索引各自管理资料或观测结果，不另设全局调查循环。

正常 API 问答固定使用 `pi-v1`。旧 LangChain、Direct 和 Claude Runtime 保留给显式注入的历史评测；旧 Profile／Runtime 环境变量不切换在线入口，Pi 失败也不回退。Direct 的 Context Manager、Question Contract、Evidence Ledger 和 Answer Closure 没有进入 Pi。旧链的退出条件见[迁移计划](agent-migration-plan.md)。

## 包结构与依赖方向

后端按职责分包，依赖方向固定为 `api → 领域服务 → storage`，`shared/` 可被任何层引用：

| 包 | 职责 |
| --- | --- |
| `api/` | 唯一 HTTP 入口：服务器装配、路由分发、SSE |
| `query/` | 问答运行时、工作区准备、运行记录 |
| `knowledge/` | 业务补充知识、业务关系、调查入口与图谱 |
| `codeintel/` | 代码解析、索引、匹配、代码地图、跨系统调用边 |
| `requirement/` | 需求原文领域 |
| `evaluation/` | 效果验证与历史评测 |
| `platform/` | 工程配置与注册表 |
| `storage/` | 数据库结构与迁移 |
| `shared/` | 跨包共用工具 |

同包内使用相对 import；跨包使用绝对 import（`business_code_agent.<包>.<模块>`），使跨包依赖在代码中可见。领域包不反向 import `api`；`cli.py` 只做参数解析和装配，不承载业务逻辑。这些规则由 `tests/test_package_boundaries.py` 静态校验（禁止跨包相对 import、领域包反向引用 `api/`、HTTP 层直接依赖 `storage/`、`storage/` 与 `shared/` 依赖领域包），`tests/test_import_paths.py` 校验 import 目标文件存在；两者都不需要安装可选运行时依赖。

`query/agent/pi/` 封装 Pi RPC 子进程、事件转换、会话文件和只读检索扩展；正常在线问答固定进入 Pi，旧 Profile／Runtime 环境变量不再切换在线入口，旧 Runtime 仅由评测代码显式注入。Pi 工作区位于注册工程 `pi/<conversation-id>`（兼容单工程同理），不改动 Legacy 的 `modes/{combined,none,backbone,ast}` 布局。

`storage/connections.py` 提供不应用主库 schema 的通用 SQLite 连接工厂，供契约索引等独立快照库复用；独立快照不得走 `schema.connect()`，后者会创建并迁移主库表。HTTP 层不直接调用 `storage`：请求级主库连接由 `ProjectContext.open_db()` 提供。

`query/` 无需导入 `evaluation/` 与 `knowledge/`。`codeintel/` 独立于 `evaluation/`、`query/` 和 `requirement/`，依赖方向由 `tests/test_package_boundaries.py` 校验。`codeintel/ast/` 管理手动生成、版本与只读查询，旧 AST 导入保留兼容入口。调查锚点和需求文档解析分别由 `shared/entry_anchors.py`、`shared/docparse.py` 提供。

`codeintel/contracts/` 管理显式准备的工程契约索引：`java.py` 生成窄范围 Java 表达式与局部分支结构，`resolve.py` 绑定调用、HTTP 配置、返回字段与实体成员，`rules.py` 连接真实 setter 写入点与相应返回成员，`index.py` 保存 SQLite 快照并增量更新，`query.py` 在资料范围、当前原文与预算内交付证据包。解析仅出现在准备命令中，不从问答调用。`query/langchain_agent/contracts.py` 适配工具与本轮补查状态，`contract_answer.py`、`business_answer.py` 提供可选的结构事实、业务分支解读和条件化建议；代码解析领域层不依赖 LangChain。

根目录的 `start.sh` 是 macOS、Linux 与 Windows Git Bash 的共用启动入口。实现位于 `scripts/launcher/`，由选定的 Python 读取 UTF-8 工程配置并启动服务。打包脚本位于 `scripts/offline/`，评测脚本位于 `scripts/evaluation/`。

## Workspace

`WorkspaceManager.prepare_pi()` 在当前工程的工作区根下准备 `pi/<conversationId>/`。按模式和可用性挂载以下资料：

| 资料 | 工作区路径 | 用途 |
| --- | --- | --- |
| 授权源码和仓库 README | `repos/<repo-id>/` | 核实实现；README 提供项目背景 |
| 需求原文 | `requirements/` | 说明被提出的目标 |
| 业务补充知识 | `knowledge/business-context/` | 术语、业务约定、系统职责和例外 |
| 自动代码地图 | `knowledge/generated-code-map/` | 文件、符号和结构导航 |
| 系统代码导航 | `knowledge/system-guides/`（可选工作区副本） | 系统查找规则和应用补充，可按需读取 |

`combined` 是在线默认，按已准备的状态提供业务资料、Code Map 和 AST 关系工具。`none` 提供基础文件，`backbone` 额外提供业务补充，`ast` 额外要求可用的结构索引与工具。AST 作为有版本的工具资料记录在 Manifest 中。手动生成才会创建版本。

源码目录通过链接挂载。未指定范围时使用当前工程所有登记仓库；指定范围后，按配置展开系统与仓库 ID。未知 ID、空仓库集合和不可读取的必需仓库会在调用模型前失败。Pi 的只读检索扩展从授权真实根目录搜索，将结果映射回工作区路径，不用全局跟随链接。工作目录本身不是系统级隔离边界。

Pi 每轮接收 CodeAgent 生成的系统提示与实际来源清单。系统导航用配置中的应用映射与本轮 System Guide 片段拼装，可用于任一资料模式。工作区只保留当前范围的导航副本。资料正文按需读取，源码确认实现。工作区的 `CLAUDE.md` 和 `.langchain-traces/` 属于旧 Runtime。历史 Run 保留其资料模式和版本。

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

`PiAgentRuntime` 为每轮启动 Pi RPC 进程，核对 CLI、实际模型和只读搜索扩展。基础工具是 `read`、`grep`、`find`、`ls` 和 `codemode`。Pi 使用自身模型配置；`BUSINESS_CODE_PI_MODEL` 在 `.env.example` 中预设 `deepseek/gpt-6-luna`，留空时使用 Pi 默认模型，也可由单轮请求的 `model` 覆盖。`BUSINESS_CODE_PI_TIMEOUT` 默认 600 秒；用户可主动取消。

Pi 会话文件保存在工程数据目录 `pi-sessions/`。工程、范围、Profile、资料模式或版本、模型或相关 Pi 配置变化时重建会话。历史 LangChain 或 Claude 会话独立保留。

现行提示词和分层候选共用 Pi Runtime。分层候选把调查、证据和作答规则放在行为段，把当前工程、资料路径与 System Guide 放在工程上下文。工具描述由各工具提供。候选用 `evaluate_pi.py run --prompt-variants baseline,layered` 与现行提示词配对运行，提示词分组进入候选会话配置。

最终答案的临时绝对源码路径规范为工程相对路径。运行记录依据本轮 `read` 交付的原文核验引用路径和行区间，`grep` 命中只作导航。核验不判断结论是否被原文语义支持。复杂题的“目标覆盖”由模型逐项声明；每项标为已核实且至少有一条带行号引用，并且其带行号引用均通过核验时，才把该项计为 verified。全部自报项通过只说明这些声明有本轮交付依据，原问题的完整性仍需复核。

`QueryService` 从 Run 创建起持续保存 Trace，记录模型调用摘要、工具输入和输出、状态、用量与错误。归档保存在工程的 `query-traces/<runId>.json`，取消或失败也保留；Pi 模型摘要不作为完整请求快照。问答页可查看和导出 Trace。旧 Runtime 的程序补查、facts／contracts 收尾和 SearchCode 不参与这条链路。

Trace 的「系统提示词」显示 CodeAgent 提交的片段、Pi 在请求前报告的生效文本和启用工具说明，以及是否复用会话。运行中先展示已装配内容，结束时归档观测值。模型调用记录仍以摘要提供。

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

1. 解析工程和范围，创建或读取 Conversation，检查该会话是否已有运行。
2. 创建 Run、保存问题并开始 Trace；准备授权工作区及会话配置。
3. 调用 Pi，持续保存工具、模型和状态事件并通过 SSE 转发。
4. 保存回答、运行终态和用量。失败和取消分别记录，正常完成也不代表业务关系已查全。

`query_message` 服务前端历史与审计；Pi 自己管理可恢复的模型会话。

## API 与 SSE

登记工程使用 `/api/projects/<projectId>/...`，兼容单工程可用 `/api/...`。`POST /api/query` 返回一次性 JSON，`POST /api/query/stream` 返回：

```text
event: run     data: {"runId":"...", "conversationId":"...", ...}
event: event   data: {"eventType":"tool", ...}
event: result  data: {"runId":"...", "answer":"...", ...}
event: error   data: {"error":"..."}
```

`GET /api/pi/models` 返回可展示的 Pi 模型；`GET /api/query/<runId>/trace` 按工程与运行归属读取归档。关闭 SSE 连接不会自动取消后台任务。

## 代码索引边界

手动生成的 AST 版本仅包含 `index.db` 和 `metadata.json`。Pi 的 `find_code_symbol`、`get_code_symbol`、`find_code_relations` 及前端关系图共用 Reader。查询固定工程、当前版本与仓库范围。已变更的源码需重新生成索引。Pi 在 `combined` 中可降级使用文件工具，显式 `ast` 要求结构工具就绪。Code Map 继续以独立文件资料供全仓组合问答。

`GET /api/ast/symbols` 按名称定位符号，`/api/ast/symbols/<id>/relations` 展开一层关系，`/api/ast/symbols/<id>/source` 显示索引定位的当前源码片段。三个接口均支持 `versionId` 与仓库限制 `repositoryId`。关系查询支持 `direction` 和 `relationType`。

资料中心用 `GET /api/materials` 汇总当前工程的导航文档、业务原文、投影、AST 与原始资料。管理员可用 `/api/knowledge/system-guides` 浏览、编辑、生成草稿和预览生效内容，并将原始文件上传到需求资料目录。问答 Trace 保存本轮装配情况与实际调用和交付。见[阶段 6](agent-migration-plan.md#阶段-6资料中心与代码结构可视化)。

`codeintel/contracts/graph.py` 提供只读调用链投影，复用显式准备的 `contract-index/contracts.sqlite` 与工程源码根目录。`GET /api/projects/<projectId>/call-chains` 列出接口；`/<contractId>?version=...` 返回节点与定向边；`/<contractId>/evidence?version=...&node=...` 或 `&edge=...` 返回有行数上限的索引源码片段。版本变化拒绝旧引用，工程身份由 ProjectContext 与快照共同核对，不使用可变的全局工程。

投影从已有 RETURN_CALL／HTTP_RESPONSE 中提取主链并去重，不构建新的 AST。前端 `features/graph/CallChainView.jsx` 负责接口选择、横向图布局、缩放平移和源码抽屉；现有 GraphPage 的业务图改为独立标签保留。源文件更新时连接标为过期，原文面板说明是索引快照。图仅覆盖已抽取的返回依赖关系，完整调用图／控制流／副作用调用不在本次实现内。

`repository`、`code_file`、`code_symbol`、`code_fact`、`cross_application_edge` 继续服务同步、索引和浏览。Pi 文件工具直接读取授权源码；SearchCode 使用独立准备的混合检索索引，供旧链和独立诊断使用。结构命中提供导航，跨系统结论仍须核对源码连接。

`business_baseline_source`、`business_entity`、`business_relation_v2`、`business_entry_anchor` 继续服务知识维护。Pi 调查读取对应的授权 Markdown 文件，不查询这些表；文档中的入口只作为导航线索。

## 数据库迁移

打开数据库时：

- 旧 `query_agent_run` 会迁移为 `query_run`，历史运行标记为 `LEGACY_QUERY_AGENT`；
- 旧 `agent_run`、`query_agent_step`、`query_tool_call`、`query_checkpoint` 被删除；
- 旧 `query_feedback` 会重建为引用 `query_run` 的表；
- `functional_*`、旧 mapping、治理 proposal 表按既有迁移规则清除。

新库永远只创建 `query_run` 和 `query_event`，不会重新引入旧 Query Agent 表。

## 效果验证（评测子系统）

生产链问答质量使用 `scripts/evaluation/evaluate_pi.py`：它调用生产 `QueryService` 和 Pi Runtime，固定模型、题库和资料，保存答案、原始 Trace 与逐项复核。题库检查项只留在评测侧。

历史知识主干对照由 `business_code_agent/evaluation/` 实现，命令行脚本与评测页面共用同一执行逻辑，结果与 Pi 基线分开记录：

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

- 生产问答需要 Pi CLI 与可访问的模型；历史 Claude 工具需要独立配置；
- 主问答工具只读取和搜索本轮授权的文件，不能修改源码或执行 Bash；
- 业务补充知识、代码地图和混合索引可能过时，Agent 必须核对当前源码；
- 回答质量仍取决于模型、资料和调查过程，框架或索引存在不代表业务链路已经答对。
