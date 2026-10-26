"""Async SQLite (WAL) persistence: runs, their events, and the daily LLM spend ledger."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    mode           TEXT NOT NULL,
    company_query  TEXT NOT NULL,
    replay_id      TEXT,
    status         TEXT NOT NULL,
    client_ip      TEXT,
    created_at_ms  INTEGER NOT NULL,
    completed_at_ms INTEGER,
    state_json     TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id     TEXT NOT NULL,
    event_seq  INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_seq)
);
CREATE TABLE IF NOT EXISTS llm_spend (
    day        TEXT PRIMARY KEY,
    cost_usd   REAL NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);
"""


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database not connected")
        return self._conn

    async def wal_enabled(self) -> bool:
        try:
            async with self.conn.execute("PRAGMA journal_mode") as cur:
                row = await cur.fetchone()
            return bool(row) and str(row[0]).lower() in ("wal", "memory")
        except Exception:
            return False

    # ---- runs
    async def insert_run(self, run_id: str, mode: str, company_query: str, replay_id: Optional[str],
                         client_ip: Optional[str]) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, mode, company_query, replay_id, status, client_ip, created_at_ms) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            (run_id, mode, company_query, replay_id, client_ip, int(time.time() * 1000)),
        )
        await self.conn.commit()

    async def append_event(self, run_id: str, event_seq: int, event_json: str) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO run_events (run_id, event_seq, event_json) VALUES (?, ?, ?)",
            (run_id, event_seq, event_json),
        )
        await self.conn.commit()

    async def finish_run(self, run_id: str, status: str, state: dict[str, Any]) -> None:
        await self.conn.execute(
            "UPDATE runs SET status = ?, completed_at_ms = ?, state_json = ? WHERE run_id = ?",
            (status, int(time.time() * 1000), json.dumps(state, ensure_ascii=False), run_id),
        )
        await self.conn.commit()

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT run_id, mode, company_query, replay_id, status, created_at_ms, completed_at_ms, state_json "
            "FROM runs WHERE run_id = ?", (run_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        keys = ["run_id", "mode", "company_query", "replay_id", "status", "created_at_ms", "completed_at_ms", "state_json"]
        return dict(zip(keys, row))

    async def get_events(self, run_id: str, after_seq: int = 0) -> list[str]:
        async with self.conn.execute(
            "SELECT event_json FROM run_events WHERE run_id = ? AND event_seq > ? ORDER BY event_seq",
            (run_id, after_seq),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    # ---- LLM budget
    async def add_llm_spend(self, cost_usd: float, input_tokens: int, output_tokens: int) -> None:
        await self.conn.execute(
            "INSERT INTO llm_spend (day, cost_usd, input_tokens, output_tokens) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET cost_usd = cost_usd + excluded.cost_usd, "
            "input_tokens = input_tokens + excluded.input_tokens, output_tokens = output_tokens + excluded.output_tokens",
            (_today(), cost_usd, input_tokens, output_tokens),
        )
        await self.conn.commit()

    async def llm_spend_today(self) -> float:
        async with self.conn.execute("SELECT cost_usd FROM llm_spend WHERE day = ?", (_today(),)) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0
