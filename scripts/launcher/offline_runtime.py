#!/usr/bin/env python3
"""Small standard-library helper shared by offline launchers."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--metadata", required=True)
    validate.add_argument("--target", required=True, choices=("linux", "windows"))
    defaults = subparsers.add_parser("defaults")
    defaults.add_argument("--config", required=True)
    defaults.add_argument("--format", choices=("json", "tsv"), default="json")
    metadata = subparsers.add_parser("metadata")
    metadata.add_argument("--metadata", required=True)
    metadata.add_argument(
        "--field", required=True,
        choices=("pythonMajorMinor", "machine", "target", "repositoryMode", "dependencyMode", "mode"),
    )
    args = parser.parse_args()
    if args.command == "validate":
        _validate(Path(args.metadata), args.target)
    elif args.command == "defaults":
        _defaults(Path(args.config), args.format)
    else:
        payload = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
        print(payload.get(args.field) or "")


def _validate(path: Path, target: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_system = "Windows" if target == "windows" else "Linux"
    if payload.get("target") != target or platform.system() != expected_system:
        raise SystemExit(
            f"离线包目标是 {payload.get('target')}，当前系统是 {platform.system()}，不能混用"
        )
    package_root = path.resolve().parent.parent
    requirements = package_root / "offline" / "requirements.txt"
    if not requirements.is_file():
        raise SystemExit(f"离线包缺少依赖清单: {requirements}")
    dependency_mode = str(payload.get("dependencyMode") or "").strip()
    if dependency_mode not in {"download", "bundled-wheels"}:
        raise SystemExit(f"离线包 dependencyMode 无效: {dependency_mode or '<empty>'}")
    repository_mode = str(payload.get("repositoryMode") or "").strip()
    if repository_mode not in {"intranet-git", "bundled-snapshot", "none"}:
        raise SystemExit(f"离线包 repositoryMode 无效: {repository_mode or '<empty>'}")
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    minimum = tuple(int(part) for part in str(payload.get("pythonMinimum") or "3.11").split("."))
    if sys.version_info[:2] < minimum:
        raise SystemExit(
            f"应用需要 Python {'.'.join(map(str, minimum))} 或更高版本，当前是 Python {current_python}。"
        )
    current_machine = _machine_name()
    if dependency_mode == "bundled-wheels":
        wheelhouse = package_root / "offline" / "wheelhouse"
        has_requirements = bool(requirements.read_text(encoding="utf-8").strip())
        if not wheelhouse.is_dir() or (has_requirements and not any(wheelhouse.glob("*.whl"))):
            raise SystemExit(f"离线包缺少 wheel 文件: {wheelhouse}")
        if payload.get("pythonMajorMinor") != current_python:
            raise SystemExit(
                f"包内 wheel 需要 Python {payload.get('pythonMajorMinor')}，当前是 Python {current_python}。"
            )
        if payload.get("machine") != current_machine:
            raise SystemExit(
                f"包内 wheel 架构是 {payload.get('machine')}，当前机器是 {current_machine}，不能混用"
            )
    print(json.dumps({"python": current_python, "machine": current_machine}, ensure_ascii=False))


def _defaults(path: Path, output_format: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    startup = payload.get("startup") or {}
    knowledge = payload.get("knowledge") or {}
    if not isinstance(startup, dict):
        raise SystemExit("项目配置 startup 必须是对象")
    if not isinstance(knowledge, dict):
        raise SystemExit("项目配置 knowledge 必须是对象")
    database = str(startup.get("database") or ".data/knowledge.db").strip()
    if not database:
        raise SystemExit("项目配置 startup.database 不能为空")
    try:
        port = int(startup.get("port") or 8082)
    except (TypeError, ValueError) as exc:
        raise SystemExit("项目配置 startup.port 必须是整数") from exc
    if port < 1 or port > 65535:
        raise SystemExit("项目配置 startup.port 必须在 1 到 65535 之间")
    baseline_value = knowledge.get("baselineRoot")
    business_context_value = knowledge.get("businessContextRoot")
    baseline_exists = False
    material_value = business_context_value or baseline_value
    if material_value:
        baseline = Path(str(material_value)).expanduser()
        baseline = baseline if baseline.is_absolute() else path.resolve().parent / baseline
        baseline_exists = baseline.is_dir()
    result = {
        "database": database,
        "port": port,
        "baselineExists": baseline_exists,
    }
    if output_format == "tsv":
        print(f"{result['database']}\t{result['port']}\t{1 if result['baselineExists'] else 0}")
    else:
        print(json.dumps(result, ensure_ascii=False))


def _machine_name() -> str:
    value = (
        platform.machine().lower()
        .replace("amd64", "x86_64")
        .replace("x64", "x86_64")
        .replace("aarch64", "arm64")
    )
    return value or "unknown"


if __name__ == "__main__":
    main()
