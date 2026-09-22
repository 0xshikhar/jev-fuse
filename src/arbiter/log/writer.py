"""Asynchronous append-only decision log writer with SQLite WAL mode."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
import aiosqlite

from arbiter.log.models import DecisionRecord

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

INSERT_DECISION_SQL = """
INSERT INTO decisions (
    trace_id, timestamp_ns, timestamp_iso, task, client_id, provider,
    input_hash, input_preview, decision_value, raw_score, confidence,
    action, latency_ms, cached, human_label, human_labeled_at, context_json, reason
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trace_id) DO UPDATE SET
    human_label = COALESCE(excluded.human_label, decisions.human_label),
    human_labeled_at = COALESCE(excluded.human_labeled_at, decisions.human_labeled_at);
"""

UPDATE_LABEL_SQL = """
UPDATE decisions
SET human_label = ?, human_labeled_at = ?
WHERE trace_id = ?;
"""


class DecisionLogWriter:
    """Non-blocking, batched asynchronous SQLite decision log writer."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        flush_interval_ms: float = 50.0,
        batch_size: int = 100,
        queue_max_size: int = 10000,
    ):
        self._db_path = str(db_path)
        self._flush_interval = flush_interval_ms / 1000.0
        self._batch_size = batch_size
        self._queue: asyncio.Queue[DecisionRecord | None] = asyncio.Queue(maxsize=queue_max_size)
        self._db: aiosqlite.Connection | None = None
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._init_lock = asyncio.Lock()

    async def start(self) -> None:
        """Explicitly initialize DB and start the background writer loop."""
        await self._init_db()

    async def _init_db(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db

        async with self._init_lock:
            if self._db is not None:
                return self._db

            if self._db_path != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute("PRAGMA busy_timeout = 5000;")

            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
            await conn.executescript(schema_sql)
            await conn.commit()
            self._db = conn

            # Start background writer worker
            self._running = True
            self._worker_task = asyncio.create_task(self._worker_loop())
            return self._db

    async def _worker_loop(self) -> None:
        """Background coroutine that flushes queued records in transactions."""
        batch: list[DecisionRecord] = []
        last_flush = asyncio.get_event_loop().time()

        while self._running:
            try:
                # Wait for items with timeout
                timeout = max(0.005, self._flush_interval - (asyncio.get_event_loop().time() - last_flush))
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                    if item is None:
                        # Termination sentinel
                        self._queue.task_done()
                        break
                    batch.append(item)
                except asyncio.TimeoutError:
                    pass

                now = asyncio.get_event_loop().time()
                should_flush = bool(batch) and (
                    len(batch) >= self._batch_size
                    or self._queue.empty()
                    or (now - last_flush) >= self._flush_interval
                )

                if should_flush:
                    await self._write_batch(batch)
                    for _ in range(len(batch)):
                        self._queue.task_done()
                    batch.clear()
                    last_flush = now

            except asyncio.CancelledError:
                break
            except Exception as exc:
                import sys
                print(f"[Arbiter DecisionLogWriter Error] {exc}", file=sys.stderr)

        # Flush any remaining items before exiting
        if batch:
            await self._write_batch(batch)
            for _ in range(len(batch)):
                self._queue.task_done()
            batch.clear()

    async def _write_batch(self, records: Sequence[DecisionRecord]) -> None:
        if not records or self._db is None:
            return
        rows = [r.to_sql_tuple() for r in records]
        await self._db.executemany(INSERT_DECISION_SQL, rows)
        await self._db.commit()

    def log(self, record: DecisionRecord) -> None:
        """
        Non-blocking append of a decision record to the write queue.
        Guarantees <0.05ms latency on the hot request path.
        """
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            import sys
            print(f"[Arbiter Warning] Decision log queue is full! Dropping trace {record.trace_id}", file=sys.stderr)

    async def log_async(self, record: DecisionRecord) -> None:
        """Coroutine version waiting for space if queue is full."""
        await self._ensure_running()
        await self._queue.put(record)

    async def attach_label(self, trace_id: str, label: str) -> bool:
        """Attach a reviewed human feedback label to a previously recorded decision."""
        await self._ensure_running()
        now_iso = datetime.now(timezone.utc).isoformat()
        assert self._db is not None
        cursor = await self._db.execute(UPDATE_LABEL_SQL, (label, now_iso, trace_id))
        await self._db.commit()
        return cursor.rowcount > 0

    async def _ensure_running(self) -> None:
        if self._db is None:
            await self._init_db()

    async def flush(self) -> None:
        """Wait for all pending queued writes to be committed to disk."""
        await self._ensure_running()
        await self._queue.join()
        # Trigger an immediate commit of any active batch
        if self._db is not None:
            await self._db.commit()

    async def close(self) -> None:
        """Gracefully drain the write queue, terminate worker, and close connection."""
        if not self._running:
            return

        self._running = False
        await self._queue.put(None)  # Sentinel to wake up worker

        if self._worker_task is not None:
            await self._worker_task
            self._worker_task = None

        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "DecisionLogWriter":
        await self._init_db()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
