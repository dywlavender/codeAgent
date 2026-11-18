# Business Code Agent

一个面向多仓库项目的业务代码问答工作台。在线问答统一使用 Pi Agent；CodeAgent 管理工程、资料授权、运行记录和源码引用核验。一次问答可以调查同一工程授权范围内的多个系统和仓库。

左侧导航把知识总览、系统导航、知识图谱、AST 结构和业务知识组成「知识中心」；代码问答、效果验证和工程设置在「其他」分组。当前重点是验证问答的完整性、准确性和调查成本，以及业务补充知识能否改善源码难以回答的问题。见 [Pi 质量基线](docs/records/2026-11/pi-quality-baseline-20261115.md)和[资料与知识方向](docs/spec/knowledge-backbone-direction.md)。

## 查询主链

```text
用户问题 → QueryService：工程、范围、工作区和运行记录
         → PiAgentRuntime：会话、上下文、工具调用和答案
         → CodeAgent：引用核验、Trace 和结果保存
```

Pi 自主选择 `read`、`grep`、`find`、`ls` 和 `codemode`，只在授权资料内调查。旧 LangChain、Direct 和 Claude Runtime 保留在显式评测或兼容代码中；旧环境变量不能切换在线问答。混合检索和契约工具不在 Pi 的工具清单内。

每轮可在“查看调用轨迹”中查看工具输入与结果、模型调用摘要、状态和错误，支持筛选与导出。归档位于工程数据目录 `query-traces/<runId>.json`，运行失败或取消也保留。Pi 的模型记录是摘要，不是完整请求快照。引用核验只确认本轮 `read` 交付的路径和行区间；模型的“目标覆盖”声明需要按原问题复核。

## 快速开始（部署）

### 前置条件

- Python 3.11+
- Node.js 20+
- Git（同步内网仓库时需要）
- 安装 Pi CLI，使 `pi --version` 可用；在 Pi 中配置支持工具调用的模型和供应商凭据。CodeAgent 的 Pi 选项见 `.env.example`
- Claude CLI 仅用于历史评测和业务投影的结构化导入

### 一键启动

在项目根目录运行。Windows 使用 Git Bash。

```bash
bash ./start.sh
```

根目录已有 `project.config.json` 时会自动读取，支持 UTF-8（含 BOM）。启动器默认创建或复用项目内的 Python 环境，同步并增量索引 Git 仓库，构建前端，然后启动 `http://127.0.0.1:8082/`。端口被占用时会停止监听该端口的旧进程再启动。重复启动不会复制源码。

可以用 `--python` 复用已有 Python 3.11+ 环境。macOS 或 Linux 传 `--python "/path/to/venv/bin/python"`，Windows 传 `--python "D:/venvs/code-agent/Scripts/python.exe"`。长期使用同一个环境时，在工程配置的 `startup` 中添加 `python`，值为解释器路径。相对路径以项目根目录为准，命令行参数优先。启动时会显示实际使用的 Python 版本和路径。

新机器的环境安装、项目包生成和首次部署请按[Windows 部署步骤](docs/spec/offline-deployment.md)从上到下执行；部署后的日常启动、参数和日志位置见 [Windows 启动与参数参考](docs/spec/windows-usage.md)。

### 项目配置

```bash
cp project.config.example.json project.config.json
```

至少配置项目、仓库和业务补充知识目录。`gitUrl` 必须是部署机可以访问的内网地址；不配置 `localPath` 时，仓库会放在 `repositoryRoot/<id>`。旧配置中的 `baselineRoot` 仍兼容。

