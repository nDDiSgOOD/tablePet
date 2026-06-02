#!/usr/bin/env bash
# 启动 TablePet Island 伴生 App
# 用法：./scripts/run_island.sh [API_BASE_URL]
# 例：  ./scripts/run_island.sh http://127.0.0.1:8000
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT/island_app"
API_URL="${1:-http://127.0.0.1:8000}"

cd "$APP_DIR"
echo "[island] building (release)…"
# --disable-sandbox 兼容部分 macOS 终端环境（如某些受限会话），
# 在普通用户终端下加这个参数也是安全的。
swift build -c release --disable-sandbox

BIN="$APP_DIR/.build/release/TablePetIsland"
if [[ ! -x "$BIN" ]]; then
  echo "[island] 构建失败：$BIN 不存在" >&2
  exit 1
fi

echo "[island] launching against $API_URL"
TABLEPET_API="$API_URL" "$BIN"
