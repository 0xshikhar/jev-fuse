"""Unit and concurrency tests for Arbiter MicroBatcher."""

import asyncio
import time
import uuid
from typing import Sequence
import pytest

from arbiter.batch.batcher import MicroBatcher
from arbiter.cache.hasher import compute_input_hash
from arbiter.provider.base import ProviderHealth
from arbiter.schema.internal import NormalizedRequest, RawScore


def make_request(task: str = "test-task", text: str = "test-input", kind: str = "bool") -> NormalizedRequest:
    h = compute_input_hash(task=task, input_text=text)
    return NormalizedRequest(
        trace_id=str(uuid.uuid4()),
        task=task,
        kind=kind,  # type: ignore[arg-type]
        input=text,
        input_hash=h,
        timestamp_ns=time.time_ns(),
    )


class MockBatchProvider:
    """Mock provider recording batches and latencies."""

    def __init__(self, delay_ms: float = 5.0, fail: bool = False) -> None:
        self.delay_ms = delay_ms
        self.fail = fail
        self.batches_received: list[list[NormalizedRequest]] = []
        self._name = "mock-batch-provider"

    @property
    def name(self) -> str:
        return self._name

    async def infer(self, batch: Sequence[NormalizedRequest]) -> list[RawScore]:
        self.batches_received.append(list(batch))
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000.0)

        if self.fail:
            raise RuntimeError("Simulated provider inference failure")

        return [
            RawScore(
                value=True,
                raw_score=0.95,
                candidate_scores={"true": 0.95, "false": 0.05},
            )
            for _ in batch
        ]

    async def health(self) -> ProviderHealth:
        return ProviderHealth(status="healthy", latency_ms=1.0)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_solitary_request_flushes_on_timeout() -> None:
    provider = MockBatchProvider(delay_ms=2.0)
    batcher = MicroBatcher(
        provider=provider,
        window_floor_ms=10.0,
        window_ceiling_ms=30.0,
        max_batch_size=64,
    )
    await batcher.start()

    req = make_request(task="test-solitary", text="print(1)")

    t0 = time.monotonic()
    score = await batcher.enqueue(req)
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert score.raw_score == 0.95
    assert len(provider.batches_received) == 1
    assert len(provider.batches_received[0]) == 1
    # Solitary request took window_floor_ms (~10ms) + provider delay (~2ms)
    assert 8.0 <= elapsed_ms <= 100.0
    assert batcher.stats.total_requests == 1
    assert batcher.stats.total_batches == 1

    await batcher.close()


@pytest.mark.asyncio
async def test_burst_concurrency_coalescing() -> None:
    """Verify 50 concurrent requests coalesce into a small number of batches."""
    provider = MockBatchProvider(delay_ms=5.0)
    batcher = MicroBatcher(
        provider=provider,
        window_floor_ms=15.0,
        window_ceiling_ms=30.0,
        max_batch_size=64,
        adaptive=True,
    )
    await batcher.start()

    requests = [
        make_request(task="burst-task", text=f"code snippet {i}")
        for i in range(50)
    ]

    # Dispatch 50 requests concurrently
    scores = await asyncio.gather(*(batcher.enqueue(r) for r in requests))

    assert len(scores) == 50
    for s in scores:
        assert s.raw_score == 0.95

    # Should have coalesced 50 requests into only 1 or 2 batches!
    assert len(provider.batches_received) <= 3
    assert batcher.stats.total_requests == 50
    assert batcher.stats.avg_batch_size >= 15.0

    await batcher.close()


@pytest.mark.asyncio
async def test_immediate_flush_on_max_batch_size() -> None:
    """Verify hitting max_batch_size flushes immediately without waiting for window."""
    provider = MockBatchProvider(delay_ms=2.0)
    # Set a large window timeout (200ms) but small max_batch_size (5)
    batcher = MicroBatcher(
        provider=provider,
        window_floor_ms=200.0,
        window_ceiling_ms=300.0,
        max_batch_size=5,
    )
    await batcher.start()

    requests = [
        make_request(task="cap-task", text=f"item {i}")
        for i in range(5)
    ]

    t0 = time.monotonic()
    scores = await asyncio.gather(*(batcher.enqueue(r) for r in requests))
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    assert len(scores) == 5
    # Flushed immediately on reaching 5 items, way faster than the 200ms window!
    assert elapsed_ms < 100.0
    assert batcher.stats.flushes_by_max_size >= 1

    await batcher.close()


@pytest.mark.asyncio
async def test_provider_error_propagates_to_all_futures() -> None:
    """Verify that an inference failure rejects all futures in that batch."""
    provider = MockBatchProvider(delay_ms=2.0, fail=True)
    batcher = MicroBatcher(
        provider=provider,
        window_floor_ms=5.0,
        max_batch_size=10,
    )
    await batcher.start()

    requests = [
        make_request(task="err-task", text=f"item {i}")
        for i in range(3)
    ]

    results = await asyncio.gather(
        *(batcher.enqueue(r) for r in requests),
        return_exceptions=True,
    )

    assert len(results) == 3
    for res in results:
        assert isinstance(res, RuntimeError)
        assert "Simulated provider inference failure" in str(res)

    await batcher.close()


@pytest.mark.asyncio
async def test_clean_shutdown_and_drain() -> None:
    provider = MockBatchProvider(delay_ms=5.0)
    batcher = MicroBatcher(provider=provider, window_floor_ms=50.0)
    await batcher.start()

    req = make_request(task="drain-1", text="x")
    task1 = asyncio.create_task(batcher.enqueue(req))

    # Allow enqueue to register
    await asyncio.sleep(0.005)
    # Close immediately while item is pending
    await batcher.close()

    res = await task1
    assert res.raw_score == 0.95
    assert batcher.stats.current_queue_depth == 0
