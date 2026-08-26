# Business Code Intelligence Agent

一个面向 Java / MyBatis 老项目的 Agent 驱动知识工作台。普通用户通过只读 Query Agent 获取带证据的回答；管理员通过独立 Knowledge Update Agent 分析代码变化、需求、文档、人工说明和用户反馈，审核后发布可持续维护的业务功能知识。

它不是通用聊天机器人，也不会把搜索候选直接写成结论。每条确定事实都必须引用 Evidence；证据不足时输出 Unknown，来源冲突时输出 Conflict。

## Windows 一键启动

### 环境要求

- Windows 10 / 11
- Python 3.11+
- Node.js 20+
- 首次启动可访问 PyPI 和 npm

下载代码后，直接双击：

```text
start-windows.bat
```

脚本会自动创建 Python 虚拟环境、安装依赖、构建前端，并按当前模式或 `project.config.json` 初始化/同步知识库、启动服务并打开浏览器：

```text
http://127.0.0.1:8083/
```

首次验证内置项目时，不需要填写任何项目路径；`project.config.json` 已提供默认项目。

### 一键索引真实项目

在项目根目录打开 `cmd.exe`：

```bat
start-windows.bat -Mode Repository ^
  -Repository "D:\IdeaProjects\loan-system" ^
  -RepositoryId "loan-system"
```

代码路径支持空格和中文。应用只读取源码，不修改被索引仓库。

持续维护的项目建议改用 Git 配置：

```bat
start-windows.bat
```

根目录存在 `project.config.json` 时会自动使用。首次启动自动克隆；以后先检查远端，本地已经是最新版本时不会更新工作区，落后时才快进更新并增量索引。其他配置文件可通过 `-ProjectConfig` 指定。

### 快速重启

首次成功运行后，可跳过安装和前端构建：

```bat
start-windows.bat -SkipInstall -SkipFrontendBuild
```

完整参数、空库模式、需求和业务知识导入、代理配置及排错见 [Windows 使用方案](docs/windows-usage.md)。

## macOS 一键启动

环境要求：macOS、Python 3.11+、Node.js 20+。首次启动需要能够访问 PyPI 和 npm。

在终端进入项目目录后执行：

```bash
chmod +x start-mac.sh
./start-mac.sh
```

脚本会自动创建 `.venv`、安装后端、构建前端，并按当前模式或 `project.config.json` 初始化/同步知识库、启动服务并打开：

```text
http://127.0.0.1:8083/
```

索引真实 Java / MyBatis 项目：

```bash
./start-mac.sh --mode Repository \
  --repository "/Users/your-name/IdeaProjects/loan-system" \
  --repository-id "loan-system"
```

推荐通过 Git 配置自动同步：

```bash
cp project.config.example.json project.config.json
# 修改 project.config.json 中的 gitUrl、branch 和项目名称
./start-mac.sh
```

根目录存在 `project.config.json` 时，Mac 启动脚本会默认加载它。只有配置文件放在其他位置时才需要 `--project-config`。

当前仓库的 `project.config.json` 已预置内置验证项目、`.data/validation-project.db` 和 8083 端口，直接运行 `./start-mac.sh` 就会使用这些配置自动同步并启动。命令行显式传入 `--database` 或 `--port` 时会覆盖配置文件值。验证完成后，再用 `project.config.example.json` 覆盖它并填写真实 Git 地址即可切换项目。

首次成功运行后的快速重启：

```bash
./start-mac.sh --skip-install --skip-frontend-build
```

查看全部参数：

```bash
./start-mac.sh --help
```

## 三种使用方案

| 方案 | 命令 | 用途 |
|---|---|---|
| 配置项目验证 | 双击 `start-windows.bat` | 按 `project.config.json` 验证项目同步、Evidence 和 Run 回放 |
| 空知识库 | `start-windows.bat -Mode Empty` | 从零建立自己的知识库 |
| 真实代码 | `start-windows.bat -Mode Repository -Repository "D:\code\project"` | 自动索引 Java / MyBatis 事实 |

未配置 `startup.database` 时默认数据库是 `.data\knowledge.db`；当前 `project.config.json` 配置为 `.data\validation-project.db`。初始化是幂等的，不会在重启时清空 Runs 或现有数据。

## 内置验证项目

仓库内提供了一个独立的 [Loan Lifecycle Validation Project](examples/validation-project/README.md)，专门验证字段生命周期、MyBatis 读写、定时任务、需求证据和 Git 变更后的知识更新。它不连接真实数据库，也不依赖公司代码。

```bash
business-code-agent init-db --db .data/validation-project.db
business-code-agent ingest-repo examples/validation-project --db .data/validation-project.db --repository-id validation-project
business-code-agent requirement-import examples/validation-project/docs/requirements/REQ-VAL-001.md --id REQ-VAL-001 --db .data/validation-project.db
```

