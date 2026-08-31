#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

PROJECT_CONFIG=""
DATABASE=""
HOST_ADDRESS="127.0.0.1"
PORT=""
DEMO=0
USE_MODEL=0

usage() {
  cat <<'EOF'
Business Code Agent - Linux offline launcher

Usage:
  ./start-offline-linux.sh [options]

Options:
  --project-config PATH   Offline project config (default: project.config.json)
  --database PATH         SQLite database (default: project startup.database)
  --host ADDRESS          Listen address (default: 127.0.0.1)
  --port PORT             Listen port (default: project startup.port)
  --demo                  Ignore project config and initialize the built-in demo
  --use-model             Allow baseline refresh to use configured internal model
  -h, --help              Show this help
EOF
}

fail() {
  printf '[ERROR] %s\n' "$1" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project-config) [ "$#" -ge 2 ] || fail "--project-config requires a path"; PROJECT_CONFIG="$2"; shift 2 ;;
    --database) [ "$#" -ge 2 ] || fail "--database requires a path"; DATABASE="$2"; shift 2 ;;
    --host) [ "$#" -ge 2 ] || fail "--host requires an address"; HOST_ADDRESS="$2"; shift 2 ;;
    --port) [ "$#" -ge 2 ] || fail "--port requires a number"; PORT="$2"; shift 2 ;;
    --demo) DEMO=1; shift ;;
    --use-model) USE_MODEL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

if command -v python3 >/dev/null 2>&1; then
  BOOTSTRAP_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  BOOTSTRAP_PYTHON="$(command -v python)"
else
  fail "Python was not found. Install the exact version recorded in offline/package-info.json"
fi

REQUIRED_PYTHON="$($BOOTSTRAP_PYTHON "$PROJECT_ROOT/scripts/offline_runtime.py" metadata \
  --metadata "$PROJECT_ROOT/offline/package-info.json" --field pythonMajorMinor)"
DEPENDENCY_MODE="$($BOOTSTRAP_PYTHON "$PROJECT_ROOT/scripts/offline_runtime.py" metadata \
  --metadata "$PROJECT_ROOT/offline/package-info.json" --field dependencyMode)"
REPOSITORY_MODE="$($BOOTSTRAP_PYTHON "$PROJECT_ROOT/scripts/offline_runtime.py" metadata \
  --metadata "$PROJECT_ROOT/offline/package-info.json" --field repositoryMode)"
PACKAGE_MODE="$($BOOTSTRAP_PYTHON "$PROJECT_ROOT/scripts/offline_runtime.py" metadata \
  --metadata "$PROJECT_ROOT/offline/package-info.json" --field mode)"
if [ -n "$REQUIRED_PYTHON" ] && command -v "python$REQUIRED_PYTHON" >/dev/null 2>&1; then
  PYTHON_LAUNCHER="$(command -v "python$REQUIRED_PYTHON")"
else
  PYTHON_LAUNCHER="$BOOTSTRAP_PYTHON"
fi

"$PYTHON_LAUNCHER" "$PROJECT_ROOT/scripts/offline_runtime.py" validate \
  --metadata "$PROJECT_ROOT/offline/package-info.json" --target linux

if [ "$REPOSITORY_MODE" = "intranet-git" ] && ! command -v git >/dev/null 2>&1; then
  fail "Git was not found. Intranet Git mode requires Git on the deployment machine"
fi

if [ "$DEMO" -eq 0 ]; then
  if [ -z "$PROJECT_CONFIG" ] && [ -f "$PROJECT_ROOT/project.config.json" ]; then
    PROJECT_CONFIG="$PROJECT_ROOT/project.config.json"
  fi
  if [ -z "$PROJECT_CONFIG" ]; then
    if [ "$PACKAGE_MODE" = "demo" ]; then
      DEMO=1
    else
      fail "project.config.json was not found. Copy project.config.example.json and configure the intranet Git repositories, or start with --demo"
    fi
  fi
fi

