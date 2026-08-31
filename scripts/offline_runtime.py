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
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    minimum = tuple(int(part) for part in str(payload.get("pythonMinimum") or "3.11").split("."))
    if sys.version_info[:2] < minimum:
        raise SystemExit(
            f"应用需要 Python {'.'.join(map(str, minimum))} 或更高版本，当前是 Python {current_python}。"
        )
    current_machine = _machine_name()
    if payload.get("dependencyMode") == "bundled-wheels":
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
    baseline_value = knowledge.get("baselineRoot") or knowledge.get("root")
    baseline_exists = False
    if baseline_value:
        baseline = Path(str(baseline_value)).expanduser()
        baseline = baseline if baseline.is_absolute() else path.resolve().parent / baseline
        baseline_exists = baseline.is_dir()
    result = {
        "database": startup.get("database") or ".data/knowledge.db",
        "port": int(startup.get("port") or 8082),
        "baselineExists": baseline_exists,
    }
    if output_format == "tsv":
        print(f"{result['database']}\t{result['port']}\t{1 if result['baselineExists'] else 0}")
    else:
        print(json.dumps(result, ensure_ascii=False))


def _machine_name() -> str:
    return platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")


if __name__ == "__main__":
    main()
