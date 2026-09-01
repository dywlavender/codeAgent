#!/usr/bin/env python3
"""Build a self-contained offline deployment archive for Windows or Linux."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import tomllib
except ImportError as exc:  # pragma: no cover - Python 3.11+ always has it
    raise SystemExit("Python 3.11 or later is required") from exc


ROOT = Path(__file__).resolve().parent.parent
IGNORED_DIRECTORIES = {
    ".git", ".idea", ".vscode", ".gradle", "__pycache__", "node_modules",
    "target", "build", ".venv", ".venv-windows", ".venv-offline",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Windows/Linux 压缩部署包")
    parser.add_argument("--target", choices=("all", "linux", "windows"), default="all")
    parser.add_argument("--mode", choices=("generic", "project", "demo"), default="generic")
    parser.add_argument("--project-config", default=str(ROOT / "project.config.json"))
    parser.add_argument("--output-dir", default=str(ROOT / "offline-packages"))
    parser.add_argument("--wheelhouse", help="复用已经准备好的 wheel 目录，不再执行 pip download")
    parser.add_argument(
        "--bundle-python-wheels", action="store_true",
        help="把当前目标平台的 Python wheel 打进包；默认由部署机从可用镜像安装",
    )
    parser.add_argument("--skip-frontend-build", action="store_true")
    parser.add_argument("--without-tree-sitter", action="store_true")
    parser.add_argument(
        "--bundle-repositories", action="store_true",
        help="把当前源码快照打进包；默认在目标机通过内网 Git 获取最新代码",
    )
    args = parser.parse_args()

    _assert_python()
    bundle_wheels = bool(args.bundle_python_wheels or args.wheelhouse)
    if args.target == "all" and bundle_wheels:
        raise SystemExit(
            "--target all 不能与包内 wheel 模式同时使用。wheel 具有平台绑定，"
            "请分别在 Windows/Linux 上使用 --target 和 --bundle-python-wheels。"
        )
    if bundle_wheels:
        _assert_build_host(args.target)
    if not args.skip_frontend_build:
        _build_frontend()
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise SystemExit("frontend/dist 不存在，请移除 --skip-frontend-build 后重新生成")

    if args.target == "all":
        _build_all_targets(args)
        return

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    machine = _machine_name() if bundle_wheels else "universal"
    python_tag = f"py{sys.version_info.major}.{sys.version_info.minor}" if bundle_wheels else "py3"
    bundle_name = f"business-code-agent-offline-{args.target}-{machine}-{python_tag}"

    with tempfile.TemporaryDirectory(prefix="business-code-offline-") as temporary:
        staging = Path(temporary) / bundle_name
        staging.mkdir()
        _copy_application(staging)
        if args.mode == "generic":
            shutil.rmtree(staging / "knowledge", ignore_errors=True)
        requirements = _runtime_requirements(include_tree_sitter=not args.without_tree_sitter)
        offline_root = staging / "offline"
        offline_root.mkdir(parents=True)
        (offline_root / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")

        if bundle_wheels:
            wheelhouse = offline_root / "wheelhouse"
            wheelhouse.mkdir(parents=True)
            if args.wheelhouse:
                source_wheelhouse = Path(args.wheelhouse).expanduser().resolve()
                if not source_wheelhouse.is_dir() or not any(source_wheelhouse.glob("*.whl")):
                    raise SystemExit(f"wheelhouse 中没有 wheel 文件: {source_wheelhouse}")
                _copy_tree_contents(source_wheelhouse, wheelhouse)
            else:
                _download_wheels(requirements, wheelhouse)

        config_name = None
        repository_count = 0
        repository_mode = "intranet-git" if args.mode == "generic" else "none"
        if args.mode == "project":
            config_path = Path(args.project_config).expanduser().resolve()
            repository_count, repository_mode = _prepare_project_config(
                config_path, staging, bundle_repositories=args.bundle_repositories,
            )
            config_name = "project.config.json"

        metadata = {
            "product": "Business Code Agent",
            "target": args.target,
            "platformSystem": platform.system(),
            "machine": machine,
            "pythonMinimum": "3.11",
            "pythonMajorMinor": f"{sys.version_info.major}.{sys.version_info.minor}" if bundle_wheels else None,
            "dependencyMode": "bundled-wheels" if bundle_wheels else "download",
            "mode": args.mode,
            "projectConfig": config_name,
            "repositoryCount": repository_count,
            "repositoryMode": repository_mode,
            "treeSitterIncluded": not args.without_tree_sitter,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
        (offline_root / "package-info.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

        archive_base = output_dir / bundle_name
        archive_path = Path(shutil.make_archive(
            str(archive_base), "zip", root_dir=staging.parent, base_dir=staging.name,
        ))

    print(f"离线包已生成: {archive_path}")
    print(f"目标环境: {args.target} / Python 3.11+")
    if bundle_wheels:
        print(f"Python wheel 已打包，仅适用于 {machine} / Python {metadata['pythonMajorMinor']}。")
    else:
        print("Python 依赖将在部署机通过可用的 PyPI 或内部镜像安装。")
    print("目标机不需要 Node.js/npm；内网 Git 模式仍需要 Git。")


def _build_all_targets(args: argparse.Namespace) -> None:
    common = [
        "--mode", args.mode,
        "--output-dir", args.output_dir,
        "--skip-frontend-build",
    ]
    if args.mode == "project":
        common.extend(("--project-config", args.project_config))
    if args.without_tree_sitter:
        common.append("--without-tree-sitter")
    if args.bundle_repositories:
        common.append("--bundle-repositories")
    for target in ("windows", "linux"):
        _run(
            [sys.executable, str(Path(__file__).resolve()), "--target", target, *common],
            cwd=ROOT,
        )
    print("Windows 和 Linux 部署包已全部生成。")


def _assert_build_host(target: str) -> None:
    current = platform.system().lower()
    expected = "windows" if target == "windows" else "linux"
    if current != expected:
        raise SystemExit(
            f"{target} 离线包必须在同类 {target} 构建机生成，当前系统是 {platform.system()}。"
            "这样才能下载与目标平台匹配的二进制 wheel。"
        )


def _assert_python() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("构建机需要 Python 3.11 或更高版本")


def _build_frontend() -> None:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("构建机缺少 Node.js/npm；目标离线机不需要安装 Node.js")
    _run([npm, "ci", "--no-audit", "--no-fund"], cwd=ROOT / "frontend")
    _run([npm, "run", "build"], cwd=ROOT / "frontend")


def _runtime_requirements(*, include_tree_sitter: bool) -> list[str]:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload.get("project") or {}
    requirements = [str(item) for item in project.get("dependencies") or []]
    if include_tree_sitter:
        requirements.extend(str(item) for item in (project.get("optional-dependencies") or {}).get("tree-sitter", []))
    if not requirements:
        raise SystemExit("pyproject.toml 没有运行时依赖")
    return list(dict.fromkeys(requirements))


def _download_wheels(requirements: list[str], wheelhouse: Path) -> None:
    print("下载当前平台的 Python wheel...")
    _run([
        sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
        "--only-binary=:all:", "--dest", str(wheelhouse), *requirements,
    ], cwd=ROOT)
    if not any(wheelhouse.glob("*.whl")):
        raise SystemExit("没有下载到 Python wheel")


def _copy_application(staging: Path) -> None:
    _copy_tree(ROOT / "business_code_agent", staging / "business_code_agent")
    _copy_tree(ROOT / "frontend" / "dist", staging / "frontend" / "dist")
    for directory in ("examples", "knowledge", "docs"):
        source = ROOT / directory
        if source.is_dir():
            _copy_tree(source, staging / directory)
    for filename in ("README.md", "pyproject.toml", ".env.example", "project.config.example.json"):
        source = ROOT / filename
        if source.is_file():
            shutil.copy2(source, staging / filename)
    for filename in ("start-offline-linux.sh", "start-offline-windows.bat"):
        shutil.copy2(ROOT / filename, staging / filename)
    scripts = staging / "scripts"
    scripts.mkdir(exist_ok=True)
    for filename in ("start-offline-windows.ps1", "offline_runtime.py"):
        shutil.copy2(ROOT / "scripts" / filename, scripts / filename)


def _prepare_project_config(
    config_path: Path,
    staging: Path,
    *,
    bundle_repositories: bool,
) -> tuple[int, str]:
    if not config_path.is_file():
        raise SystemExit(f"项目配置不存在: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"项目配置无法读取: {exc}") from exc
    repositories = payload.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise SystemExit("项目模式要求 project.config.json 至少配置一个 repositories 条目")

    local_sources = [
        _local_git_source(config_path, str(item.get("gitUrl") or ""))
        for item in repositories if isinstance(item, dict)
    ]
    local_count = sum(source is not None for source in local_sources)
    if local_count and local_count != len(repositories) and not bundle_repositories:
        raise SystemExit(
            "项目配置同时包含本地 Git 路径和远端 Git 地址。请统一使用内网 Git 地址，"
            "或增加 --bundle-repositories 将所有仓库作为快照打包。"
        )
    use_snapshots = bundle_repositories or local_count == len(repositories)

    configured_root = _resolve(config_path, payload.get("repositoryRoot") or ".data/repositories")
    offline_repositories = staging / "offline-repositories"
    rewritten = []
    for index, item in enumerate(repositories):
        if not isinstance(item, dict):
            raise SystemExit(f"repositories[{index}] 必须是对象")
        repository_id = str(item.get("id") or "").strip()
        if not repository_id or not re.fullmatch(r"[A-Za-z0-9._-]+", repository_id):
            raise SystemExit(f"仓库 id 只能包含字母、数字、点、下划线和短横线: {repository_id!r}")
        if use_snapshots:
            source = _resolve(config_path, item["localPath"]) if item.get("localPath") else configured_root / repository_id
            local_git_source = _local_git_source(config_path, str(item.get("gitUrl") or ""))
            if not source.is_dir() and local_git_source is not None:
                source = local_git_source
            if not source.is_dir():
                raise SystemExit(
                    f"仓库本地快照不存在: {source}\n"
                    "请先同步 Git，或移除 --bundle-repositories 让目标机从内网 Git 获取。"
                )
            destination = offline_repositories / repository_id
            print(f"打包代码快照: {repository_id} ← {source}")
            _copy_tree(source, destination)
            rewritten.append({**item, "localPath": f"offline-repositories/{repository_id}"})
        else:
            git_url = str(item.get("gitUrl") or "").strip()
            if not git_url:
                raise SystemExit(
                    f"仓库 {repository_id} 缺少 gitUrl；内网 Git 模式无法在目标机获取代码。"
                    "可以补充内网地址，或使用 --bundle-repositories。"
                )
            portable = dict(item)
            portable.pop("localPath", None)
            rewritten.append(portable)

    knowledge = dict(payload.get("knowledge") or {})
    baseline_value = knowledge.get("baselineRoot")
    if baseline_value:
        baseline_source = _resolve(config_path, baseline_value)
        if baseline_source.is_dir():
            baseline_destination = staging / "knowledge" / "baseline"
            if baseline_destination.exists():
                shutil.rmtree(baseline_destination)
            _copy_tree(baseline_source, baseline_destination)
            knowledge["baselineRoot"] = "knowledge/baseline"

    payload["repositoryRoot"] = "offline-repositories" if use_snapshots else ".data/repositories"
    payload["repositories"] = rewritten
    if knowledge:
        payload["knowledge"] = knowledge
    (staging / "project.config.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return len(rewritten), "bundled-snapshot" if use_snapshots else "intranet-git"


def _resolve(config_path: Path, value: str) -> Path:
    candidate = Path(str(value)).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()


def _local_git_source(config_path: Path, value: str) -> Path | None:
    value = value.strip()
    if not value or "://" in value or re.match(r"^[^/\\]+@[^:]+:", value):
        return None
    candidate = _resolve(config_path, value)
    return candidate if candidate.is_dir() else None


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=_ignored_names, dirs_exist_ok=True)


def _copy_tree_contents(source: Path, destination: Path) -> None:
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            _copy_tree(item, target)
        else:
            shutil.copy2(item, target)


def _ignored_names(_directory: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name in IGNORED_DIRECTORIES or name.endswith((".pyc", ".pyo"))
    }


def _machine_name() -> str:
    value = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    return re.sub(r"[^a-z0-9_.-]", "-", value) or "unknown"


def _run(command: list[str], *, cwd: Path) -> None:
    print("+ " + " ".join(command))
    process = subprocess.run(command, cwd=cwd, check=False)
    if process.returncode:
        raise SystemExit(f"命令执行失败，退出码 {process.returncode}: {command[0]}")


if __name__ == "__main__":
    main()
