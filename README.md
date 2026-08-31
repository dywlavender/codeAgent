# Business Code Agent

面向多仓库老项目的 Agent 知识工作台。核心是 Agent：少量稳定的业务知识和项目上下文用于理解问题、确定调查入口；入口之后的类、方法、调用链、HTTP/RPC、SQL 和代码证据都基于当前索引实时调查，不作为长期业务知识。

## 当前主链路

```text
自然语言业务基线
  ↓  模型结构化（未配置模型时使用保守解析）
System / BusinessTerm / Capability / Flow / Relation / Rule
  ↓
FLOW / CAPABILITY 的 Entry Anchor（可选）
  ↓
Runtime Agent 解析当前代码入口
  ↓
实时调查 Method / Call / HTTP / RPC / MQ / DB
  ↓
Runtime Evidence → Answer
```

长期保存、可重建索引和单次运行数据分开：

- Business Knowledge：业务是什么、为什么、有哪些关系；
- Project Context：系统、应用、仓库及代码归属；
- Entry Anchor：应用 + 页面/类/Job/Consumer 名称，仅作为调查起点。

代码索引（`code_file`、`code_symbol`、`code_fact`、`cross_application_edge`）可以重建；业务与代码之间不保存普通映射，Query Agent 只通过入口锚点定位起点，再基于当前索引实时调查。

技术拓扑同样单独保存。`software_system` / `application` 表示部署与代码归属，不会混入人工维护的业务 `SYSTEM` 知识。

## 一键启动

### macOS

```bash
chmod +x start-mac.sh
./start-mac.sh
```

### Windows

双击 `start-windows.bat`，或者在 PowerShell 中执行：

```powershell
.\scripts\start-windows.ps1
```

根目录存在 `project.config.json` 时会自动使用，不需要额外传 `--project-config`。启动器会准备 Python 环境、同步 Git 仓库、增量索引代码、构建前端并启动工作台。

### Windows / Linux 离线部署

应用通过压缩包交付、部署机可以访问 Python 包源和内网 Git 时，可生成 Windows/Linux 部署包：

```bash
# Linux / macOS
./build-offline.sh
```

```bat
rem Windows
build-offline.bat
```

一次执行会同时生成 Windows 和 Linux 两个 ZIP，且默认不绑定业务项目。目标机解压后复制 `project.config.example.json` 为 `project.config.json`，填写当前环境的内网 Git 地址，再运行 `start-offline-linux.sh` 或 `start-offline-windows.bat`。完整说明见 [Windows / Linux 离线部署方案](docs/offline-deployment.md)。

## 项目配置

复制示例：

```bash
cp project.config.example.json project.config.json
```

核心配置：

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
  "knowledge": {
    "baselineRoot": "knowledge/baseline"
  },
  "systems": [
    {"id": "channel", "name": "渠道系统"},
    {"id": "middle", "name": "贷款中台"}
  ],
  "repositories": [
    {
      "id": "withdraw-h5-repo",
      "gitUrl": "ssh://git@git.company.local/loan/withdraw-h5.git",
      "branch": "main"
    }
  ],
  "applications": [
    {
      "id": "withdraw-h5",
      "name": "提款 H5",
      "systemId": "channel",
      "repositoryId": "withdraw-h5-repo",
      "sourceRoot": ".",
      "type": "FRONTEND",
      "language": "typescript",
      "framework": "vue"
    }
  ]
}
```

`applications` 可以把一个仓库按 `sourceRoot` 拆成多个应用，也可以让多个仓库归属同一个系统。未配置 `systems/applications` 时保持兼容：每个仓库自动视为一个应用。完整说明与验收示例见 [多应用业务流使用指南](docs/multi-application-flow.md)。

私有仓库使用本机已有的 SSH Key 或 Git 凭据管理器，配置文件不保存账号密码。

## 模型配置

复制 `.env.example` 为 `.env`：

```dotenv
BUSINESS_CODE_MODEL_ENABLED=true
BUSINESS_CODE_MODEL_PROVIDER=openai
BUSINESS_CODE_MODEL_NAME=gpt-4.1-mini
BUSINESS_CODE_MODEL_API_KEY=your-key
BUSINESS_CODE_MODEL_TEMPERATURE=0
BUSINESS_CODE_MODEL_TIMEOUT=60
BUSINESS_CODE_MODEL_MAX_RETRIES=2
```

模型用于把自然语言业务基线转换为六类知识，以及增强问答理解。所有生成的业务知识必须引用基线原文。实体名称、别名、`codeHints`、流程步骤和其他属性值必须能在引用所在的 Markdown 小节中逐字找到，不能借用同文档其他功能段落的内容。关系两端及“触发、属于、依赖、产生、负责处理”等关系语义必须在同一个引用片段中明确出现。模型自行补充的检索提示会被过滤，定义的语义改写会回退到引用片段中的原句。

未配置模型时仍然可以：

- 同步和索引代码；
- 从明确的 Markdown 标题和原句安全提取知识；
- 搜索当前代码并从入口实时调查；
- 使用确定性 Query Agent 回答有证据的问题。

保守解析不会猜测业务规则或代码类名。

## 编写业务基线

在 `knowledge.baselineRoot` 下放 Markdown。业务人员可以正常写自然语言，不需要填写 JSON、YAML、类路径或数据库字段。

```markdown
# 提款业务基线

