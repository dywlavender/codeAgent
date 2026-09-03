#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

DEFAULT_PROJECT_CONFIG="$PROJECT_ROOT/project.config.json"
MODE="Empty"
MODE_EXPLICIT=0
DATABASE=".data/knowledge.db"
DATABASE_EXPLICIT=0
REPOSITORY=""
REPOSITORY_ID="repo-main"
PROJECT_CONFIG=""
HOST_ADDRESS="127.0.0.1"
PORT="8082"
PORT_EXPLICIT=0
SKIP_INSTALL=0
SKIP_FRONTEND_BUILD=0
NO_BROWSER=0

usage() {
  cat <<'EOF'
Business Code Agent - macOS launcher

Usage:
  ./start-mac.sh [options]

Options:
  --mode Empty|Repository        Startup mode (default: Empty)
  --database PATH               SQLite database (default: project startup.database or .data/knowledge.db)
  --repository PATH             Java/MyBatis repository for Repository mode
  --repository-id ID            Repository identifier (default: repo-main)
  --project-config PATH         Override the default project.config.json path
  --host ADDRESS                Listen address (default: 127.0.0.1)
  --port PORT                   Listen port (default: project startup.port or 8082)
  --skip-install                Reuse the installed Python environment
  --skip-frontend-build         Reuse frontend/dist
  --no-browser                  Do not open the browser automatically
  -h, --help                    Show this help

Examples:
  ./start-mac.sh --project-config project.config.json
  ./start-mac.sh --mode Empty
  ./start-mac.sh --mode Repository --repository "/Users/me/IdeaProjects/loan-system"
  ./start-mac.sh                # Uses project.config.json when it exists
  ./start-mac.sh --project-config configs/another-project.json
  ./start-mac.sh --skip-install --skip-frontend-build
EOF
}

fail() {
  printf '\n[ERROR] %s\n' "$1" >&2
  exit 1
}

step() {
  printf '\n\033[36m==> %s\033[0m\n' "$1"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      [ "$#" -ge 2 ] || fail "--mode requires a value"
      MODE="$2"
      MODE_EXPLICIT=1
      shift 2
      ;;
    --database)
      [ "$#" -ge 2 ] || fail "--database requires a path"
      DATABASE="$2"
      DATABASE_EXPLICIT=1
      shift 2
      ;;
    --repository)
      [ "$#" -ge 2 ] || fail "--repository requires a path"
      REPOSITORY="$2"
      shift 2
      ;;
    --repository-id)
      [ "$#" -ge 2 ] || fail "--repository-id requires a value"
      REPOSITORY_ID="$2"
      shift 2
      ;;
    --project-config)
      [ "$#" -ge 2 ] || fail "--project-config requires a path"
      PROJECT_CONFIG="$2"
      shift 2
      ;;
    --host)
      [ "$#" -ge 2 ] || fail "--host requires an address"
      HOST_ADDRESS="$2"
      shift 2
      ;;
    --port)
      [ "$#" -ge 2 ] || fail "--port requires a number"
      PORT="$2"
      PORT_EXPLICIT=1
      shift 2
      ;;
    --skip-install)
      SKIP_INSTALL=1
      shift
      ;;
    --skip-frontend-build)
      SKIP_FRONTEND_BUILD=1
      shift
      ;;
    --no-browser)
      NO_BROWSER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1 (run ./start-mac.sh --help)"
      ;;
  esac
done

if [ -z "$PROJECT_CONFIG" ] && [ "$MODE_EXPLICIT" -eq 0 ] && [ -z "$REPOSITORY" ] && [ -f "$DEFAULT_PROJECT_CONFIG" ]; then
  PROJECT_CONFIG="$DEFAULT_PROJECT_CONFIG"
fi

case "$MODE" in
  Empty|empty) MODE="Empty" ;;
  Repository|repository) MODE="Repository" ;;
  *) fail "--mode must be Empty or Repository" ;;
esac

if command -v python3 >/dev/null 2>&1; then
  PYTHON_LAUNCHER="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_LAUNCHER="$(command -v python)"
else
  fail "Python 3.11+ was not found. Install it with Homebrew: brew install python"
fi

if [ -n "$PROJECT_CONFIG" ]; then
  [ -f "$PROJECT_CONFIG" ] || fail "Project config does not exist: $PROJECT_CONFIG"
  PROJECT_RUNTIME_DEFAULTS="$("$PYTHON_LAUNCHER" - "$PROJECT_CONFIG" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"项目配置无法读取: {exc}")

startup = payload.get("startup") or {}
if not isinstance(startup, dict):
    raise SystemExit("项目配置 startup 必须是对象")

database = startup.get("database", "")
port = startup.get("port", "")
if database and not isinstance(database, str):
    raise SystemExit("项目配置 startup.database 必须是字符串")
if port not in ("", None):
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise SystemExit("项目配置 startup.port 必须是整数")
    if not 1 <= port <= 65535:
        raise SystemExit("项目配置 startup.port 必须在 1 到 65535 之间")
else:
    port = ""
print(f"{database}\t{port}")
PY
  )" || fail "无法读取项目配置中的 startup 设置"
  IFS=$'\t' read -r CONFIG_DATABASE CONFIG_PORT <<< "$PROJECT_RUNTIME_DEFAULTS"
  if [ "$DATABASE_EXPLICIT" -eq 0 ] && [ -n "$CONFIG_DATABASE" ]; then
    DATABASE="$CONFIG_DATABASE"
  fi
  if [ "$PORT_EXPLICIT" -eq 0 ] && [ -n "$CONFIG_PORT" ]; then
    PORT="$CONFIG_PORT"
  fi
