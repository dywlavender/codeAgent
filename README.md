# Business Code Agent

一个面向多仓库项目的业务代码问答工作台。在线问答统一使用 Pi Agent；CodeAgent 管理工程、资料授权、运行记录和源码引用核验。一次问答可以调查同一工程授权范围内的多个系统和仓库。

当前重点是验证问答的完整性、准确性和调查成本，以及业务补充知识能否改善源码难以回答的问题。见 [Pi 质量基线](docs/records/2026-11/pi-quality-baseline-20261115.md)和[资料与知识方向](docs/spec/knowledge-backbone-direction.md)。

## 查询主链

```text
用户问题 → QueryService：工程、范围、工作区和运行记录
         → PiAgentRuntime：会话、上下文、工具调用和答案
         → CodeAgent：引用核验、Trace 和结果保存
```

Pi 自主选择 `read`、`grep`、`find`、`ls` 和 `codemode`，只在授权资料内调查。旧 LangChain、Direct 和 Claude Runtime 保留在显式评测或兼容代码中；旧环境变量不能切换在线问答。混合检索和契约工具不在 Pi 的工具清单内。

每轮可在“查看调用轨迹”中查看工具输入与结果、模型调用摘要、状态和错误，支持筛选与导出。归档位于工程数据目录 `query-traces/<runId>.json`，运行失败或取消也保留。Pi 的模型记录是摘要，不是完整请求快照。引用核验只确认本轮 `read` 交付的路径和行区间；模型的“目标覆盖”声明需要按原问题复核。

## 代码结构浏览

“知识图谱 → 调用链”可直接浏览已准备索引中的接口 → 服务方法 → HTTP 客户端 → 远端接口，点击节点或连线查看源码依据，也可从远端接口继续展开。支持接口搜索、缩放和平移；原业务知识图保留在“业务关系”。浏览无需启用问答实验，不调用模型或生成 AST，当前显示的是已解析的返回依赖调用关系。

```bash
python scripts/evaluation/prepare_contract_index.py --project-config project.config.json
```

索引保存在当前工程数据目录的 `contract-index/contracts.sqlite`。可增量更新，解析器升级时可用 `--reparse` 重新解析。兼容单工程使用 `--project-config`，登记工程也可用 `--project-registry <注册表路径> --project-id <工程编号>`。

## 前置条件

- Python 3.11+
- Node.js 20+
- Git（同步内网仓库时需要）
- 安装 Pi CLI，使 `pi --version` 可用；在 Pi 中配置支持工具调用的模型和供应商凭据。CodeAgent 的 Pi 选项见 `.env.example`
- Claude CLI 仅用于需要它的历史评测和知识维护

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

## Pi Runtime 配置

```dotenv
BUSINESS_CODE_PI_COMMAND=pi
BUSINESS_CODE_PI_MODEL=deepseek/gpt-6-luna
BUSINESS_CODE_PI_TIMEOUT=600
```

`BUSINESS_CODE_PI_MODEL` 留空时使用 Pi 自身的默认模型。网页可按工程从 Pi 模型列表选择，单轮请求可传 `model` 覆盖；实际模型记录在 Trace 中。`BUSINESS_CODE_MODEL_*` 供旧 Runtime 使用。

## 工作区

问答默认使用 `combined`：挂载授权源码和 README、已有需求和业务补充知识、代码地图及可用的手动 AST。资料按需读取；AST 缺失或待更新时继续使用其他可用资料。`none`、`backbone` 和 `ast` 用于显式对照。启动或问答不会隐式生成 AST 或准备混合／契约索引。

会话工作区的资料布局：

```text
<workspace-root>/pi/<conversationId>/
├── repos/<repo-id>/                → 授权仓库
├── requirements/                  → 需求原文
├── knowledge/business-context/     → 业务补充知识
├── knowledge/generated-code-map/  → 自动代码地图
└── knowledge/ast/                  → 可用的手动 AST（组合模式）
```

目录优先使用软链接；Windows 无法创建软链接时使用 Junction。项目资料页可查看各来源的状态。刷新资料不会同步仓库或导入知识。

## 业务知识和需求

源码用于确认实现；需求原文记录目标；[业务补充知识](docs/spec/business-baseline-guide.md)记录源码难以推断的术语、职责和例外；代码地图与 AST 用于导航。管理员可在“业务知识维护”中管理业务文档和结构化关系。Pi 调查时直接读取授权文件。

## API

登记工程使用 `/api/projects/<projectId>/...`；以下为兼容单工程的路径写法。

- `GET /api/workspace`：项目、工作区、仓库和索引摘要
- `GET /api/runs`：最近运行
- `GET /api/conversations?limit=20&cursor=...`：按会话分页，返回 items 和 nextCursor
- `POST /api/query`：单次问答
- `POST /api/query/stream`：SSE 问答，返回运行状态、工具步骤和结果
- `GET /api/pi/models`：当前 Pi 可用模型列表
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

侧栏每页显示 20 个会话，点击“加载更多会话”继续浏览，不再受最近 30 次问答的限制。同一浏览器标签页刷新后会自动恢复当前会话；若任务仍为 running/cancelling，页面每秒读取最新进度，保留停止按钮，完成后恢复输入。重新打开历史中的运行任务也按此方式跟踪，不会重复调用模型。

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

工程测试覆盖 Runtime、工作区、QueryService、事件归一化和前端 SSE。真实问答用 `scripts/evaluation/evaluate_pi.py` 固定模型与资料执行配对，结合原始 Trace 和逐项复核判断答案质量。

更完整的设计说明见 [当前架构](docs/spec/architecture.md)。

旧的知识主干自动评测入口见[自动对照验证指南](docs/spec/knowledge-backbone-automation.md)，其结果独立于 Pi 批次。
