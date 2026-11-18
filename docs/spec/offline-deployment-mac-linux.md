# macOS / Linux 部署步骤

已拿到 Linux ZIP（`business-code-agent-linux-project.zip`，macOS 与 Linux 共用）时，从第 2 节开始。部署包提供项目源码、启动脚本、配置模板和手册。运行环境、数据库和业务仓库在目标机准备。

## 1. 生成项目包

在项目源码目录执行：

```bash
python3 scripts/offline/build_offline_bundle.py --target linux
```

生成文件：

```text
offline-packages/business-code-agent-linux-project.zip
```

这一步只需在每次发布新版本时执行。打包机需要 Python 3.11+。

## 2. 在目标机安装环境

1. 安装 Git。使用远程仓库时，确认目标机能访问配置中的 Git 地址，并准备好 SSH Key 或凭据助手。
2. 准备 Python 3.11 或更高版本。macOS 可用 Homebrew（`brew install python`）或 python.org 安装包；Linux 用发行版包管理器安装 `python3` 与 `python3-venv`。首次启动会自动创建项目虚拟环境 `.venv`，创建它的 `python3` 必须不低于 3.11；使用已有环境时配置 `startup.python` 或传入 `--python`。
3. 安装 Node.js 和 npm。启动脚本要求 Node.js 20 或更高版本，推荐 Node.js 24。

首次启动会从 Python 包源和 npm registry 安装依赖。如需在内网安装，请配置目标机可访问的镜像。SQLite 随 Python 使用。

在线问答需要另装 Pi CLI，并确认终端中 `pi --version` 可用。项目 ZIP 不包含 Pi、供应商凭据和工程索引。混合检索用于独立诊断和旧评测，仍需单独准备相应依赖、模型文件与索引。

## 3. 解压项目包

将 ZIP 解压到固定目录，例如：

```text
~/business-code-agent
```

后续命令都在这个目录中执行。包根目录的 `README.md` 提供安装手册入口。首次启动会用包里的前端源码和构建配置生成 `frontend/dist`。

## 4. 配置工程和模型

进入解压目录，复制配置模板：

```bash
cd ~/business-code-agent
cp project.config.example.json project.config.json
cp .env.example .env
```

用 UTF-8 保存 `project.config.json`。相对路径以项目目录为准。检查以下配置：

- `project.id`、`project.name`；
- `repositories` 中的 `id`、`gitUrl`、`branch`。若用 `gitUrl: "unused"` 指定本地快照，`localPath` 需要指向目标机上已经准备好的目录；
- `systems` 和 `applications` 中引用的 ID，使其与仓库配置一致；
- `startup.database` 和 `startup.port`，如需使用非默认值。复用已有 Python 环境时，可添加 `startup.python`，值为该环境的解释器路径。

示例配置里的 Git 地址是占位内容，不能直接用于部署。Git 凭据使用目标机已有的 SSH Key 或凭据助手，不要写进 JSON。

如需使用业务补充资料，先把资料放到目标机可访问的目录，再将 `knowledge.businessContextRoot`（或兼容字段 `knowledge.baselineRoot`）改为该目录。项目 ZIP 不包含根目录的业务资料；不使用这类资料时，删除配置中的 `knowledge` 项即可。

在 Pi 中配置目标机可访问、支持工具调用的模型及凭据。在 `.env` 中用 `BUSINESS_CODE_PI_COMMAND` 指定 Pi 命令，用 `BUSINESS_CODE_PI_MODEL` 指定在线默认模型；留空时使用 Pi 自己的模型配置。`BUSINESS_CODE_MODEL_*` 仅用于旧 Runtime。

## 5. 首次启动

在项目根目录执行：

```bash
bash ./start.sh
```

macOS 也可以使用兼容入口 `bash ./start-mac.sh`，两者等价。首次启动时，脚本会按顺序：

1. 检查实际使用的 Python 版本和路径。默认创建或复用 `.venv`；
2. 安装 Python 项目和可选 Tree-sitter；若可选依赖安装失败，则使用内置解析器继续；
3. 在 `frontend` 下执行 `npm ci` 和 `npm run build`；
4. 初始化 SQLite 数据库并按配置同步、索引远程仓库；
5. 启动服务并自动打开浏览器。

成功后访问：

```text
http://127.0.0.1:8082/
```

若修改了配置端口，以 `startup.port` 为准。启动窗口显示 `Workbench is ready` 即表示服务已就绪；按 `Ctrl+C` 停止服务。若该端口已有进程监听，脚本会先停止旧监听进程再启动；重启前请确认占用者就是本应用。

## 6. 后续启动

同一目录再次启动时，可跳过重复安装和前端构建：

```bash
bash ./start.sh --skip-install --skip-frontend-build
```

如果更新了 Python 或前端依赖，去掉对应的跳过参数，让脚本重新安装和构建。

## 不同步远程仓库时

只启动空工作台并初始化数据库：

```bash
bash ./start.sh --mode empty
```

直接索引目标机上的本地源码目录：

```bash
bash ./start.sh --mode repository --repository ~/source/loan-system --repository-id loan-system
```

## 启动失败时查看

- Python 安装或后端依赖失败：检查 `python3 --version` 及 Python 包源；
- 前端安装或构建失败：检查 `node --version`（需 20+）、`npm --version` 及 npm registry；
- 仓库同步失败：检查 Git 是否安装、`gitUrl`/分支是否正确以及目标机是否有仓库权限；
- 服务启动后退出：查看数据库目录（默认 `.data/`）中的 `server-error.log`；正常输出写入 `server.log`。

升级时重新生成并解压新版项目包，保留目标机的 `project.config.json`、`.env`、数据库和业务资料目录。