"""Agent 运行时 / The single public entrypoint.

外部代码（routers、usb handlers、websocket、未来的 mqtt …）只允许通过
``run_agent`` 与 agent 交互。这层薄封装：
  - 把 ``AgentInput`` 转成 langgraph state
  - 调 graph.ainvoke
  - 把终态映射回 ``AgentOutput``，并捕获顶层异常
"""

from __future__ import annotations

import time

from .contract import AgentInput, AgentOutput
from .graph import get_graph, initial_state, to_output


async def run_agent(payload: AgentInput) -> AgentOutput:
    """运行 agent / Execute the langgraph and return AgentOutput."""
    started = time.perf_counter()
    graph = get_graph()
    state = initial_state(payload)
    try:
        final = await graph.ainvoke(state)
    except Exception as exc:
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        return AgentOutput(
            ok=False,
            reply="",
            error=str(exc)[:500],
            timing_ms={"total": elapsed},
        )
    output = to_output(final)
    output.timing_ms.setdefault(
        "total", round((time.perf_counter() - started) * 1000, 2)
    )
    return output
