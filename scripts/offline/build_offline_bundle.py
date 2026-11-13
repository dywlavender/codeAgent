#!/usr/bin/env python3
"""Package the runnable project source for deployment.

The archive intentionally contains the project itself, not a second software
distribution: Python, Node.js, npm, Git, the virtual environment, databases,
and business repositories are prepared on the deployment machine.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent

# Local build/runtime output must not become part of a source deployment archive.
IGNORED_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    "coverage",
    "target",
    "build",
    ".venv",
    ".venv-windows",
    ".venv-offline",
    ".data",
    "offline-packages",
}

COMMON_FILES = (
    "pyproject.toml",
    "project.config.example.json",
    "start.sh",
    "scripts/launcher/start.sh",
    "scripts/launcher/start.py",
)
FRONTEND_FILES = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.js",
)
FRONTEND_DIRECTORIES = (
    "src",
    "public",
)
TARGET_FILES = {
    "windows": (
        ".env.example",
        "docs/spec/offline-deployment.md",
        "docs/spec/windows-usage.md",
    ),
    "linux": ("start-mac.sh",),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="把项目源码和启动脚本打成部署包，不打包运行环境或业务仓库。",
    )
    parser.add_argument(
        "--target",
        choices=("windows", "linux", "all"),
        default="windows",
        help="部署目标，默认生成 Windows 包。",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "offline-packages"),
        help="ZIP 输出目录，默认是 offline-packages/。",
    )
    args = parser.parse_args()

    _assert_python()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    targets = ("windows", "linux") if args.target == "all" else (args.target,)
    archives = [_build_package(target, output_dir) for target in targets]
    for archive in archives:
        print(f"项目部署包已生成: {archive}")
    print("包内不包含 Python、Node.js/npm、Git、虚拟环境、数据库或业务代码仓库。")


def _build_package(target: str, output_dir: Path) -> Path:
    package_name = f"business-code-agent-{target}-project"
    with tempfile.TemporaryDirectory(prefix="business-code-agent-package-") as temporary:
        staging_root = Path(temporary) / package_name
        staging_root.mkdir(parents=True)
        _copy_project(staging_root, target=target)
        archive_base = output_dir / package_name
        return Path(
            shutil.make_archive(
                str(archive_base),
                "zip",
                root_dir=staging_root.parent,
                base_dir=staging_root.name,
            )
        )


def _copy_project(staging: Path, *, target: str) -> None:
    _copy_required_tree(ROOT / "business_code_agent", staging / "business_code_agent")

    for filename in COMMON_FILES:
        _copy_required_file(ROOT / filename, staging / filename)

    frontend_root = ROOT / "frontend"
    for filename in FRONTEND_FILES:
        _copy_required_file(frontend_root / filename, staging / "frontend" / filename)
    for directory in FRONTEND_DIRECTORIES:
        source = frontend_root / directory
        if source.is_dir():
            _copy_required_tree(source, staging / "frontend" / directory)

    for relative_path in TARGET_FILES[target]:
        _copy_required_file(ROOT / relative_path, staging / relative_path)

    if target == "windows":
        (staging / "README.md").write_text(
            "# Windows 安装与启动\n\n"
            "首次部署按[安装手册](docs/spec/offline-deployment.md)配置运行环境、项目资料和模型。\n\n"
            "在解压目录打开 Git Bash，执行 `bash ./start.sh`。\n\n"
            "日常启动、命令参数和日志位置见[启动手册](docs/spec/windows-usage.md)。\n",
            encoding="utf-8",
        )


def _copy_required_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"项目缺少部署文件: {source.relative_to(ROOT)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if source.suffix == ".sh":
        destination.write_bytes(destination.read_bytes().replace(b"\r\n", b"\n"))


def _assert_python() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("构建机需要 Python 3.11 或更高版本")


def _copy_required_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise SystemExit(f"项目缺少部署目录: {source.relative_to(ROOT)}")
    shutil.copytree(source, destination, ignore=_ignored_names, dirs_exist_ok=True)


def _ignored_names(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED_NAMES or name.endswith((".pyc", ".pyo"))
    }


if __name__ == "__main__":
    main()
