#!/usr/bin/env bash
# 仓库根目录只保留薄壳，离线启动实现见 scripts/launcher/start-offline-linux.sh。
# 部署包中这两个文件同样位于包根目录，实现位于 scripts/launcher/。
set -euo pipefail

exec "$(cd "$(dirname "$0")" && pwd)/scripts/launcher/start-offline-linux.sh" "$@"
