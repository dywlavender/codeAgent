"""Start the workbench with native Python on macOS, Linux or Windows."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[2]
WINDOWS = sys.platform == "win32"
CLI = ["-m", "business_code_agent.cli"]


class StartupError(Exception):
    pass


def _arguments():
    parser = argparse.ArgumentParser(description="Start the Business Code Agent workbench", prog="bash ./start.sh")
    parser.add_argument("--mode", type=str.casefold, choices=("empty", "repository"), help="启动空工作台或索引本地仓库")
    parser.add_argument("--database", help="SQLite 数据库路径")
    parser.add_argument("--repository", type=Path, help="本地源码目录")
    parser.add_argument("--repository-id", default="repo-main", help="本地仓库 ID")
    parser.add_argument("--project-config", type=Path, help="工程配置路径，默认使用项目根目录的 project.config.json")
    parser.add_argument("--project-registry", type=Path, help="多工程注册表目录")
    parser.add_argument("--project-id", help="注册表模式的默认工程 ID")
    parser.add_argument("--python", help="Python 解释器路径，优先于 startup.python")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, help="监听端口，默认读取 startup.port 或使用 8082")
    parser.add_argument("--skip-install", action="store_true", help="复用已安装的 Python 依赖")
    parser.add_argument("--skip-frontend-build", action="store_true", help="复用已有的前端构建")
    parser.add_argument("--no-browser", action="store_true", help="启动后手动打开浏览器")
    return parser.parse_args()


def _project_startup(config):
    if config is None:
        return {}
    try:
        payload = json.loads(config.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StartupError(f"无法读取工程配置 {config}：{exc}") from exc
    if not isinstance(payload, dict):
        raise StartupError("工程配置必须是 JSON 对象")
    startup = payload.get("startup")
    if startup is None:
        return {}
    if not isinstance(startup, dict):
        raise StartupError("工程配置的 startup 必须是对象")
    return startup


def _runtime_python(requested):
    if requested is not None:
        if not isinstance(requested, str) or not requested.strip():
            raise StartupError("startup.python 必须是非空的 Python 解释器路径")
        executable = Path(requested).expanduser().absolute()
    else:
        venv = ROOT / (".venv-windows" if WINDOWS else ".venv")
        executable = venv / ("Scripts/python.exe" if WINDOWS else "bin/python")
        if not executable.is_file():
            if sys.version_info < (3, 11):
                raise StartupError("创建虚拟环境需要 Python 3.11 或更高版本。可通过 --python 指定解释器")
            print("==> 创建项目虚拟环境", flush=True)
            subprocess.run([sys.executable, "-X", "utf8", "-m", "venv", str(venv)], check=True)
    if not executable.is_file():
        raise StartupError(f"找不到 Python 解释器 {executable}")
    python = [str(executable), "-X", "utf8"]
    version = subprocess.run(
        [*python, "-c", "import sys; print('Python', sys.version.split()[0], sys.executable); sys.exit(sys.version_info < (3, 11))"],
        check=False,
    )
    if version.returncode:
        raise StartupError(f"需要 Python 3.11 或更高版本，当前解释器为 {executable}")
    return python


def _prepare_dependencies(python, args):
    if args.skip_install:
        check = subprocess.run([*python, "-c", "import langchain, langchain_openai"], check=False)
        if check.returncode:
            raise StartupError(f"所选 Python 环境缺少后端依赖：{python[0]}。请去掉 --skip-install 后重试")
    else:
        print("==> 安装 Python 依赖", flush=True)
        install = [*python, "-m", "pip", "install", "--disable-pip-version-check", "-e"]
        if subprocess.run([*install, ".[tree-sitter]"], check=False).returncode:
            print("可选的 Tree-sitter 安装失败，继续安装后端基础依赖。", file=sys.stderr, flush=True)
            subprocess.run([*install, "."], check=True)

    frontend = ROOT / "frontend"
    if args.skip_frontend_build:
        if not (frontend / "dist/index.html").is_file():
            raise StartupError("缺少 frontend/dist/index.html，请去掉 --skip-frontend-build 后重试")
        return
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if WINDOWS else "npm")
    if not node or not npm:
        raise StartupError("构建前端需要 Node.js 和 npm")
    print("==> 构建前端", flush=True)
    subprocess.run([node, "-e", "if (+process.versions.node.split('.')[0] < 20) { console.error('Node.js 20 or later is required'); process.exit(1) }"], check=True)
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=frontend, shell=WINDOWS, check=True)
    subprocess.run([npm, "run", "build"], cwd=frontend, shell=WINDOWS, check=True)


def _prepare_project(python, args, config, database):
    database.parent.mkdir(parents=True, exist_ok=True)
    if args.project_registry:
        args.project_registry.mkdir(parents=True, exist_ok=True)
        return
    repo = args.repository.expanduser().absolute() if args.repository else None
    if not config and args.mode == "repository" and (not repo or not repo.is_dir()):
        raise StartupError("Repository 模式需要通过 --repository 指定已有的源码目录")
    print("==> 准备工程数据库", flush=True)
    subprocess.run([*python, *CLI, "init-db", "--db", str(database)], check=True)
    if config:
        subprocess.run([*python, *CLI, "sync-project", "--config", str(config), "--db", str(database)], check=True)
    elif args.mode == "repository":
        subprocess.run([
            *python, *CLI, "ingest-repo", str(repo),
            "--repository-id", args.repository_id, "--db", str(database),
        ], check=True)


def _port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _listeners(port):
    if WINDOWS:
        output = subprocess.check_output(["netstat.exe", "-ano"], text=True, encoding="ascii", errors="replace")
        pids = set()
        for line in output.splitlines():
            columns = line.split()
            if (len(columns) == 5 and columns[0] == "TCP" and columns[1].endswith(f":{port}")
                    and columns[2].endswith(":0") and columns[-1].isdigit()):
                pids.add(int(columns[-1]))
        return pids
    lsof = shutil.which("lsof")
    if not lsof:
        return set()
    result = subprocess.run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return {int(value) for value in result.stdout.split() if value.isdigit()}


def _stop_listener(host, port):
    pids = _listeners(port)
    if not pids:
        raise StartupError(f"端口 {port} 已占用。请关闭监听进程或通过 --port 指定其他端口")
    for pid in sorted(pids):
        print(f"==> 停止监听端口 {port} 的进程 {pid}", flush=True)
        if WINDOWS:
            subprocess.run(["taskkill.exe", "/PID", str(pid), "/F"], stdout=subprocess.DEVNULL, check=False)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    for _ in range(20):
        if not _port_open(host, port):
            return
        time.sleep(0.25)
    if not WINDOWS:
        for pid in pids & _listeners(port):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not _port_open(host, port):
            return
    raise StartupError(f"端口 {port} 仍在使用。请关闭监听进程或通过 --port 指定其他端口")


def _serve(python, args, config, database, port):
    probe = "127.0.0.1" if args.host in ("0.0.0.0", "::", "") else args.host
    if _port_open(probe, port):
        _stop_listener(probe, port)
    url_host = f"[{probe}]" if ":" in probe else probe
    url = f"http://{url_host}:{port}/"
    ready_url = url + ("api/projects" if args.project_registry else "api/workspace")
    opener = build_opener(ProxyHandler({}))
    command = [*python, *CLI, "serve-query", "--db", str(database), "--host", args.host, "--port", str(port)]
    if config:
        command.extend(("--project-config", str(config)))
    if args.project_registry:
        command.extend(("--project-registry", str(args.project_registry.expanduser().absolute())))
    if args.project_id:
        command.extend(("--project-id", args.project_id))
    log = database.parent / "server.log"
    error_log = database.parent / "server-error.log"
    with log.open("wb") as output, error_log.open("wb") as errors:
        server = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=errors)
    try:
        for _ in range(40):
            if server.poll() is not None:
                detail = "\n".join(error_log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
                raise StartupError(f"服务启动时退出，详情见 {error_log}\n{detail}")
            try:
                with opener.open(ready_url, timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, OSError):
                pass
            time.sleep(0.25)
        else:
            raise StartupError(f"服务未就绪，详情见 {error_log}")
        print(f"Workbench is ready: {url}\nDatabase: {database}\nLogs: {log}\n按 Ctrl+C 停止服务。", flush=True)
        if not args.no_browser and not webbrowser.open(url):
            print(f"请在浏览器中打开 {url}", flush=True)
        return server.wait()
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def main():
    os.chdir(ROOT)
    args = _arguments()
    config = args.project_config
    if config is None and not (args.project_registry or args.mode or args.repository):
        default_config = ROOT / "project.config.json"
        if default_config.is_file():
            config = default_config
    if config is not None:
        config = config.expanduser().absolute()
    startup = _project_startup(config)

    database = args.database if args.database is not None else startup.get("database") or ".data/knowledge.db"
    if not isinstance(database, str) or not database.strip():
        raise StartupError("startup.database 必须是非空的数据库路径")
    port = args.port if args.port is not None else startup.get("port")
    try:
        port = 8082 if port in (None, "") else int(port)
    except (TypeError, ValueError) as exc:
        raise StartupError("startup.port 必须是 1 至 65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise StartupError("端口必须在 1 至 65535 之间")
    if args.project_registry is not None:
        args.project_registry = args.project_registry.expanduser().absolute()
    database = Path(database).expanduser().absolute()
    python = _runtime_python(args.python if args.python is not None else startup.get("python"))
    _prepare_dependencies(python, args)
    _prepare_project(python, args, config, database)
    return _serve(python, args, config, database, port)


def _terminate(signum, _frame):
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (StartupError, OSError, subprocess.SubprocessError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
