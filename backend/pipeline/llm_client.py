"""Gemini structured-output wrapper: JSON schema, timeout, 2 retries (1 s / 2 s), token and cost logging.

Stages depend on the `LLMClient` protocol, so tests pass a scripted fake and never call the network.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Optional, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

RETRY_BACKOFF_SEC = (1.0, 2.0)


class LLMError(Exception):
    """The model did not return valid JSON for the schema after all retries, or timed out."""


@dataclass
class LLMResult(Generic[T]):  # noqa: UP046 - T is shared with the LLMClient protocol
    parsed: T
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    attempts: int
    raw_text: str


class LLMClient(Protocol):
    async def structured(self, *, stage: str, system: str, user: str, schema: type[T],
                         model: Optional[str] = None, timeout: Optional[float] = None) -> LLMResult[T]: ...


class GeminiClient:
    def __init__(self, settings: Any, db: Any = None, sleep: Callable[[float], Any] = asyncio.sleep):
        from google import genai  # imported lazily so replay-only deployments never load it

        self.settings, self.db, self.sleep = settings, db, sleep
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def _call(self, model: str, system: str, user: str, schema: type[BaseModel]) -> Any:
        from google.genai import types

        config = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                             response_schema=schema, temperature=0.0)
        return await self.client.aio.models.generate_content(model=model, contents=user, config=config)

    async def structured(self, *, stage: str, system: str, user: str, schema: type[T],
                         model: Optional[str] = None, timeout: Optional[float] = None) -> LLMResult[T]:
        model = model or self.settings.gemini_model
        timeout = timeout or self.settings.gemini_timeout_sec
        started = time.perf_counter()
        last: Exception | None = None
        for attempt in range(len(RETRY_BACKOFF_SEC) + 1):
            try:
                resp = await asyncio.wait_for(self._call(model, system, user, schema), timeout=timeout)
                text = resp.text or ""
                parsed = schema.model_validate_json(text)
                usage = getattr(resp, "usage_metadata", None)
                tin = int(getattr(usage, "prompt_token_count", 0) or 0)
                tout = int(getattr(usage, "candidates_token_count", 0) or 0)
                await self._record_spend(tin, tout)
                return LLMResult(parsed, model, tin, tout, int((time.perf_counter() - started) * 1000), attempt + 1, text)
            except (TimeoutError, ValidationError, json.JSONDecodeError) as exc:
                last = exc
            except Exception as exc:  # noqa: BLE001 - SDK transport errors vary by version
                last = exc
            if attempt < len(RETRY_BACKOFF_SEC):
                await self.sleep(RETRY_BACKOFF_SEC[attempt])
        raise LLMError(f"{stage}: {type(last).__name__}: {last}")

    async def _record_spend(self, tin: int, tout: int) -> None:
        if self.db is None:
            return
        cost = (tin * self.settings.llm_price_input_per_mtok_usd + tout * self.settings.llm_price_output_per_mtok_usd) / 1e6
        await self.db.add_llm_spend(cost, tin, tout)


class ScriptedLLM:
    """Test double: returns canned outputs per stage. A value may be a dict, a callable(user) -> dict, or an
    Exception instance to raise (to exercise fallbacks)."""

    def __init__(self, script: dict[str, Any]):
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def structured(self, *, stage: str, system: str, user: str, schema: type[T],
                         model: Optional[str] = None, timeout: Optional[float] = None) -> LLMResult[T]:
        self.calls.append({"stage": stage, "system": system, "user": user})
        out = self.script[stage]
        if isinstance(out, list):  # successive calls take successive entries
            out = out.pop(0) if len(out) > 1 else out[0]
        if isinstance(out, Exception):
            raise out
        if callable(out):
            out = out(user)
        parsed = schema.model_validate(out)
        return LLMResult(parsed, model or "scripted", len(user) // 4, 50, 1, 1, json.dumps(out))
