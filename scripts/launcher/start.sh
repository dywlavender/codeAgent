#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$project_root"
launcher="$project_root/scripts/launcher/start.py"

windows=0
default_python="$project_root/.venv/bin/python"
case "$(uname -s)" in
  MINGW*|MSYS*)
    windows=1
    default_python="$project_root/.venv-windows/Scripts/python.exe"
    launcher="$(cygpath -am -- "$launcher")"
    ;;
esac

# An explicit interpreter can bootstrap the launcher even before Python is on PATH.
requested_python=""
expect_python=0
for argument in "$@"; do
  if [ "$expect_python" -eq 1 ]; then
    requested_python="$argument"
    expect_python=0
    continue
  fi
  case "$argument" in
    --python) expect_python=1 ;;
    --python=*) requested_python="${argument#--python=}" ;;
    --) break ;;
  esac
done
if [ "$expect_python" -eq 1 ]; then
  printf 'Error: --python requires a path.\n' >&2
  exit 1
fi

if [ -n "$requested_python" ]; then
  if [ "$windows" -eq 1 ]; then
    requested_python="$(cygpath -au -- "$requested_python")"
  elif [[ "$requested_python" != /* ]]; then
    requested_python="$project_root/$requested_python"
  fi
  if [ ! -f "$requested_python" ] || [ ! -x "$requested_python" ]; then
    printf 'Error: Python is not executable: %s\n' "$requested_python" >&2
    exit 1
  fi
  bootstrap=("$requested_python")
elif [ -x "$default_python" ]; then
  bootstrap=("$default_python")
elif [ "$windows" -eq 1 ] && command -v py.exe >/dev/null 2>&1; then
  bootstrap=(py.exe -3)
elif [ "$windows" -eq 1 ] && command -v python.exe >/dev/null 2>&1; then
  bootstrap=(python.exe)
elif command -v python3 >/dev/null 2>&1; then
  bootstrap=(python3)
elif command -v python >/dev/null 2>&1; then
  bootstrap=(python)
else
  printf 'Error: Install Python 3.11 or later, or pass --python PATH.\n' >&2
  exit 1
fi

exec "${bootstrap[@]}" -X utf8 "$launcher" "$@"