BASELINE_EXISTS=0
if [ "$DEMO" -eq 0 ]; then
  [ -f "$PROJECT_CONFIG" ] || fail "Project config does not exist: $PROJECT_CONFIG"
  DEFAULTS="$("$PYTHON_LAUNCHER" "$PROJECT_ROOT/scripts/offline_runtime.py" defaults --format tsv --config "$PROJECT_CONFIG")"
  IFS=$'\t' read -r CONFIG_DATABASE CONFIG_PORT BASELINE_EXISTS <<< "$DEFAULTS"
  [ -n "$DATABASE" ] || DATABASE="$CONFIG_DATABASE"
  [ -n "$PORT" ] || PORT="$CONFIG_PORT"
else
  [ -n "$DATABASE" ] || DATABASE=".data/knowledge.db"
  [ -n "$PORT" ] || PORT="8082"
fi

case "$PORT" in ''|*[!0-9]*) fail "--port must be an integer" ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "--port must be between 1 and 65535"

VENV_ROOT="$PROJECT_ROOT/.venv-offline"
VENV_PYTHON="$VENV_ROOT/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
  printf '\n==> Creating offline Python environment\n'
  "$PYTHON_LAUNCHER" -m venv "$VENV_ROOT"
fi

REQUIREMENTS_FILE="$PROJECT_ROOT/offline/requirements.txt"
INSTALLED_REQUIREMENTS="$VENV_ROOT/.requirements-installed.txt"
if [ ! -f "$INSTALLED_REQUIREMENTS" ] || ! cmp -s "$REQUIREMENTS_FILE" "$INSTALLED_REQUIREMENTS"; then
  if [ "$DEPENDENCY_MODE" = "bundled-wheels" ]; then
    printf '\n==> Installing dependencies from bundled wheelhouse\n'
    "$VENV_PYTHON" -m pip install --disable-pip-version-check --no-index \
      --find-links "$PROJECT_ROOT/offline/wheelhouse" \
      -r "$REQUIREMENTS_FILE"
  else
    printf '\n==> Installing dependencies from configured Python package index\n'
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$REQUIREMENTS_FILE"
  fi
  cp "$REQUIREMENTS_FILE" "$INSTALLED_REQUIREMENTS"
fi

case "$DATABASE" in /*) DATABASE_PATH="$DATABASE" ;; *) DATABASE_PATH="$PROJECT_ROOT/$DATABASE" ;; esac
mkdir -p "$(dirname "$DATABASE_PATH")"

printf '\n==> Preparing offline knowledge database\n'
if [ "$DEMO" -eq 1 ]; then
  "$VENV_PYTHON" -m business_code_agent.cli init-demo --db "$DATABASE_PATH"
else
  "$VENV_PYTHON" -m business_code_agent.cli init-db --db "$DATABASE_PATH"
  SYNC_ARGUMENTS=(-m business_code_agent.cli sync-project --config "$PROJECT_CONFIG" --db "$DATABASE_PATH")
  [ "$REPOSITORY_MODE" = "bundled-snapshot" ] && SYNC_ARGUMENTS+=(--offline)
  "$VENV_PYTHON" "${SYNC_ARGUMENTS[@]}"
  if [ "$BASELINE_EXISTS" -eq 1 ]; then
    BASELINE_ARGUMENTS=(-m business_code_agent.cli baseline-refresh --config "$PROJECT_CONFIG" --db "$DATABASE_PATH")
    [ "$USE_MODEL" -eq 1 ] || BASELINE_ARGUMENTS+=(--no-model)
    "$VENV_PYTHON" "${BASELINE_ARGUMENTS[@]}"
  fi
fi

URL_HOST="$HOST_ADDRESS"
[ "$URL_HOST" = "0.0.0.0" ] && URL_HOST="127.0.0.1"
printf '\nWorkbench is starting: http://%s:%s/\n' "$URL_HOST" "$PORT"
printf 'Press Control+C to stop the service.\n\n'

SERVER_ARGUMENTS=(-m business_code_agent.cli serve-query --db "$DATABASE_PATH" --host "$HOST_ADDRESS" --port "$PORT")
[ "$DEMO" -eq 1 ] || SERVER_ARGUMENTS+=(--project-config "$PROJECT_CONFIG")
exec "$VENV_PYTHON" "${SERVER_ARGUMENTS[@]}"
