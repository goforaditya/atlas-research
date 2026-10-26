"""Exports the compiled LangGraph runner.

`run_live_pipeline` is implemented in Task 14; until then live runs are refused with a clear error and
the replays remain the guaranteed demo path.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

LIVE_PIPELINE_READY = False


async def run_live_pipeline(run_id: str, company_query: str, on_event: Callable[[Any], Awaitable[None]],
                            settings: Any, db: Any) -> None:
    raise NotImplementedError("Live pipeline is built in Tasks 12-14")