## 极优

极优是再担保类型产品，代码中一般用 JY 表示。

## 担保处理

极优提款成功后，需要进行担保后处理。

## 提款主流程

渠道提交提款申请，中台完成业务编排，核心完成放款，中台同步最终提款结果。
```

人工只补充无法稳定从代码判断的内容：

- 项目特有术语和别名；
- 系统职责边界；
- 核心业务能力；
- 粗粒度业务流程；
- 业务关系和规则。

不要求人工填写调用链、方法、SQL、表字段或完整技术流程。若业务人员知道稳定入口，可在流程/能力小节补一行入口锚点。

完整说明见 [业务基线使用指南](docs/business-baseline-guide.md)。

## 使用流程

1. 配置 Git 仓库和 `knowledge.baselineRoot`。
2. 启动工作台，等待代码同步和索引完成。
3. 把一份或多份自然语言 Markdown 放入基线目录。
4. 进入“业务知识维护”，点击“导入业务基线”。
5. 查看六类知识、原文来源、业务关系和调查入口。
6. 在问答页面提问；Agent 会优先解析入口，再基于当前代码实时调查。
7. 入口失效时，Agent 自动退化到当前代码的普通搜索，不修改业务知识。

命令行也可以执行：

```bash
python3 -m business_code_agent.cli baseline-refresh \
  --config project.config.json \
  --db .data/knowledge.db
```

不调用模型：

```bash
python3 -m business_code_agent.cli baseline-refresh \
  --config project.config.json \
  --db .data/knowledge.db \
  --no-model
```

未定位不是失败。问答会标记入口解析状态，并在当前代码索引中继续搜索；不会把不存在的类或方法写入知识。

## 业务入口锚点

FLOW/CAPABILITY 的 Markdown 小节可以补充入口列表：

```markdown
## 提款申请流程

用户在渠道发起提款，中台完成申请处理。

### 调查入口

- 提款 H5 | PAGE | WithdrawApply.vue
- 渠道服务 | CONTROLLER | ChannelWithdrawController
- 贷款中台 | CONTROLLER | MiddleWithdrawController
```

入口名称只允许页面名、类名、Job 或 Consumer 名；不能填写限定类名、方法签名、文件路径或行号。入口必须在同一小节原文中逐字出现。每次问答都会按 `application + entryName` 解析当前代码，状态为 `RESOLVED`、`MULTIPLE` 或 `NOT_FOUND`；解析结果不回写业务知识。

## 管理接口

- `POST /api/knowledge/baselines/refresh`：导入自然语言基线和入口锚点；
- `GET /api/knowledge/entities`：查询 System、Term、Capability、Flow 和 Rule；
- `GET /api/knowledge/entities/{id}`：查看知识、来源、关系和入口锚点；
- `GET /api/knowledge/entry-anchors`：查询入口锚点；
- `GET /api/knowledge/relations/{id}`：查看业务关系；
- `GET /api/knowledge-graph`：查看业务知识、业务关系、应用和入口投影；
- `POST /api/query`：执行问答。

管理员写操作可通过 `admin.apiTokenEnv` 配置的口令保护；读取接口保持只读。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

```bash
cd frontend
npm run build
```

内置 `examples/validation-project` 用于验证 Java/MyBatis 代码事实、自然语言业务基线和问答检索链路；`examples/multi-application-flow` 验证入口锚点驱动的多应用流程，从 H5 点击事件追踪到中台最终处理方法。

详细技术结构见 [当前架构](docs/architecture.md)。