验证项目自身有 `v1.0-rule-a-only`、`v1.1-allow-b` 和 `v1.2-batch-scope` 三个业务变更标签，完整验收步骤见其 README。

## Git 项目配置

对于持续维护的老项目，建议配置 Git 地址，不再手工维护本地项目路径：

```json
{
  "project": {
    "id": "loan-system",
    "name": "贷款核心系统"
  },
  "startup": {
    "database": ".data/knowledge.db",
    "port": 8082
  },
  "repositoryRoot": ".data/repositories",
  "repositories": [
    {
      "id": "loan-core",
      "gitUrl": "https://github.com/your-company/loan-core.git",
      "branch": "main"
    },
    {
      "id": "loan-job",
      "gitUrl": "https://github.com/your-company/loan-job.git",
      "branch": "develop"
    }
  ]
}
```

| 字段 | 含义 |
|---|---|
| `project.id` | 项目稳定标识 |
| `project.name` | 页面展示名称 |
| `startup.database` | 启动时使用的默认 SQLite 路径，可被命令行参数覆盖 |
| `startup.port` | 启动时使用的默认端口，可被命令行参数覆盖 |
| `repositoryRoot` | 自动管理的仓库存放目录，相对配置文件解析 |
| `repositories[].id` | 仓库稳定标识，也用于代码索引 |
| `repositories[].gitUrl` | HTTPS 或 SSH Git 地址 |
| `repositories[].branch` | 跟踪的远端分支 |
| `repositories[].localPath` | 可选，为单个仓库指定本地目录 |

同步行为：

- 本地目录不存在时自动克隆。
- 启动时先执行远端 `fetch`，以判断远端是否有变化。
- 本地与远端一致时返回 `UP_TO_DATE`，不更新工作区。
- 本地落后时执行快进更新，返回 `UPDATED`。
- 本地存在未提交修改、未推送提交或分支分叉时停止，让使用者先处理，不覆盖本地代码。
- 同步后运行增量索引，没有变化的源码不会重复解析。

验证项目也支持相对本地 Git 地址，例如 `examples/validation-project`。启动器会以配置文件所在目录解析该路径，并在 `.data/repositories` 下建立受管理的克隆；后续启动会正常返回 `UP_TO_DATE`。

只同步并索引，不启动网页：

```bash
business-code-agent sync-project \
  --config project.config.json \
  --db .data/knowledge.db
```

私有仓库复用本机已有的 SSH Key 或 Git 凭据管理器，配置文件不保存账号密码。完整示例见 `project.config.example.json`。

## LangChain 1.2 模型配置

Knowledge Update Agent 和用户端 Query Agent 都使用 LangChain 1.2 的 `create_agent`、`init_chat_model` 和结构化输出接口。模型配置统一放在根目录 `.env`，Git 项目配置 `project.config.json` 负责项目、仓库、启动默认值和管理员入口。

旧版本 `project.config.json` 中的 `model` / `queryModel` 仍可作为兼容回退，但只要 `.env` 中出现 `BUSINESS_CODE_MODEL_*` 配置，就完全以 `.env` 为准。

密钥和模型参数都不写入 `project.config.json`。项目支持根目录 `.env` 文件；CLI 启动时会自动加载，且 `.env` 优先于当前进程中的同名环境变量。先复制示例文件：

```bash
cp .env.example .env
# 编辑 .env，填写模型参数、BUSINESS_CODE_MODEL_API_KEY 和 BUSINESS_CODE_ADMIN_TOKEN
```

macOS：

```bash
./start-mac.sh
```

Windows PowerShell：

```powershell
.\start-windows.bat
```

也可以不使用 `.env`，直接在启动脚本所在的终端设置同名环境变量，但启动时如果存在 `.env`，文件中的值会覆盖终端中的同名值。示例配置启用了管理员认证，因此 `BUSINESS_CODE_ADMIN_TOKEN` 也需要填写。

`.env` 中的 `BUSINESS_CODE_MODEL_PROVIDER`、`BUSINESS_CODE_MODEL_NAME`、`BUSINESS_CODE_MODEL_BASE_URL`、`BUSINESS_CODE_MODEL_TEMPERATURE`、`BUSINESS_CODE_MODEL_TIMEOUT` 和 `BUSINESS_CODE_MODEL_MAX_RETRIES` 分别控制供应商、模型、兼容接口地址、温度、超时和重试次数。日志级别通过 `BUSINESS_CODE_LOG_LEVEL` 控制（默认 `INFO`，设为 `DEBUG` 可查看 HTTP 访问与检索细节），输出遵循 Python 标准 logging 格式：`时间 级别 模块: 消息`。`openai` 同时支持通过 `BASE_URL` 连接兼容接口；使用其他模型供应商时修改 provider、name，并安装对应的 LangChain 集成包。未配置模型或设置 `BUSINESS_CODE_MODEL_ENABLED=false` 时，Knowledge Update Agent 和 Query Agent 都会降级到确定性流程；Query Agent 会标记 `FALLBACK`，不会把规则拼装伪装成模型归纳。

