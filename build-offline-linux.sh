#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_LAUNCHER="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_LAUNCHER="$(command -v python)"
else
  printf '[ERROR] 构建机需要 Python 3.11 或更高版本。\n' >&2
  exit 1
fi

exec "$PYTHON_LAUNCHER" "$PROJECT_ROOT/scripts/build_offline_bundle.py" --target linux "$@"