```json
{
  "project": {"id": "loan-system", "name": "贷款核心系统"},
  "startup": {"database": ".data/knowledge.db", "port": 8082},
  "repositoryRoot": ".data/repositories",
  "knowledge": {"businessContextRoot": "knowledge/business-context"},
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

### Pi Runtime 配置

```dotenv
BUSINESS_CODE_PI_COMMAND=pi
BUSINESS_CODE_PI_MODEL=deepseek/gpt-6-luna
BUSINESS_CODE_PI_TIMEOUT=600
```

`BUSINESS_CODE_PI_MODEL` 留空时使用 Pi 自身的默认模型。网页可按工程从 Pi 模型列表选择，单轮请求可传 `model` 覆盖；实际模型记录在 Trace 中。`BUSINESS_CODE_MODEL_*` 供旧 Runtime 使用。

### 管理员认证

在工程配置中添加 `admin` 块即可启用管理员口令：

```json
"admin": {"name": "knowledge-admin", "apiTokenEnv": "BUSINESS_CODE_ADMIN_TOKEN"}
```

`apiTokenEnv` 指向保存口令的环境变量（写入 `.env`）。页面右下角「管理员入口」输入口令解锁后，受控操作在本浏览器会话内可用（按工程隔离）。受控操作包括：系统导航查看与发布、业务知识文档读写与 AI 修订建议、业务投影导入、AST 生成、原始资料上传、评测与检索诊断写入。未配置 `admin` 块时本机回环访问无需口令；服务绑定非回环地址时强制要求配置，否则启动失败。

### 多工程

平台支持登记并切换多个独立工程。侧栏「登记工程」按钮填写配置路径、展示名称和数据根目录；命令行等价于：

```bash
python -m business_code_agent.cli project-register --registry .data/projects \
  --config project.config.json --name 贷款核心系统
