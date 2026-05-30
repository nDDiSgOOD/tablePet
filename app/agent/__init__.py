"""TablePet Agent 模块 / Unified agent entrypoint.

所有外部输入（web / wifi / usb）都收口到 ``run_agent``：

    from app.agent import run_agent, AgentInput, AgentOutput, Channel

Agent 内部用 langgraph 编排逻辑，节点实现留待业务方填充。
"""

from __future__ import annotations

from .contract import AgentInput, AgentOutput, Channel
from .runtime import run_agent

__all__ = ["AgentInput", "AgentOutput", "Channel", "run_agent"]
