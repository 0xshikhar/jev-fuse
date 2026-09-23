"""Unit tests for hashing, in-memory LRU, persistent SQLite cache, and composite two-tier cache."""

import asyncio
from pathlib import Path

import pytest

from jevfuse.cache import (
    CompositeCache,
    InMemoryLRUCache,
    SQLiteCacheStore,
    canonicalize_text,
    compute_input_hash,
    hash_normalized_request,
)
from jevfuse.schema import DecisionKind, NormalizedRequest, RawScore


def test_canonicalize_text():
    assert canonicalize_text("hello\r\nworld") == "hello\nworld"
    # Unicode composed vs decomposed
    decomposed = "e\u0301"  # e + acute accent
    composed = "\u00e9"     # é
    assert canonicalize_text(decomposed) == canonicalize_text(composed)


def test_compute_input_hash_determinism():
    h1 = compute_input_hash("code-risk", "git push --force", ["allow", "deny"])
    h2 = compute_input_hash("CODE-RISK ", "git push --force\r\n", ["allow", "deny"])
    assert h1 == h2
    assert len(h1) == 64

    # Different choice order or choices yields different hash
    h3 = compute_input_hash("code-risk", "git push --force", ["deny", "allow"])
    assert h1 != h3

    # Idempotency key overrides input body
    h_idemp1 = compute_input_hash("code-risk", "input A", idempotency_key="key_123")
    h_idemp2 = compute_input_hash("code-risk", "input B", idempotency_key="key_123")
    assert h_idemp1 == h_idemp2


def test_hash_normalized_request():
    req = NormalizedRequest(
        trace_id="tr_1",
        task="element-select",
        kind=DecisionKind.CHOICE,
        input="Click login",
        choices=("#btn-login", "#btn-cancel"),
        client_id="browser",
        provider="jev",
        input_hash="dummy",
        timestamp_ns=1000,
    )
    h = hash_normalized_request(req)
    assert len(h) == 64
    assert h == compute_input_hash("element-select", "Click login", ["#btn-login", "#btn-cancel"])


@pytest.mark.asyncio
async def test_in_memory_lru_cache_hit_miss():
    cache = InMemoryLRUCache(max_size=10)
    raw = RawScore(value="safe", raw_score=0.95, candidate_scores={"safe": 0.95})

    assert await cache.get("hash_1") is None

    await cache.set("hash_1", raw, provider="jev", ttl_seconds=60)
    entry = await cache.get("hash_1")
    assert entry is not None
    assert entry.raw_score.value == "safe"
    assert entry.provider == "jev"
    assert not entry.is_expired()


@pytest.mark.asyncio
async def test_in_memory_lru_cache_eviction():
    cache = InMemoryLRUCache(max_size=2)
    raw = RawScore(value="val", raw_score=0.9)

    await cache.set("h1", raw, provider="jev")
    await cache.set("h2", raw, provider="jev")
    assert len(cache) == 2

    # Access h1 so h2 becomes oldest
    assert await cache.get("h1") is not None

    # Insert h3 -> h2 should be evicted
    await cache.set("h3", raw, provider="jev")
    assert len(cache) == 2
    assert await cache.get("h2") is None
    assert await cache.get("h1") is not None
    assert await cache.get("h3") is not None


@pytest.mark.asyncio
async def test_in_memory_lru_cache_expiry():
    cache = InMemoryLRUCache(max_size=10)
    raw = RawScore(value="val", raw_score=0.9)

    await cache.set("h_exp", raw, provider="jev", ttl_seconds=0.05)
    assert await cache.get("h_exp") is not None

    await asyncio.sleep(0.06)
    assert await cache.get("h_exp") is None


@pytest.mark.asyncio
async def test_sqlite_cache_persistence(tmp_path: Path):
    db_file = tmp_path / "cache.db"
    store1 = SQLiteCacheStore(db_path=db_file)
    raw = RawScore(value=True, raw_score=0.88)

    await store1.set("hash_persistent", raw, provider="laya-mlx", ttl_seconds=300)
    await store1.close()

    # Reconnect with new store instance to test disk persistence
    store2 = SQLiteCacheStore(db_path=db_file)
    entry = await store2.get("hash_persistent")
    assert entry is not None
    assert entry.raw_score.value is True
    assert entry.provider == "laya-mlx"
    await store2.close()


@pytest.mark.asyncio
async def test_sqlite_cache_purge_expired():
    store = SQLiteCacheStore(db_path=":memory:")
    raw = RawScore(value="val", raw_score=0.5)

    # 1. Store already expired item (ttl = -1s)
    await store.set("expired_key", raw, provider="jev", ttl_seconds=-1.0)
    # 2. Store valid item (ttl = 60s)
    await store.set("valid_key", raw, provider="jev", ttl_seconds=60.0)

    # Get on expired should be None
    assert await store.get("expired_key") is None
    assert await store.get("valid_key") is not None

    # Purge
    deleted = await store.purge_expired()
    assert deleted >= 1
    assert await store.get("valid_key") is not None
    await store.close()


@pytest.mark.asyncio
async def test_composite_cache_two_tier_promotion():
    composite = CompositeCache(memory_max_size=5, sqlite_path=":memory:", default_ttl_seconds=100)
    raw = RawScore(value="truncate", raw_score=0.84, candidate_scores={"keep": 0.1, "truncate": 0.84})

    await composite.set("hash_comp", raw, provider="jev")

    # Entry is in memory and sqlite
    entry1 = await composite.get("hash_comp")
    assert entry1 is not None
    assert entry1.raw_score.value == "truncate"

    # Clear memory tier specifically
    await composite._memory.clear()
    assert len(composite._memory) == 0

    # Composite get should hit SQLite tier and promote back into memory tier
    entry2 = await composite.get("hash_comp")
    assert entry2 is not None
    assert entry2.raw_score.value == "truncate"
    assert len(composite._memory) == 1  # Promoted back to Tier 1

    # Delete clears both tiers
    await composite.delete("hash_comp")
    assert await composite.get("hash_comp") is None
    await composite.close()
