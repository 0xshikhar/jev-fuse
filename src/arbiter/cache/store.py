"""Two-tier caching engine combining in-memory LRU with persistent SQLite storage."""

import asyncio
from collections import OrderedDict
import time
from pathlib import Path
from typing import Any
import aiosqlite

from arbiter.cache.base import CacheEntry, CacheStore
from arbiter.schema.internal import RawScore


class InMemoryLRUCache(CacheStore):
    """Tier 1: High-throughput, sub-microsecond in-memory LRU cache."""

    def __init__(self, max_size: int = 1024):
        self._max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, input_hash: str) -> CacheEntry | None:
        async with self._lock:
            entry = self._cache.get(input_hash)
            if entry is None:
                return None

            if entry.is_expired():
                del self._cache[input_hash]
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(input_hash)
            return entry

    async def set(
        self,
        input_hash: str,
        raw_score: RawScore,
        provider: str,
        ttl_seconds: float = 3600.0,
    ) -> None:
        now_ns = time.time_ns()
        expires_at_ns = now_ns + int(ttl_seconds * 1_000_000_000)
        entry = CacheEntry(
            input_hash=input_hash,
            raw_score=raw_score,
            provider=provider,
            created_at_ns=now_ns,
            expires_at_ns=expires_at_ns,
        )

        async with self._lock:
            if input_hash in self._cache:
                self._cache.move_to_end(input_hash)
            self._cache[input_hash] = entry

            # Evict oldest entry if size exceeded
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)

    async def delete(self, input_hash: str) -> None:
        async with self._lock:
            self._cache.pop(input_hash, None)

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


class SQLiteCacheStore(CacheStore):
    """Tier 2: Persistent local SQLite cache surviving process restarts."""

    CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS decision_cache (
        input_hash TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        raw_score_json TEXT NOT NULL,
        created_at_ns INTEGER NOT NULL,
        expires_at_ns INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_decision_cache_expires ON decision_cache(expires_at_ns);
    """

    def __init__(self, db_path: str | Path = ":memory:"):
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()

    async def _ensure_db(self) -> aiosqlite.Connection:
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
            await conn.executescript(self.CREATE_TABLE_SQL)
            await conn.commit()
            self._db = conn
            return self._db

    async def get(self, input_hash: str) -> CacheEntry | None:
        db = await self._ensure_db()
        now_ns = time.time_ns()
        query = """
        SELECT input_hash, provider, raw_score_json, created_at_ns, expires_at_ns
        FROM decision_cache
        WHERE input_hash = ? AND expires_at_ns > ?
        """
        async with db.execute(query, (input_hash, now_ns)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None

            raw_score = RawScore.model_validate_json(row["raw_score_json"])
            return CacheEntry(
                input_hash=row["input_hash"],
                raw_score=raw_score,
                provider=row["provider"],
                created_at_ns=row["created_at_ns"],
                expires_at_ns=row["expires_at_ns"],
            )

    async def set(
        self,
        input_hash: str,
        raw_score: RawScore,
        provider: str,
        ttl_seconds: float = 3600.0,
    ) -> None:
        db = await self._ensure_db()
        now_ns = time.time_ns()
        expires_at_ns = now_ns + int(ttl_seconds * 1_000_000_000)
        query = """
        INSERT INTO decision_cache (input_hash, provider, raw_score_json, created_at_ns, expires_at_ns)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(input_hash) DO UPDATE SET
            provider = excluded.provider,
            raw_score_json = excluded.raw_score_json,
            created_at_ns = excluded.created_at_ns,
            expires_at_ns = excluded.expires_at_ns
        """
        await db.execute(
            query,
            (
                input_hash,
                provider,
                raw_score.model_dump_json(),
                now_ns,
                expires_at_ns,
            ),
        )
        await db.commit()

    async def delete(self, input_hash: str) -> None:
        db = await self._ensure_db()
        await db.execute("DELETE FROM decision_cache WHERE input_hash = ?", (input_hash,))
        await db.commit()

    async def purge_expired(self) -> int:
        """Purge all expired records from SQLite cache and return deleted count."""
        db = await self._ensure_db()
        now_ns = time.time_ns()
        cursor = await db.execute("DELETE FROM decision_cache WHERE expires_at_ns <= ?", (now_ns,))
        await db.commit()
        return cursor.rowcount

    async def clear(self) -> None:
        db = await self._ensure_db()
        await db.execute("DELETE FROM decision_cache")
        await db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None


class CompositeCache(CacheStore):
    """
    Two-tier composite cache combining fast in-memory LRU with durable SQLite.
    
    Read path:
        1. Check memory (<1us). If hit, return.
        2. Check SQLite (<1ms). If hit, promote to memory and return.
        3. Return None.
    
    Write path:
        Store in both memory and SQLite.
    """

    def __init__(
        self,
        memory_max_size: int = 1024,
        sqlite_path: str | Path = ":memory:",
        default_ttl_seconds: float = 3600.0,
    ):
        self._memory = InMemoryLRUCache(max_size=memory_max_size)
        self._sqlite = SQLiteCacheStore(db_path=sqlite_path)
        self._default_ttl_seconds = default_ttl_seconds

    async def get(self, input_hash: str) -> CacheEntry | None:
        # Tier 1: In-memory LRU
        entry = await self._memory.get(input_hash)
        if entry is not None:
            return entry

        # Tier 2: Persistent SQLite
        entry = await self._sqlite.get(input_hash)
        if entry is not None:
            # Promote to Tier 1
            remaining_seconds = max(0.0, (entry.expires_at_ns - time.time_ns()) / 1_000_000_000)
            if remaining_seconds > 0:
                await self._memory.set(
                    input_hash=entry.input_hash,
                    raw_score=entry.raw_score,
                    provider=entry.provider,
                    ttl_seconds=remaining_seconds,
                )
            return entry

        return None

    async def set(
        self,
        input_hash: str,
        raw_score: RawScore,
        provider: str,
        ttl_seconds: float | None = None,
    ) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        await self._memory.set(input_hash, raw_score, provider, ttl)
        await self._sqlite.set(input_hash, raw_score, provider, ttl)

    async def delete(self, input_hash: str) -> None:
        await self._memory.delete(input_hash)
        await self._sqlite.delete(input_hash)

    async def purge_expired(self) -> int:
        return await self._sqlite.purge_expired()

    async def clear(self) -> None:
        await self._memory.clear()
        await self._sqlite.clear()

    async def close(self) -> None:
        await self._sqlite.close()