python -m business_code_agent.cli project-list --registry .data/projects
```

启动时传 `--project-registry .data/projects` 启用注册表模式。切换工程会同时切换问答历史、资料版本和管理员会话；不同工程的会话、资料版本和评测输入不互相续用。工程登记只创建边界和数据目录，不自动同步源码、索引或导入知识。

## 知识中心使用指南

| 页面 | 职责 | 权限 |
| --- | --- | --- |
| 知识总览 | 各类资料的完整性、更新状态和入口 | 只读 |
| 系统导航 | 维护各系统/应用的代码查找规则 | 管理员 |
| 知识图谱 | 浏览业务关系投影与契约调用链 | 只读 |
| AST 结构 | 符号定位、调用关系探索、索引管理 | 浏览只读 / 生成管理员 |
| 业务知识 | 维护业务 Markdown 原文与结构化投影 | 管理员 |
| 代码问答 | Pi 在线调查与答案引用核验 | 只读 |
| 效果验证 | 评测批次与检索诊断 | 浏览只读 / 写入管理员 |
| 工程设置 | 工程信息、资料目录、管理员会话 | 管理入口受控 |

### 知识总览

顶部集中展示待处理事项（业务投影待同步、AST 索引待更新，由资料状态汇总推导），下方是四类资料的状态卡和进入各模块的入口，右侧说明资料如何协作。原始资料支持上传、预览和下载；代码仓库与兼容资料（含 Code Map）的目录状态也在本页查看。

### 系统导航

按系统和应用维护 `SYSTEM.md` 与应用补充导航，告诉 Pi 如何定位各系统的代码入口。中栏区分「内容预览」和「编辑 Markdown」；「使用模板」填入模板，「AI 生成草稿」由 Pi 在授权仓库内调查后返回草稿（附读取的源码清单与调查轨迹）。草稿只在编辑区，点「发布导航」确认后才参与问答；保存时比较打开时的原文，冲突需重新打开合并。右栏常驻「实际生效的导航」，可按系统视角、应用视角和仓库视角预览，未发布草稿不参与问答。

### 知识图谱

默认展示「业务关系」投影：来自已导入业务知识的实体、流程、规则和调查入口，点击节点查看业务定义、相邻知识和原文来源。业务关系不等同于代码里的直接调用。

「接口调用链」是次级技术视图，读取显式准备的契约索引：

```bash
python scripts/evaluation/prepare_contract_index.py --project-config project.config.json
```

索引保存在工程数据目录 `contract-index/contracts.sqlite`，可增量更新，解析器升级时加 `--reparse`。登记工程可用 `--project-registry <注册表路径> --project-id <工程编号>`。未准备索引时页面会提示。调用链展示接口 → 服务方法 → HTTP 客户端 → 远端接口的已解析返回依赖调用，不代表完整运行链路；浏览不调用模型、不生成结构。

### AST 结构

「关系探索」是默认标签：按类名、方法名或全限定名搜索符号（单次最多 20 条），选中后向上下游逐层展开调用、继承和实现；已解析关系用实线，未解析调用和原始调用以虚线「线索」节点展示（没有连线不等于不存在调用）。点击节点或连线在右栏查看索引定位的源码片段；已解析边能指向调用位置，线索只能定位调用语句本身。

「索引管理」为次级标签：查看索引版本、符号与关系统计和覆盖仓库。AST 由管理员手动「生成 / 重新生成」，生成过程轮询进度；源码变化后只标记待更新，不自动重新生成。已解析关系数量较少时页面会显示覆盖提示。

### 业务知识

以 Markdown 原文为中心：左侧文档列表（可新建），中栏区分「阅读预览」和「编辑 Markdown」，右栏显示结构化投影状态。保存使用乐观并发检查，原文冲突返回 409。已保存文档可发起 AI 修订建议：Pi 只接收选定原文与补充材料（无工具），建议列出修改说明、依据和待业务确认事项，管理员确认后才写回原文。

「更新投影」把原文显式导入为结构化知识，供知识图谱展示：解析器可选 Claude Code 结构化（需要 Claude CLI）或 Markdown 规则解析（无需模型）。保存原文不会自动更新图谱；投影落后于原文时总览和图谱会提示。编写方式见[业务补充知识使用指南](docs/spec/business-baseline-guide.md)。

### 原始资料

需求文档、接口说明等原始文件放在 `requirementsRoot` 指向的目录，可在知识总览上传、预览和下载原件；PDF、Excel 等不可预览的格式提供下载。原始资料是被提出的目标，不等同于已实现。

### 效果验证与工程设置

效果验证管理评测批次、题库与检索诊断；浏览只读，创建与执行需要管理员。真实问答评测使用 `scripts/evaluation/evaluate_pi.py`，逐项复核用 `review` 命令生成 `checks.md`，语义正确性仍需人工阅读源码确认。工程设置确认当前工程、资料目录可读状态和管理员会话；切换工程在这里或侧栏下拉完成。

## 工作区

问答默认使用 `combined`，按需读取授权源码、README、需求、业务补充知识和 Code Map，并可调用手动生成的 AST 关系索引。AST 缺失或待更新时继续使用其他可用资料。`none`、`backbone` 和 `ast` 用于显式对照。

会话工作区的资料布局：

```text
<workspace-root>/pi/<conversationId>/
├── repos/<repo-id>/                → 授权仓库
├── requirements/                  → 需求原文
├── knowledge/business-context/     → 业务补充知识
├── knowledge/system-guides/        → 授权范围内的已发布系统导航
└── knowledge/generated-code-map/   → 自动代码地图
```

目录优先使用软链接；Windows 无法创建软链接时使用 Junction。资料总览可查看各来源的状态；刷新资料不会同步仓库或导入知识。

## API

登记工程使用 `/api/projects/<projectId>/...`；以下为兼容单工程的路径写法。

- `GET /api/workspace`：项目、工作区、仓库和索引摘要
- `GET /api/materials`：导航文档、业务原文、投影、AST 与原始资料的状态汇总
- `GET /api/runs`：最近运行
- `GET /api/conversations?limit=20&cursor=...`：按会话分页，返回 items 和 nextCursor
- `POST /api/query`：单次问答
- `POST /api/query/stream`：SSE 问答，返回运行状态、工具步骤和结果
- `GET /api/pi/models`：当前 Pi 可用模型列表
- `GET /api/conversations/{conversationId}`：按时间恢复完整多轮会话
- `GET /api/query/{runId}`：回答、事件、用量和反馈
- `POST /api/query/{runId}/cancel`：取消指定的运行，返回 cancelling 或已结束状态
- `POST /api/query/{runId}/feedback`：提交有帮助/需改进反馈
- `GET /api/ast`：AST 索引状态；`/api/ast/symbols...`：符号、关系与源码
- `GET /api/knowledge-graph?q=&type=`：业务关系投影；`/api/call-chains...`：契约调用链
- `/api/knowledge/*`：管理员知识维护、导入与浏览

问答输入：

```json
{"question":"中台发起提款前具体校验哪些条件？","conversationId":"可选"}
```

返回：

```json
{
  "runId": "RUN-...",
  "conversationId": "CONV-...",
  "runtime": "PI",
  "sessionId": "...",
  "status": "completed",
  "answer": "...",
  "events": []
}
```

使用 `conversationId` 追问时，Pi 会话保存在工程数据目录 `pi-sessions/`。工程、范围、资料模式或版本、模型和相关配置变化时重建会话；历史答案仍可查看。

### 对话页面与进度事件

左侧按会话归组，点击历史恢复该会话的全部问答；中间展示对话，底部输入框支持 Enter 发送、Shift + Enter 换行。中文输入法确认候选词不会触发发送。侧栏可收起，小屏使用抽屉式导航。

侧栏每页显示 20 个会话，点击“加载更多会话”继续浏览。同一浏览器标签页刷新后会自动恢复当前会话；若任务仍为 running/cancelling，页面每秒读取最新进度，保留停止按钮，完成后恢复输入。重新打开历史中的运行任务也按此方式跟踪，不会重复调用模型。

同一会话同时只允许一个运行中或取消中的任务。后端通过 SQLite 事务检查，重复请求返回 HTTP 409（SSE 返回 CONVERSATION_BUSY 错误），不会额外写入问答或启动模型。不同会话仍可独立运行。

单轮问答失败显示在对应回答位置；刷新列表、恢复进度或停止任务等操作失败显示在输入框上方，历史中的失败记录不会遮住新的操作错误。

调查过程默认折叠，点击状态行可展开工具步骤和返回内容。工具调用与结果使用同一个 ID 更新，不产生两条重复步骤；失败的工具会单独标识，不自动视为成功。回答支持 Markdown 标题、列表、表格和代码块复制。

最终回答以 SSE 的 `result` 为准。Pi 的内部思考内容不会写入公开轨迹。

SSE 的 `event` 消息使用以下持久化结构，`sequence` 在每次运行内递增：

| eventType | payload | 页面行为 |
| --- | --- | --- |
| status | phase、label | 更新当前阶段 |
| tool | id、name、input、status、output（可选） | 按 id 更新工具步骤 |
| text | id、text、mode（append / replace） | 按文本块追加或替换，避免完整消息重复显示 |
| error | error | 显示失败原因 |

SSE 的 `result` 消息保留完整回答、运行 ID、会话 ID、用量和事件。旧历史事件仍能浏览，无需迁移数据库。

点击“停止生成”会请求取消本轮 Pi 运行。已记录的事件保留，取消的运行状态为 `cancelled`。任务已经完成时，取消不会覆盖结果。启动阶段点击停止会等运行 ID 到达后立即发送请求。

SSE 首先发送 `run` 消息（runId、conversationId），供前端关联取消请求。单纯关闭页面或断开网络不会取消后台任务。

## 数据库

查询运行数据只有：

- `query_conversation`：运行时、会话和工作区
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

工程测试覆盖 Runtime、工作区、QueryService、事件归一化、前端 SSE 和知识中心页面交互。真实问答用 `scripts/evaluation/evaluate_pi.py` 固定模型与资料执行配对，结合原始 Trace 和逐项复核判断答案质量。

更完整的设计说明见 [当前架构](docs/spec/architecture.md)。

旧的知识主干自动评测入口见[自动对照验证指南](docs/spec/knowledge-backbone-automation.md)，其结果独立于 Pi 批次。
