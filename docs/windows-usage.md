# Windows 使用方案

本页描述可访问 PyPI/npm 的普通启动方式。部署机不能访问公网、但能访问内网 Git 时，请使用 [Windows / Linux 离线部署方案](offline-deployment.md)。

## 先选一种模式

| 模式 | 适用场景 | 数据库内容 | 启动命令 |
|---|---|---|---|
| 配置项目验证 | 使用根目录 `project.config.json` | 当前为验证项目 | 双击 `start-windows.bat` |
| Demo | 显式体验内置通用示例 | 内置通用示例 | `start-windows.bat -Mode Demo` |
| Empty | 先建立空知识库，稍后手工导入 | 无示例数据 | `start-windows.bat -Mode Empty` |
| Repository | 直接索引本地 Java/MyBatis 项目 | 真实代码事实 | `start-windows.bat -Mode Repository -Repository "D:\code\loan-system"` |

没有项目配置时脚本默认创建 `.data\knowledge.db`，监听 `127.0.0.1:8082`，并打开浏览器；项目配置可以通过 `startup.database` 和 `startup.port` 覆盖这两个默认值。它不会修改被索引的代码仓库。

## 前置环境

- Windows 10 或 Windows 11。
- Python 3.11 或更高版本。安装时勾选 `Add Python to PATH`。
- Node.js 20 LTS 或更高版本，需包含 npm。
- 首次启动需要访问 PyPI 和 npm。之后可使用 `-SkipInstall -SkipFrontendBuild`。

不要求全局安装 Java、Maven、Neo4j 或 SQLite。Java 源码分析读取文件，不编译目标项目。

## 一键启动

### 双击体验

在资源管理器中双击：

```text
start-windows.bat
```

首次运行会执行：

1. 检查 Python 和 Node.js 版本。
2. 创建 `.venv-windows` 虚拟环境。
3. 安装 Python 包；Tree-sitter 未安装时使用内置 Java 解析器。
4. 用 `npm ci` 按锁文件安装并构建前端。
5. 按当前模式或 `project.config.json` 幂等初始化/同步知识库。
6. 启动工作台并打开配置中的地址；当前验证配置为 `http://127.0.0.1:8083/`。

关闭启动窗口或按 `Ctrl+C` 会停止服务。

每次执行启动脚本，发现目标端口已被占用时会直接停止占用进程，再启动工作台并复用原端口。

### 索引真实代码库

从项目根目录打开 `cmd.exe`：

```bat
start-windows.bat -Mode Repository ^
  -Repository "D:\IdeaProjects\loan-system" ^
  -RepositoryId "loan-system"
```

路径可以包含空格和中文。当前索引器支持 `.java` 和 MyBatis `.xml`，并自动跳过 `.git`、`.idea`、`target`、`build`、`.gradle`、`node_modules`。

### 使用 Git 配置自动同步

复制根目录的 `project.config.example.json` 为 `project.config.json`，填写仓库的 `gitUrl` 和 `branch`：

```bat
start-windows.bat
```

根目录存在 `project.config.json` 时会自动加载。首次运行自动克隆；以后先检查远端。本地已是最新版本时不会更新工作区，本地落后时才快进更新。同步完成后只增量索引变化源码。私有仓库复用 Windows Git Credential Manager 或 SSH Key。

当前仓库的 `project.config.json` 已配置内置验证项目、`.data\validation-project.db` 和 8083 端口。直接运行 `start-windows.bat` 即可验证配置驱动的同步；显式传入 `-Database` 或 `-Port` 时会覆盖配置文件值。切换真实项目时，将它替换为 `project.config.example.json` 的副本并填写实际 Git 地址。

Knowledge Update Agent 和 Query Agent 共用 `.env` 中的模型配置。推荐复制根目录 `.env.example` 为 `.env`，然后填写模型参数、模型密钥和管理员口令：

```powershell
Copy-Item .env.example .env
# 编辑 .env，填写 BUSINESS_CODE_MODEL_API_KEY 和 BUSINESS_CODE_ADMIN_TOKEN
.\start-windows.bat
```

也可以继续在当前 PowerShell 会话中直接设置同名 `BUSINESS_CODE_MODEL_*` 环境变量；如果 `.env` 存在，文件值优先。模型供应商、模型名、兼容接口地址和重试参数也都在 `.env` 中配置，不写入 JSON。项目和仓库配置示例见根目录 `project.config.example.json`。

