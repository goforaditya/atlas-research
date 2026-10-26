"""Replay player: reads a .jsonl.gz fixture and yields StageEvents with speed-scaled delays (Section 10.3).

Recorded gaps g_i (g_1 = 300 ms) are clamped to [180, 950] ms and rescaled so the whole replay takes
T = 14,000 ms / speed: 14 s at 1x, 7 s at 2x, 2.8 s at 5x, and no delay for 'instant'.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from backend.schemas.events import StageEvent

TARGET_MS = 14_000
GAP_MIN_MS, GAP_MAX_MS, FIRST_GAP_MS = 180, 950, 300
Speed = Union[float, str]


class FixtureError(ValueError):
    pass


@dataclass
class Fixture:
    header: dict
    events: list[StageEvent]

    @property
    def replay_id(self) -> str:
        return self.header["replay_id"]


def load_fixture(path: Union[str, Path]) -> Fixture:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    if not lines:
        raise FixtureError(f"{path}: empty fixture")
    header = json.loads(lines[0])
    if not header.get("_meta"):
        raise FixtureError(f"{path}: line 1 is not a header")
    events = [StageEvent.model_validate_json(ln) for ln in lines[1:]]
    if header.get("total_events") != len(events):
        raise FixtureError(f"{path}: header says {header.get('total_events')} events, found {len(events)}")
    if [e.event_seq for e in events] != list(range(1, len(events) + 1)):
        raise FixtureError(f"{path}: event_seq is not 1..N")
    return Fixture(header, events)


def parse_speed(speed: Optional[Speed]) -> Optional[float]:
    """None means instant; otherwise a positive multiplier (1.0, 2.0, 5.0)."""
    if speed is None or (isinstance(speed, str) and speed.strip().lower() == "instant"):
        return None
    value = float(speed)
    if value <= 0:
        raise ValueError("speed must be positive")
    return value


def schedule_delays_ms(events: list[StageEvent], speed: Optional[Speed]) -> list[float]:
    """Delay before each event, in ms. Sums to TARGET_MS / speed (0 for instant)."""
    s = parse_speed(speed)
    if s is None or not events:
        return [0.0 for _ in events]
    gaps = [FIRST_GAP_MS] + [events[i].elapsed_ms - events[i - 1].elapsed_ms for i in range(1, len(events))]
    clamped = [min(max(g, GAP_MIN_MS), GAP_MAX_MS) for g in gaps]
    k = (TARGET_MS / s) / sum(clamped)
    return [g * k for g in clamped]


def rebind(event: StageEvent, run_id: str, started_at_ms: int) -> StageEvent:
    """Give a recorded event the new run's id and wall-clock timestamp; elapsed_ms keeps the recorded timing."""
    return event.model_copy(update={"run_id": run_id, "timestamp_ms": started_at_ms + event.elapsed_ms})


async def play(
    fixture: Fixture, run_id: str, speed: Optional[Speed] = 1.0, *, after_seq: int = 0,
    started_at_ms: Optional[int] = None,
) -> AsyncIterator[StageEvent]:
    """Yield events with event_seq > after_seq (reconnects resume without replaying earlier delays)."""
    started = started_at_ms if started_at_ms is not None else int(time.time() * 1000)
    delays = schedule_delays_ms(fixture.events, speed)
    for event, delay in zip(fixture.events, delays):
        if event.event_seq <= after_seq:
            continue
        if delay:
            await asyncio.sleep(delay / 1000)
        yield rebind(event, run_id, started)


def project_final_state(fixture: Fixture) -> dict:
    """Shallow-merge every state_patch (Section 4 merge rule)."""
    state: dict = {}
    for e in fixture.events:
        state.update(e.state_patch)
    return state
