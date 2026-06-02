#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# 一键本地起 TablePet：同时启动 FastAPI 后端 + 灵动岛伴生 App
# 用法：./scripts/dev_all.sh
# 退出：Ctrl+C 会同时杀掉两个子进程
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT/island_app"
PORT="${TABLEPET_PORT:-8000}"
LOG_DIR="$ROOT/.cache/dev_logs"
mkdir -p "$LOG_DIR"

color()  { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
info()   { color '0;36' "[dev] $1"; }
ok()     { color '0;32' "[dev] $1"; }
warn()   { color '0;33' "[dev] $1"; }
fail()   { color '0;31' "[dev] $1"; }

PIDS=()
cleanup() {
    fail "shutting down…"
    # 先发 TERM，给孩子机会优雅退出，再补 KILL
    for pid in "${PIDS[@]:-}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    sleep 0.5
    for pid in "${PIDS[@]:-}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    exit 0
}
trap cleanup INT TERM EXIT

# ───── 1. 端口检测：占用就顺延 ─────
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
    warn "port $PORT in use, trying $((PORT + 1))…"
    PORT=$((PORT + 1))
done

# ───── 2. 激活 venv（如果存在）─────
cd "$ROOT"
if [[ -d ".venv" ]]; then
    # shellcheck disable=SC1091
    source ".venv/bin/activate"
    info "venv activated"
fi

# ───── 3. 后台启 FastAPI ─────
info "starting FastAPI on http://127.0.0.1:${PORT}"
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload \
    >"$LOG_DIR/api.log" 2>&1 &
API_PID=$!
PIDS+=("$API_PID")

# 等 API 健康检查通过（最多 15s）
info "waiting for API…"
for i in {1..30}; do
    if curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 \
       || curl -sf "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        ok "API is up (pid=$API_PID, log=$LOG_DIR/api.log)"
        break
    fi
    if ! kill -0 "$API_PID" 2>/dev/null; then
        fail "API exited prematurely. tail of log:"
        tail -n 30 "$LOG_DIR/api.log" || true
        exit 1
    fi
    sleep 0.5
done

# ───── 4. 编译并启动岛 App ─────
info "building island app (release)…"
(
    cd "$APP_DIR"
    swift build -c release --disable-sandbox >"$LOG_DIR/island_build.log" 2>&1
) || {
    fail "island build failed. last 30 lines:"
    tail -n 30 "$LOG_DIR/island_build.log" || true
    exit 1
}
ok "island built"

BIN="$APP_DIR/.build/release/TablePetIsland"
if [[ ! -x "$BIN" ]]; then
    fail "island binary missing: $BIN"
    exit 1
fi

info "launching island (api=http://127.0.0.1:${PORT})"
TABLEPET_API="http://127.0.0.1:${PORT}" "$BIN" \
    >"$LOG_DIR/island.log" 2>&1 &
ISLAND_PID=$!
PIDS+=("$ISLAND_PID")
ok "island running (pid=$ISLAND_PID, log=$LOG_DIR/island.log)"

cat <<EOF

──────────────────────────────────────────────
$(color '1;35' '🐱  TablePet dev environment ready')

  Dashboard      http://127.0.0.1:${PORT}
  API logs       $LOG_DIR/api.log
  Island logs    $LOG_DIR/island.log

  Ctrl+C 同时关闭后端和岛
──────────────────────────────────────────────

EOF

# ───── 5. 等任意一个子进程挂掉就整体退出 ─────
# 用 wait -n 等"任一"，wait -n 在 zsh / bash 4+ 都支持
while true; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        fail "API died, see $LOG_DIR/api.log"
        exit 1
    fi
    if ! kill -0 "$ISLAND_PID" 2>/dev/null; then
        fail "island died, see $LOG_DIR/island.log"
        exit 1
    fi
    sleep 1
done
