# Business Code Agent

面向 Java / MyBatis 老项目的 Agent 知识工作台。MVP 用少量自然语言业务基线告诉系统“业务是什么、什么和什么有关”，再把这些知识定向映射到当前代码；问答 Agent 先用业务知识缩小调查范围，最后回到代码和原文证据回答。

## MVP1 + MVP2 已实现的主链路

```text
自然语言业务基线
  ↓  模型结构化（未配置模型时使用保守解析）
System / BusinessTerm / Capability / Flow / Relation / Rule
  ↓
定向搜索当前代码
  ↓
独立 Business-Code Mapping
  ↓
Query Agent 检索业务知识、映射和代码证据
  ↓
MVP2 问答观察：从答案中的业务/代码证据生成 Mapping 候选
  ↓
管理员确认后写入正式 Mapping
```

三类数据始终分开保存：

- Business Knowledge：业务是什么、为什么、有哪些关系；
- Code Knowledge：类、方法、字段、API、任务、表和调用事实；
- Mapping：某条业务知识在代码中对应哪里。

重新索引代码只重建静态 Mapping，不会改写人工业务基线；问答发现的 Mapping 先进入独立观察记录，确认后才进入正式 Mapping。

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
  "repositories": [
    {
      "id": "loan-core",
      "gitUrl": "https://github.com/your-company/loan-core.git",
      "branch": "main"
    }
  ]
}
```

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
- 搜索代码候选并建立 Mapping；
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

不要求人工填写调用链、入口类、SQL、表字段或完整技术流程。

完整说明见 [业务基线使用指南](docs/business-baseline-guide.md)。

## 使用流程

1. 配置 Git 仓库和 `knowledge.baselineRoot`。
2. 启动工作台，等待代码同步和索引完成。
3. 把一份或多份自然语言 Markdown 放入基线目录。
4. 进入“业务知识维护”，点击“导入业务基线”。
5. 查看六类知识、原文来源和代码映射。
6. 对未定位或候选 Mapping 补充更明确的业务别名后重新导入。
7. 在问答页面提问；Agent 会先命中业务知识，再沿 Mapping 调查代码。
8. 若回答同时有充分的业务和代码证据，进入“业务知识维护”确认问答发现的 Mapping 候选。

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

查看和确认问答发现的候选也可以使用命令行：

```bash
python3 -m business_code_agent.cli mapping-observations --db .data/knowledge.db
python3 -m business_code_agent.cli mapping-review BMO-xxxxxxxxxxxxxxxx accept --db .data/knowledge.db --note "确认该入口属于此业务"
```

## Mapping 状态

- `VERIFIED`：代码证据明确且候选唯一；
- `CANDIDATE`：存在合理候选，但不能唯一确定；
- `UNRESOLVED`：当前代码索引中没有找到；
- `CONFLICTED`：不同来源或证据存在冲突；
- `DEPRECATED`：来源已经移除或知识不再有效。

未定位不是失败。问答 Agent 会明确说明当前没有找到对应实现，不会生成不存在的类或方法。

## MVP2：问答驱动的 Mapping 观察

第二阶段只做一件事：利用已经完成的问答证据，补充 Business-Code Mapping。它不会自动修改人工 Markdown，也不会把搜索候选直接当成事实。观察器只读取最终回答 `facts` 实际引用的业务/代码 Evidence ID，不读取未被回答采用的搜索候选。

触发条件是同一回答同时引用了：

- 已发布的业务实体或业务关系证据；
- 当前代码索引中的直接代码证据；
- 回答证据状态为“证据充分”。

系统会把候选写入 `business_code_mapping_observation`，状态为 `CANDIDATE`，并保存问题、业务对象、代码 Symbol、证据编号、可信度和生成原因。管理员在页面点击“确认”后，才会写入 `business_code_mapping`，来源标记为 `QUERY_REVIEW`；点击“忽略”只关闭该候选。

这样做的判断边界是：问答可以发现“这两个事实可能有关”，但不能替人确认业务语义。候选越多不代表知识越好，后续仍以确认率和问答准确率衡量效果。

## 管理接口

- `POST /api/knowledge/baselines/refresh`：导入自然语言基线并建立 Mapping；
- `POST /api/knowledge/mappings/rebuild`：不改业务知识，只重建代码 Mapping；
- `GET /api/knowledge/entities`：查询 System、Term、Capability、Flow 和 Rule；
- `GET /api/knowledge/entities/{id}`：查看知识、来源、关系和 Mapping；
- `GET /api/knowledge/relations/{id}`：查看业务关系；
- `GET /api/knowledge-graph`：查看业务知识、关系和代码映射投影；
- `GET /api/knowledge/mapping-observations?status=CANDIDATE`：查看问答发现的 Mapping 候选；
- `GET /api/knowledge/mapping-observations/{id}`：查看候选及证据；
- `POST /api/knowledge/mapping-observations/{id}/accept`：管理员确认候选；
- `POST /api/knowledge/mapping-observations/{id}/reject`：管理员忽略候选；
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

内置 `examples/validation-project` 用于验证 Java/MyBatis 代码事实、自然语言业务基线、独立 Mapping 和问答检索链路。

详细技术结构见 [当前架构](docs/architecture.md)。
