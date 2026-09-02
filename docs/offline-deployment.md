# Windows / Linux 离线部署

这里的“离线部署”指应用本体通过压缩包交付，部署机仍可访问公司允许的 Python 包源、内网 Git 和内网模型服务。默认不会把 Python wheel 和业务代码仓库固化在安装包中：首次启动从可用的 PyPI/内部镜像安装 Python 依赖，并从 `project.config.json` 配置的内网 Git 获取代码。

## 部署边界

| 内容 | 生成离线包的构建机 | 内网部署机 |
|---|---|---|
| Python 依赖 | 只生成依赖清单 | 从可用的 PyPI 或公司镜像安装 |
| 前端 | 执行 `npm ci` 和构建 | 直接使用构建产物，不需要 Node.js |
| 业务代码 | 不默认打包 | 通过内网 Git 克隆或更新 |
| SQLite | 不预制业务数据库 | 首次启动建立，以后增量更新 |
| 大模型 | 不打包 | 问答可使用本地确定性流程；业务基线导入默认使用模型，也可显式选择 Markdown 解析 |

前端产物和 Python 源码没有平台绑定，因此默认包可跨系统构建，部署机只需 Python 3.11+。Windows 和 Linux 仍分别生成入口包，避免启动器和部署说明混淆。只有显式选择“包内 wheel”严格断网模式时，构建机才必须与部署机的操作系统、CPU 架构和 Python 大小版本一致。

## 一、一键生成通用部署包

构建机需要 Python 3.11+、Node.js/npm，并能取得前端 npm 依赖。Python 依赖默认不在构建机下载。

### Linux / macOS 执行

```bash
chmod +x build-offline.sh
./build-offline.sh
```

### Windows 执行

在 `cmd.exe` 中运行：

```bat
build-offline.bat
```

一次执行会在 `offline-packages` 中同时生成 Windows 和 Linux 两个 ZIP。默认压缩包包含应用源码、已构建前端、Python 依赖清单、启动器和项目配置示例；不包含 `.env`、正式项目配置、业务基线、运行数据库、Python wheel 和业务代码仓库。

常用构建参数：

```text
--output-dir PATH             指定输出目录
--target windows|linux        只生成指定平台；默认生成两个平台
--skip-frontend-build         复用现有 frontend/dist
--without-tree-sitter         不打包可选的 Tree-sitter 解析器
--mode demo                   生成不连接项目 Git 的体验包
--mode project --project-config PATH
                               显式生成已经绑定项目配置的包
--bundle-repositories         连 Git 内网也不可达时，显式打包当前代码快照
--bundle-python-wheels        Python 包源也不可达时，显式打包当前平台 wheel
--wheelhouse PATH             复用已有 wheel 目录，并自动启用 wheel 模式
```

两个 `--bundle-*` 都是严格断网兜底模式。源码快照不会自动更新；包内 wheel 会重新引入平台和 Python 小版本绑定。正常内网部署不需要使用它们。

默认不传 `--project-config`，生成的包不绑定任何业务项目。同一个包可以部署到不同环境，各环境使用自己的项目配置。

## 二、在部署机配置项目

解压后，在部署机复制配置示例：

Linux：

```bash
cp project.config.example.json project.config.json
```

Windows：

```bat
copy project.config.example.json project.config.json
```

然后编辑 `project.config.json`。每个仓库的 `gitUrl` 使用当前部署环境可访问的内网地址：

```json
{
  "repositoryRoot": ".data/repositories",
  "repositories": [
    {
      "id": "loan-core",
      "gitUrl": "ssh://git@git.company.local/loan/loan-core.git",
      "branch": "main"
    }
  ]
}
```

不要把 Git 密码写进 JSON。部署机使用 SSH Key、Git Credential Manager 或公司统一认证。

## 三、部署到内网机器

目标机准备：