### 使用空知识库

```bat
start-windows.bat -Mode Empty -Database ".data\company.db"
```

之后使用 `.venv-windows\Scripts\business-code-agent.exe` 索引本地代码：

```bat
.venv-windows\Scripts\business-code-agent.exe ingest-repo ^
  "D:\IdeaProjects\loan-system" ^
  --repository-id loan-system ^
  --db ".data\company.db"

```

把业务基线 Markdown 放入 `project.config.json` 中 `knowledge.baselineRoot` 指定的目录。启动工作台后进入“业务知识维护”，点击“导入业务基线”。

导入后再次运行：

```bat
start-windows.bat -Mode Empty -Database ".data\company.db" -SkipInstall -SkipFrontendBuild
```

`Empty` 只创建或迁移表，不会清空现有数据库。

## 常用参数

```text
-Mode Demo|Empty|Repository
-Database <SQLite 文件>
-Repository <Java 仓库目录>
-RepositoryId <稳定仓库 ID>
-ProjectConfig <项目 Git 配置 JSON>
-HostAddress 127.0.0.1
-Port 8082
-SkipInstall
-SkipFrontendBuild
-NoBrowser
```

端口冲突不需要额外参数，启动器会自动停止占用进程后重启。

默认只监听本机。只有明确需要局域网访问时才使用：

```bat
start-windows.bat -HostAddress 0.0.0.0
```

此时服务会要求 `admin.apiTokenEnv` 对应的环境变量已经设置；否则拒绝启动。还应配置 Windows 防火墙和数据库脱敏，不要直接暴露到互联网。

## 日常使用流程

### 代码事实分析

1. 用 Repository 模式索引代码。
2. 打开 Code，搜索字段、Symbol、表或列。
3. 在 Agent 中询问“字段在哪里生成、读取和校验”。
4. 代码证据不足时查看回答中的 Unknown，不把候选结果当结论。

### 业务知识闭环

1. 在 `knowledge.baselineRoot` 中编写少量业务基线 Markdown。
2. 打开“业务知识维护”，点击“导入业务基线”。
3. 检查调查入口是否已定位，多个候选或未找到时修正文档。
4. 查看业务实体、关系、原文证据和调查入口。
5. 打开“知识图谱”查看业务实体、关系、应用和入口。
6. Query Agent 使用这些内容制定检索计划，最终仍读取当前代码和业务文档。

## 停止、重启与升级

- 停止：在启动窗口按 `Ctrl+C`，或关闭该窗口。
- 快速重启：增加 `-SkipInstall -SkipFrontendBuild`。
- 代码更新或 `package-lock.json` 更新后：不要跳过安装和构建。
- 数据库会在启动时自动迁移；升级前仍建议复制 `.data\knowledge.db` 作为备份。

## 常见问题

### PowerShell 执行策略阻止脚本

请运行 `start-windows.bat`，它只对本次进程使用 `ExecutionPolicy Bypass`，不会修改系统全局策略。

### 找不到 Python

安装 Python 3.11+，勾选 PATH。脚本优先使用 Windows `py.exe`，其次使用 `python.exe`。

### npm 或 PyPI 安装失败

首次启动需要网络。如在公司代理下，先配置 npm/pip 代理或内部镜像，再重新运行。错误不会被吞掉，窗口会停留并显示原因。

### 端口已占用

端口被占用时启动器会自动停止占用进程；如果系统无法取得 PID 或进程拒绝结束，错误信息会提示手工处理。

### 工作台提示未构建

不要使用 `-SkipFrontendBuild`，让脚本执行 `npm ci` 和 `npm run build`。

### 真实项目能索引但业务原因为空

这是证据边界，不是启动故障。请确认问题匹配的功能文档已经登记入口类和关键表，并检查入口定位状态。

### 知识更新提示缺少模型凭据

检查根目录 `.env` 中的 `BUSINESS_CODE_MODEL_ENABLED` 和 `BUSINESS_CODE_MODEL_API_KEY`。业务基线导入默认需要模型；不使用模型时只能在命令行显式传入 `--parser markdown`，不会自动切换解析方式。

### 更新知识库要求管理员口令

检查 `project.config.json` 中的 `admin.apiTokenEnv`，在启动前设置对应环境变量。进入“业务知识维护”后输入同一口令解锁；口令仅保留在当前浏览器标签会话中。知识图谱查询是只读的，不需要管理员口令。