fi

case "$PORT" in
  ''|*[!0-9]*) fail "--port must be an integer between 1 and 65535" ;;
esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "--port must be between 1 and 65535"

step "Checking Python"
"$PYTHON_LAUNCHER" -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'; print(sys.version.split()[0])" \
  || fail "Python 3.11 or later is required"

VENV_ROOT="$PROJECT_ROOT/.venv"
VENV_PYTHON="$VENV_ROOT/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
  step "Creating Python virtual environment"
  "$PYTHON_LAUNCHER" -m venv "$VENV_ROOT"
fi

if [ "$SKIP_INSTALL" -eq 0 ]; then
  step "Installing backend"
  if ! "$VENV_PYTHON" -m pip install --disable-pip-version-check -e ".[tree-sitter]"; then
    printf '\n[WARN] Optional Tree-sitter adapter could not be installed; continuing without it.\n' >&2
    "$VENV_PYTHON" -m pip install --disable-pip-version-check -e .
  fi
elif ! "$VENV_PYTHON" -c "import business_code_agent" >/dev/null 2>&1; then
  fail "--skip-install was used, but the backend is not installed in .venv"
fi

if [ "$SKIP_FRONTEND_BUILD" -eq 0 ]; then
  step "Building the Agent workbench"
  command -v node >/dev/null 2>&1 || fail "Node.js 20+ was not found. Install it with Homebrew: brew install node"
  command -v npm >/dev/null 2>&1 || fail "npm was not found. Reinstall Node.js"
  node -e "const major=Number(process.versions.node.split('.')[0]); if(major<20){console.error('Node.js 20+ required'); process.exit(1)}" \
    || fail "Node.js 20 or later is required"
  (
    cd "$PROJECT_ROOT/frontend"
    npm ci --no-audit --no-fund
    npm run build
  )
elif [ ! -f "$PROJECT_ROOT/frontend/dist/index.html" ]; then
  fail "--skip-frontend-build was used, but frontend/dist does not exist"
fi

case "$DATABASE" in
  /*) DATABASE_PATH="$DATABASE" ;;
  *) DATABASE_PATH="$PROJECT_ROOT/$DATABASE" ;;
esac
DATA_DIRECTORY="$(dirname "$DATABASE_PATH")"
mkdir -p "$DATA_DIRECTORY"

step "Preparing knowledge database"
if [ -n "$PROJECT_CONFIG" ]; then
  [ -f "$PROJECT_CONFIG" ] || fail "Project config does not exist: $PROJECT_CONFIG"
  "$VENV_PYTHON" -m business_code_agent.cli init-db --db "$DATABASE_PATH"
  "$VENV_PYTHON" -m business_code_agent.cli sync-project --config "$PROJECT_CONFIG" --db "$DATABASE_PATH"
else
  case "$MODE" in
    Empty)
      "$VENV_PYTHON" -m business_code_agent.cli init-db --db "$DATABASE_PATH"
      ;;
    Repository)
      [ -n "$REPOSITORY" ] || fail "Repository mode requires --repository /path/to/java-project"
      [ -d "$REPOSITORY" ] || fail "Repository directory does not exist: $REPOSITORY"
      "$VENV_PYTHON" -m business_code_agent.cli init-db --db "$DATABASE_PATH"
      "$VENV_PYTHON" -m business_code_agent.cli ingest-repo "$REPOSITORY" \
        --repository-id "$REPOSITORY_ID" --db "$DATABASE_PATH"
      ;;
  esac
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
  fi
}

stop_port_processes() {
  local target_port="$1"
  local owners pid
  owners="$(port_owner_pids "$target_port")"
  [ -n "$owners" ] || fail "Port $target_port is busy, but its owning PID could not be determined"
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    step "Stopping process listening on port $target_port (PID $pid)"
    kill "$pid" >/dev/null 2>&1 || true
  done <<< "$owners"
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

LOG_PATH="$DATA_DIRECTORY/server.log"
ERROR_LOG_PATH="$DATA_DIRECTORY/server-error.log"
: > "$LOG_PATH"
: > "$ERROR_LOG_PATH"

step "Starting workbench"
SERVER_ARGUMENTS=(-m business_code_agent.cli serve-query --db "$DATABASE_PATH" --host "$HOST_ADDRESS" --port "$PORT")
if [ -n "$PROJECT_CONFIG" ]; then
  SERVER_ARGUMENTS+=(--project-config "$PROJECT_CONFIG")
fi
"$VENV_PYTHON" "${SERVER_ARGUMENTS[@]}" >>"$LOG_PATH" 2>>"$ERROR_LOG_PATH" &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 40 ]; do
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    printf '\nServer stopped during startup.\n' >&2
    [ -s "$ERROR_LOG_PATH" ] && tail -n 30 "$ERROR_LOG_PATH" >&2
    exit 1
  fi
  if curl --silent --fail --max-time 1 "$URL_HOST:$PORT/api/workspace" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 0.25
  ATTEMPT=$((ATTEMPT + 1))
done

[ "$READY" -eq 1 ] || fail "Server did not become ready within 10 seconds. See $ERROR_LOG_PATH"

printf '\n\033[32mWorkbench is ready: %s\033[0m\n' "$URL"
printf 'Database: %s\nLogs: %s\nPress Control+C to stop the service.\n\n' "$DATABASE_PATH" "$LOG_PATH"

if [ "$NO_BROWSER" -eq 0 ]; then
  open "$URL" >/dev/null 2>&1 || printf '[WARN] Could not open the browser automatically. Open %s manually.\n' "$URL" >&2
fi

wait "$SERVER_PID"
