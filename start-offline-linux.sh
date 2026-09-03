#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

PROJECT_CONFIG=""
DATABASE=""
HOST_ADDRESS="127.0.0.1"
PORT=""
BASELINE_PARSER="model"

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
  --baseline-parser MODE  Baseline parser: model (default) or markdown
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
    --baseline-parser) [ "$#" -ge 2 ] || fail "--baseline-parser requires model or markdown"; BASELINE_PARSER="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "Unknown option: $1" ;;
  esac
done

case "$BASELINE_PARSER" in
  model|markdown) ;;
  *) fail "--baseline-parser must be model or markdown" ;;
esac

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

if [ -z "$PROJECT_CONFIG" ] && [ -f "$PROJECT_ROOT/project.config.json" ]; then
  PROJECT_CONFIG="$PROJECT_ROOT/project.config.json"
fi
[ -n "$PROJECT_CONFIG" ] || fail "project.config.json was not found. Copy project.config.example.json and configure the intranet Git repositories"

BASELINE_EXISTS=0
[ -f "$PROJECT_CONFIG" ] || fail "Project config does not exist: $PROJECT_CONFIG"
DEFAULTS="$("$PYTHON_LAUNCHER" "$PROJECT_ROOT/scripts/offline_runtime.py" defaults --format tsv --config "$PROJECT_CONFIG")"
IFS=$'\t' read -r CONFIG_DATABASE CONFIG_PORT BASELINE_EXISTS <<< "$DEFAULTS"
[ -n "$DATABASE" ] || DATABASE="$CONFIG_DATABASE"
[ -n "$PORT" ] || PORT="$CONFIG_PORT"

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
"$VENV_PYTHON" -m business_code_agent.cli init-db --db "$DATABASE_PATH"
SYNC_ARGUMENTS=(-m business_code_agent.cli sync-project --config "$PROJECT_CONFIG" --db "$DATABASE_PATH")
[ "$REPOSITORY_MODE" = "bundled-snapshot" ] && SYNC_ARGUMENTS+=(--offline)
"$VENV_PYTHON" "${SYNC_ARGUMENTS[@]}"
if [ "$BASELINE_EXISTS" -eq 1 ]; then
  BASELINE_ARGUMENTS=(-m business_code_agent.cli baseline-refresh --config "$PROJECT_CONFIG" --db "$DATABASE_PATH" --parser "$BASELINE_PARSER")
  "$VENV_PYTHON" "${BASELINE_ARGUMENTS[@]}"
fi

PROBE_HOST="$HOST_ADDRESS"
case "$PROBE_HOST" in
  0.0.0.0|::|'') PROBE_HOST="127.0.0.1" ;;
esac

port_is_open() {
  local target_port="$1"
  "$VENV_PYTHON" - "$PROBE_HOST" "$target_port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
for family, socktype, proto, _, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
    try:
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(address) == 0:
                raise SystemExit(0)
    except OSError:
        continue
raise SystemExit(1)
PY
}

port_owner_pids() {
  local target_port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$target_port" -sTCP:LISTEN -t 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$target_port" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$target_port" 2>/dev/null | tr ' ' '\n' | sed '/^$/d' || true
  fi
}

stop_port_processes() {
  local target_port="$1"
  local owners pid
  owners="$(port_owner_pids "$target_port")"
  [ -n "$owners" ] || fail "Port $target_port is busy, but its owning PID could not be determined"
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    printf '\n==> Stopping process listening on port %s (PID %s)\n' "$target_port" "$pid"
    kill "$pid" >/dev/null 2>&1 || true
  done < <(port_owner_pids "$target_port")
  for _ in $(seq 1 20); do
    if ! port_is_open "$target_port"; then
      return 0
    fi
    sleep 0.25
  done
  owners="$(port_owner_pids "$target_port")"
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    kill -9 "$pid" >/dev/null 2>&1 || true
  done <<< "$owners"
  port_is_open "$target_port" && fail "Port $target_port is still in use after stopping its processes"
}

if port_is_open "$PORT"; then
  printf '[WARN] Port %s is already in use; stopping the existing process and restarting.\n' "$PORT" >&2
  stop_port_processes "$PORT"
fi
port_is_open "$PORT" && fail "Port $PORT is still in use"

URL_HOST="$PROBE_HOST"
URL="http://$URL_HOST:$PORT/"
printf '\nWorkbench is starting: %s\n' "$URL"
printf 'Press Control+C to stop the service.\n\n'

SERVER_ARGUMENTS=(-m business_code_agent.cli serve-query --db "$DATABASE_PATH" --host "$HOST_ADDRESS" --port "$PORT")
SERVER_ARGUMENTS+=(--project-config "$PROJECT_CONFIG")
exec "$VENV_PYTHON" "${SERVER_ARGUMENTS[@]}"
