#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -d ".venv" ]]; then
  source ".venv/bin/activate"
fi

echo "Starting TablePet from Terminal..."
echo "Dashboard: http://localhost:8000"
echo ""
echo "如果要让桌宠控制 QQ 音乐/网易云，请在 macOS「系统设置 -> 隐私与安全性 -> 辅助功能」里允许 Terminal。"
echo ""

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
