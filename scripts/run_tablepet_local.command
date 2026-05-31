#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -d ".venv" ]]; then
  source ".venv/bin/activate"
fi

PORT="${TABLEPET_PORT:-8000}"
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

echo "Starting TablePet from Terminal..."
echo "Dashboard: http://localhost:${PORT}"
echo ""
echo "如果要让桌宠控制 QQ 音乐/网易云，请在 macOS「系统设置 -> 隐私与安全性 -> 辅助功能」里允许 Terminal。"
echo "如果 8000 已被其他服务占用，脚本会自动顺延到下一个可用端口。"
echo ""

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
