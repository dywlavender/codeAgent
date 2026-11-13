# Windows 启动与参数参考

首次安装步骤见[Windows 部署步骤](offline-deployment.md)。本文只说明部署完成后的启动方式、参数和日志位置。

## 常用启动方式

在项目根目录打开 Git Bash。

```bash
# 使用根目录的 project.config.json 启动
bash ./start.sh

# 日常启动：跳过已完成的依赖安装和前端构建
bash ./start.sh --skip-install --skip-frontend-build

# 使用已有虚拟环境
bash ./start.sh --python "D:/venvs/code-agent/Scripts/python.exe"

# 不同步仓库，只初始化并启动空工作台
bash ./start.sh --mode Empty

# 直接索引本机已有的源码目录
bash ./start.sh --mode Repository --repository "D:/source/loan-system" --repository-id loan-system
```

默认读取根目录的 `project.config.json`。可通过 `--project-config` 指定另一份配置，或用 `--mode Empty` 和 `--mode Repository` 选择启动模式。工程配置使用 UTF-8 保存，文件可带 BOM。

## 参数

| 参数 | 作用 | 默认值 |
| --- | --- | --- |
| `--project-config PATH` | 指定项目配置并按配置初始化、同步仓库 | 自动检测根目录配置 |
| `--mode Empty\|Repository` | 空工作台或本地目录索引模式 | 无工程配置时为 `Empty` |
| `--repository PATH` | `Repository` 模式下的本地源码目录 | 无 |
| `--repository-id ID` | 本地源码对应的仓库 ID | `repo-main` |
| `--database PATH` | 覆盖数据库路径 | `startup.database` 或 `.data/knowledge.db` |
| `--host ADDRESS` | 服务监听地址 | `127.0.0.1` |
| `--port PORT` | 服务端口 | `startup.port` 或 `8082` |
| `--python PATH` | 指定安装依赖、同步仓库和运行服务的解释器 | `startup.python` 或 `.venv-windows/Scripts/python.exe` |
| `--skip-install` | 复用已安装的后端依赖 | 安装依赖 |
| `--skip-frontend-build` | 复用已有的 `frontend/dist` | 构建前端 |
| `--no-browser` | 启动后手动打开浏览器 | 自动打开 |

使用工程配置时，启动器会按配置同步和索引仓库。依赖或前端更新后去掉对应的跳过参数。

在 `project.config.json` 的 `startup` 中添加 `"python": "D:/venvs/code-agent/Scripts/python.exe"`，以后直接执行 `bash ./start.sh`。指定的解释器须为 Python 3.11+。`--python` 优先于配置。启动窗口会显示实际 Python 版本和路径。

## 运行状态

- 默认地址：`http://127.0.0.1:8082/`；
- 就绪提示：`Workbench is ready`；
- 停止服务：在启动窗口按 `Ctrl+C`；
- 日志：数据库所在目录的 `server.log` 和 `server-error.log`。
