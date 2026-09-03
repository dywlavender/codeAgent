# Windows / Linux 内网部署

这里的“离线包”指应用本体通过压缩包交付。部署机可以访问公司的 Python 包源、内网 Git 和 Claude Code 所需的内网服务；应用不依赖公网。

## 部署边界

| 内容 | 构建机 | 部署机 |
|---|---|---|
| Python 依赖 | 生成依赖清单；也可选择打包 wheel | 按清单从内网 Python 源安装，或使用包内 wheel |
| 前端 | 执行 npm 构建 | 直接使用 `frontend/dist`，不需要 Node.js |
| 业务代码 | 默认不打包 | 用 `project.config.json` 从内网 Git 克隆或增量更新 |
| SQLite | 不预置业务数据 | 启动时创建或迁移 |
| Claude Code | 不打包 CLI 和登录态 | 部署机安装、认证并配置 `claude` |

因此，正常内网部署不需要把 Git 地址写入构建命令。Git 地址属于部署环境配置，放在部署机的 `project.config.json` 中。

## 一键生成 Windows 和 Linux 包

构建机需要 Python 3.11+、Node.js 20+、npm，并能访问前端 npm 源：

macOS/Linux：

```bash
chmod +x build-offline.sh
./build-offline.sh
```

Windows：

```bat
build-offline.bat
```

不带参数时，会在 `offline-packages/` 同时生成 Windows 和 Linux ZIP。默认包是通用包，不包含 `.env`、正式项目配置、SQLite 数据库、业务仓库和 Python wheel。

常用参数：

```text
--output-dir PATH
--target windows|linux       只生成一个目标；默认 all
--skip-frontend-build       复用已有 frontend/dist
--mode generic|project       generic 不绑定项目；project 随包带入配置
--project-config PATH        --mode project 时使用
--bundle-repositories        把当前代码快照打包，不在部署机拉 Git
--bundle-python-wheels       把当前平台的 Python wheel 打包
--wheelhouse PATH            复用已有 wheel 目录
```

只有在部署机连不上内网 Git 或 Python 源时才使用两个 `--bundle-*` 选项。`--bundle-python-wheels` 需要分别在与目标机相同的平台和 Python 小版本上构建；默认模式不受这个限制。

## 部署机配置

解压 ZIP 后，在包根目录创建项目配置：

Linux：

```bash
cp project.config.example.json project.config.json
```

Windows：

```bat
copy project.config.example.json project.config.json
```

编辑 `project.config.json`，把每个 `gitUrl` 换成部署机可访问的内网地址：

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

不要把密码写入 JSON；使用 SSH Key、Git Credential Manager 或公司统一认证。也可以用 `-ProjectConfig`/`--project-config` 指向包外配置，这样升级应用包时无需复制配置。

## 启动

Linux：

```bash
chmod +x start-offline-linux.sh
./start-offline-linux.sh
```

Windows：

```bat
start-offline-windows.bat
```

启动器会创建 `.venv-offline`、从内网 Python 源安装依赖、初始化 SQLite、同步并增量索引 Git，然后启动工作台。代码已经是最新版本时不会重新拉取，也不会重复索引未变化文件。

常用参数：

```text
--project-config PATH / -ProjectConfig PATH
--database PATH       / -Database PATH
--host ADDRESS        / -HostAddress ADDRESS
--port PORT           / -Port PORT
--baseline-parser     / -BaselineParser model|markdown
                      / -NoBrowser
```

启动前会检查端口；如果端口已被占用，脚本会结束监听进程并用同一端口重新启动。无法取得 PID 时才需要手工处理。

## Claude Code 和业务基线模型

问答运行时要求部署机已经安装并认证 Claude Code：

```bash
claude --version
claude auth status
```

如果不在 PATH，可设置 `CLAUDE_CODE_COMMAND` 为完整路径。问答工作区只开放 `Read`、`Glob`、`Grep`，不会让 Claude 修改代码、执行 Shell 或操作 Git。

业务基线导入仍使用 `.env` 中的 `BUSINESS_CODE_MODEL_*` 配置进行结构化；这与 Claude Code 问答运行时是两套配置。没有可用模型时，显式选择 Markdown 解析：

```bash
./start-offline-linux.sh --baseline-parser markdown
```

Windows 使用 `-BaselineParser markdown`。模型失败不会静默切换解析器。

## 升级和排查

- 业务代码更新：重新启动，启动器会从内网 Git 增量更新。
- 应用升级：生成新 ZIP，解压到新目录，继续指向原 SQLite 路径。
- Git 失败：在部署机执行 `git ls-remote <gitUrl>` 验证凭据和网络。
- Python 安装失败：检查 pip 镜像、代理和证书；包内 wheel 模式则检查平台和 Python 小版本。
- 找不到 Claude：确认 CLI 安装、登录状态和 `CLAUDE_CODE_COMMAND`。
- 端口冲突：脚本默认自动停止旧监听进程后重启。