- Python 3.11 或更高版本；
- 能访问公司允许的 PyPI 或 Python 内部镜像；
- 内网 Git 模式需要 Git 以及访问仓库的凭据；
- 不需要 Node.js、npm。

将 ZIP 复制到目标机并完整解压。不要只复制启动脚本。

### Linux 启动

```bash
chmod +x start-offline-linux.sh
./start-offline-linux.sh
```

### Windows 启动

双击 `start-offline-windows.bat`，或者执行：

```bat
start-offline-windows.bat
```

首次启动会创建 `.venv-offline`、从已配置的 Python 包源安装依赖、同步内网 Git、初始化 SQLite、索引代码并启动页面。pip 会遵循机器已有的 `pip.ini`、`pip.conf`、环境变量或公司镜像配置。以后启动仍会检查 Git；没有新提交时不会拉取或重复索引未变化文件。

常用启动参数：

```text
--project-config PATH / -ProjectConfig PATH
--database PATH       / -Database PATH
--host ADDRESS        / -HostAddress ADDRESS
--port PORT           / -Port PORT
--demo                / -Demo
--baseline-parser     / -BaselineParser model|markdown
                      / -NoBrowser
```

Linux 示例：

```bash
./start-offline-linux.sh --database /data/business-code-agent/knowledge.db --port 8082
```

Windows 示例：

```bat
start-offline-windows.bat -Database "D:\business-code-agent\knowledge.db" -Port 8082
```

端口被占用时，启动器会直接停止占用进程并复用原端口；如果系统无法取得 PID 或进程拒绝结束，错误信息会提示手工处理。

生产环境建议把 SQLite 放在解压目录之外，这样替换应用包时不会误删业务知识。

## 四、内网模型配置

复制 `.env.example` 为 `.env`。只使用代码索引和问答的本地确定性流程时：

```dotenv
BUSINESS_CODE_MODEL_ENABLED=false
```

此时不要在页面导入业务基线；如果确实需要无模型导入，请明确选择解析器：

```bash
./start-offline-linux.sh --baseline-parser markdown
```

Windows 使用 `-BaselineParser markdown`。这是一种显式运行模式，模型调用失败不会自动切换。

使用公司内网 OpenAI 兼容接口时：

```dotenv
BUSINESS_CODE_MODEL_ENABLED=true
BUSINESS_CODE_MODEL_PROVIDER=openai
BUSINESS_CODE_MODEL_NAME=company-model
BUSINESS_CODE_MODEL_BASE_URL=http://model-gateway.company.local/v1
BUSINESS_CODE_MODEL_API_KEY=internal-token
# 直连 DeepSeek OpenAI 兼容接口时可显式关闭思考模式
# BUSINESS_CODE_MODEL_THINKING=disabled
```

启动器默认用配置的模型刷新业务基线；页面中的问答 Agent 是否调用模型仍由 `.env` 控制。模型初始化或结构化失败会直接报告错误，不会静默改用另一套知识体系。

当 `BASE_URL` 使用 `api.deepseek.com` 时，适配器会默认发送 `thinking=disabled`，以兼容 Query Agent 的结构化工具调用；修改 `.env` 后需要重启启动器。

## 五、更新与排查

- 业务代码更新：重新启动即可从内网 Git 获取并增量索引。
- 应用版本或 Python 依赖更新：重新生成 ZIP，部署到新目录，并继续使用原 SQLite 路径。
- `Git was not found`：安装 Git，或重新构建源码快照包。
- Python 版本过低：安装 Python 3.11 或更高版本。
- Python 依赖下载失败：检查部署机的 PyPI/内部镜像地址、代理和证书配置。
- 严格 wheel 模式安装失败：检查构建机与部署机的平台、CPU 架构和 Python 大小版本是否一致。
- 内网 Git 拉取失败：先在目标机命令行验证同一 Git URL 和凭据；应用不会保存 Git 密码。
- 端口占用：启动器会自动停止占用进程并复用原端口；如果无法取得 PID 或停止失败，请先手工结束占用进程再重试。