如果 `BUSINESS_CODE_MODEL_ENABLED=true` 但 `BUSINESS_CODE_MODEL_API_KEY` 没有填写，服务启动时会输出明确警告，并继续使用 `FALLBACK`，不会把确定性结果误标为模型归纳。

Query Agent 的模型只负责问题理解、追问上下文消解和自然语言归纳，并只能调用代码事实、业务功能和需求摘要三个只读工具。候选范围、Evidence 读取、冲突判断、Unknown 和回答引用仍由后端确定性层裁决。

管理员口令由同一配置中的 `admin.apiTokenEnv` 指定。浏览器只在当前标签会话保存口令，普通问答接口不需要它。默认仅监听本机；如果监听非本机地址，服务会强制要求管理员口令已经配置并存在于环境变量中。

## 推荐落地流程

### 只分析代码

适合先验证工具是否能理解真实仓库：

1. 使用 Repository 模式启动。
2. 在 Code 页面搜索字段、Symbol、表或列。
3. 在 Agent 中询问：“`repayType` 在哪里生成、读取和校验？”
4. 查看右侧 Code Evidence 和底部 Run。

只有代码时，可以回答“当前怎么实现”，不能可靠回答“为什么这样设计”。这不是缺陷隐藏，而是证据边界。

### 增强业务解释

在“知识生成”中选择当前代码、需求依据并补充一段业务说明，生成待审核草稿。管理员查看结构化内容和来源证据后接受或驳回；只有接受后才生成可供 Query Agent 使用的已发布功能知识版本。

### 增强需求依据

导入 `.docx`、`.md`、`.txt` 或兼容 JSON：

```bat
.venv-windows\Scripts\business-code-agent.exe requirement-import ^
  "D:\documents\REQ-2026-018.docx" ^
  --id REQ-2026-018 ^
  --db ".data\knowledge.db"

.venv-windows\Scripts\business-code-agent.exe requirement-enrich ^
  REQ-2026-018 ^
  --db ".data\knowledge.db"
```

系统默认使用 Requirement Digest，必要时才读取命中的原文 Chunk。相同 ID 的新文档会生成新版本和变化提案，不自动覆盖人工确认的业务知识。

## Agent 工作台

功能知识正文采用 [功能知识正文模板](docs/functional-knowledge-template.md)。正文面向业务人员阅读，Evidence、来源版本和审核状态由系统自动维护。

六个一级入口：

- **Agent**：最近问题、结构化回答、三源 Evidence 和 Run Drawer。
- **知识生成**：以当前代码、需求依据和业务补充为输入，生成可审核的功能知识草稿；审核通过后才发布。
- **知识图谱**：按功能、需求、代码、标签和业务补充查询关系，并在节点详情中查看来源证据。
- **Code Fact**：Symbol、字段活动、表列事实和源码定位。
- **Requirements**：Digest、业务规则、代码关系、原文 Chunk 和版本变化。
- **Runs**：步骤、Tool Call、证据缺口与运行结果回放，不展示隐藏思维链。

知识生成页面默认展示三类输入卡片：当前代码、需求依据和业务补充。管理员不需要手工填写来源类型或 Evidence ID；系统会自动装配上下文。生成后先查看“知识草稿”和“来源证据”，再执行接受、驳回或暂缓。知识图谱页面提供搜索框、类型筛选、关系视图和节点检查器；点击节点可以查看相邻知识、标签、版本和证据数量。

## 知识更新闭环

```text
代码变化 / 需求 / 文档 / 人工说明 / 用户反馈
                     ↓
Knowledge Update Agent 识别受影响功能并生成结构化差异
                     ↓
管理员接受 / 驳回 / 暂缓
                     ↓
接受后发布新的不可变功能知识版本
                     ↓
Query Agent 只检索已发布版本
```

业务功能知识只保存功能摘要、场景、核心规则、入口和业务级数据影响；详细代码、SQL 和原始文档通过 Evidence 引用按需读取，避免知识正文无限膨胀。代码事实可自动增量刷新，但业务语义永远不会由模型直接发布。

在知识生成中，管理员只需要选择当前代码、已导入的需求，并补充一段业务说明；系统会自动组装 Code Fact、Requirement Digest 和人工 Evidence，生成待审核草稿。知识标签由 Agent 从三类材料中归纳，管理员在草稿中确认后才会成为图谱连接锚点。代码变化和需求版本变化仍会自动进入待处理列表，无法确认的内容保留为待确认项。

