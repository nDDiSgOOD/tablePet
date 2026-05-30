"""仪表盘路由 / Dashboard route returning the HTML status page."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"


@router.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    return _DASHBOARD_PATH.read_text(encoding="utf-8")
