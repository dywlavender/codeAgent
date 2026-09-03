# Windows 使用方案

Windows 启动器负责创建 Python 环境、构建前端、初始化 SQLite，并启动工作台。问答由本机 Claude Code CLI 执行；代码同步和知识维护仍由 Python 服务完成。

## 前置环境

- Windows 10/11
- Python 3.11+
- Node.js 20 LTS+（首次构建前端需要）
- Git（使用 `project.config.json` 同步仓库时需要）
- 已安装并完成认证的 Claude Code CLI，且 `claude` 在 PATH 中

首次启动需要访问 Python 包源和 npm；后续可使用 `-SkipInstall -SkipFrontendBuild`。

## 一键启动

直接双击：

```text
start-windows.bat
```

默认使用 `Empty` 模式，数据库为 `.data\knowledge.db`，监听 `127.0.0.1:8082`。如果根目录存在 `project.config.json`，双击启动时会自动读取项目配置，并同步其中的 Git 仓库。

启动器会按顺序执行：

1. 创建或复用 `.venv-windows`；
2. 安装后端依赖；
3. 执行 `npm ci` 和 `npm run build`；
4. 初始化数据库并按项目配置同步代码；
5. 启动工作台并打开浏览器。

端口被占用时，启动器会停止监听该端口的进程，然后重新启动。

## 使用项目 Git 配置

复制并编辑配置：

```powershell
Copy-Item project.config.example.json project.config.json
```

每个仓库填写部署机可以访问的内网地址：

```json
{
  "project": {"id": "loan-system", "name": "Loan System"},
  "repositoryRoot": ".data/repositories",
  "repositories": [
    {
      "id": "loan-core",
      "gitUrl": "ssh://git@git.company.local/loan/loan-core.git",
      "branch": "main"
    }
  ],
  "startup": {"database": ".data/knowledge.db", "port": 8082}
}
```

首次运行会克隆仓库；以后启动会先检查远端，已经是最新版本时不拉取，也不会重复索引未变化的文件。不要把密码写入 JSON，使用 SSH Key 或 Git Credential Manager。

如果仅验证本地代码，也可以不使用项目配置：

```powershell
start-windows.bat -Mode Repository -Repository "D:\IdeaProjects\loan-system" -RepositoryId loan-system
```

`Repository` 模式直接索引本地目录；`Empty` 模式只初始化或迁移数据库，不会清空已有数据。

## 模型和 Claude Code 配置

问答不再使用 Python LangChain Query Agent，而是调用 Claude Code CLI。确认命令行可用：

```powershell
claude --version
claude auth status
```

如 CLI 不在 PATH，可设置：

```powershell
$env:CLAUDE_CODE_COMMAND = "C:\Tools\Claude\claude.exe"
```

问答工作区只允许 Claude 使用 `Read`、`Glob`、`Grep`，不会开放编辑、写文件、Shell 或 Git 操作。工作区位于 `.data\agent-workspaces\<project-id>`，其中的仓库目录链接到实际同步目录。

业务基线导入仍可使用 `.env` 中的模型配置：

```powershell
Copy-Item .env.example .env
```

需要模型结构化时填写 `BUSINESS_CODE_MODEL_*`；不需要模型时，在管理页面选择 Markdown 规则解析，或使用：

```powershell
.venv-windows\Scripts\python.exe -m business_code_agent.cli baseline-refresh `
  --config project.config.json --db .data\knowledge.db --parser markdown
```

模型配置只影响业务基线结构化，不影响 Claude Code 问答运行时。

## 常用参数

```text
-Mode Empty|Repository
-Database <SQLite 文件>
-Repository <本地 Java 仓库目录>
-RepositoryId <稳定仓库 ID>
-ProjectConfig <项目配置 JSON>
-HostAddress 127.0.0.1
-Port 8082
-SkipInstall
-SkipFrontendBuild
-NoBrowser
```

例如：

```bat
start-windows.bat -ProjectConfig "D:\deploy\project.config.json" -Port 8083
start-windows.bat -SkipInstall -SkipFrontendBuild -NoBrowser
```

## 日常使用

1. 打开“Agent”，直接用自然语言提问；回答中的事件时间线可以展开查看 Claude 的检索过程。
2. 打开“业务知识维护”，导入或更新 `knowledge.baselineRoot` 下的 Markdown。
3. 代码更新后重新启动，启动器会增量同步和索引。
4. 需求原文放在 `requirements` 配置目录，问答阶段由 Claude 直接读取，不经过另一个 Requirement RAG 服务。

## 常见问题

### 提示找不到 Claude Code

安装 Claude Code、完成认证，并确认 `claude --version` 能执行；或设置 `CLAUDE_CODE_COMMAND` 为完整路径。

### 提示端口已占用

无需手工换端口。启动器会尝试结束占用进程后重启；如果 Windows 无法返回 PID，再手工关闭该进程。

### 项目同步失败

在 PowerShell 中用同一 Git URL 执行 `git ls-remote <gitUrl>`，先确认凭据、分支和网络可达。

### 页面空白或构建产物缺失

不要使用 `-SkipFrontendBuild`，让启动器重新执行 `npm ci` 和 `npm run build`。