回答固定拆为：

- 结论
- 业务链路
- 关键规则
- 代码实现
- 需求依据
- 冲突与未确认项

右侧 Evidence 只显示答案实际引用的证据，不混入候选结果。历史 Run 会按答案中的 Evidence ID 从权威 Evidence 表重建精确证据行。

## CLI 参考

如果不使用 Windows 脚本，也可以手工安装：

```bash
python -m venv .venv
python -m pip install -e ".[tree-sitter]"
cd frontend
npm ci
npm run build
cd ..
```

初始化和启动：

```bash
business-code-agent init-demo --db knowledge.db
business-code-agent init-db --db empty.db
business-code-agent serve-query --db knowledge.db --port 8082
```

代码分析：

```bash
business-code-agent analyze-repo /path/to/java-repository
business-code-agent ingest-repo /path/to/repository --repository-id repo-main --db knowledge.db
business-code-agent discover --db knowledge.db
business-code-agent explain repayType --db knowledge.db
```

查询和回放：

```bash
business-code-agent query "提款为什么要校验 repayType？" --db knowledge.db
business-code-agent query-run QRUN_ID --db knowledge.db
```

## HTTP API

工作台与 Query API 同源运行在 8082：

- `POST /api/query`
- `GET /api/query/{runId}`
- `POST /api/query/{runId}/feedback`
- `GET /api/runs`
- `GET /api/workspace`
- `GET /api/code/search?q=`
- `GET /api/functions?q=`
- `GET /api/requirements?q=`
- `GET /api/knowledge-admin/pending`
- `GET /api/knowledge-admin/functions`
- `GET /api/knowledge-admin/proposals`
- `GET /api/knowledge-admin/proposals/{id}`
- `POST /api/knowledge-admin/generate`
- `POST /api/knowledge-admin/proposals/{id}/review`
- `GET /api/knowledge-graph?q=&type=`

知识写入统一走带管理员口令的知识生成接口；用户侧 `/api/functions` 和 `/api/knowledge-graph` 只读取已发布知识、当前 Code Fact 和需求关联，不直接修改知识。

## 核心约束

- 单 `BusinessCodeQueryAgent`；Code、Business、Requirement 是工具和知识源，不是三个 Agent。
- 固定支持 `BUSINESS_LOGIC / DATA_TRACE / RULE_REASON / CROSS_PROCESS`。
- 三源初搜后最多扩展三轮 Evidence。
- 检索候选不能进入确定事实。
- 回答固定区分 Fact、Inference、Unknown 和 Conflict。
- checkpoint 只持久化引用和结构化事实，不保存源码、整份需求原文或隐藏思维链。
- Tree-sitter 是可选增强；不可安装时回退到保守 Java 解析器。
- SQLite 保存事实、状态和 provenance，不要求 Neo4j。

## 验证

```bash
python -m unittest discover -s tests -v
business-code-agent query-validate
```

测试覆盖新版功能知识状态机、知识更新 Agent、需求关联和 Query Agent。真实验收仍需要目标组织提供脱敏代码、需求版本、已确认业务事实和权威问题集。

## 安全说明

- 默认只监听 `127.0.0.1`。
- 普通问答和知识图谱查询保持只读；知识生成接口使用管理员口令。当前未提供完整的用户账号体系，不应直接暴露到互联网。
- API 不向浏览器返回仓库本地绝对路径，也不伪造未采集的 Git branch/commit。
- 使用 `0.0.0.0` 开放局域网访问时会强制要求管理员口令；仍应增加反向代理认证、TLS、防火墙规则和数据脱敏。

## 项目目录

- `business_code_agent/`：索引、知识构建、Evidence Loop、API 和 CLI。
- `business_code_agent/query_agent/`：三源 Query Agent、回答、冲突、可观测性和验证。
- `frontend/`：React + Vite Agent 工作台。
- `frontend/src/design-tokens.css`：primitive → semantic → component 三层设计令牌；统一字体、状态色、间距和控件契约。
- `.env.example`：Knowledge Update Agent 和 Query Agent 的模型配置模板；实际 `.env` 不提交到版本库。
- `scripts/start-windows.ps1`：Windows 启动主逻辑。
- `start-windows.bat`：双击入口。
- `docs/windows-usage.md`：Windows 使用和排错方案。
- `docs/frontend-design-system.md`：前端信息架构、字体、Phosphor 图标和组件规范。
- `examples/`：通用可重复夹具。
- `tests/`：M1-M4 回归测试。

每次执行启动脚本时，如果配置中的端口已经是本项目的工作台服务，脚本会先停止旧进程，再启动新进程，避免旧代码或旧配置继续生效。如果端口被其他程序占用，脚本仍会报错并建议更换端口。
