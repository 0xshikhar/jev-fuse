"""Adaptive cross-request micro-batcher for Arbiter decision providers.

Coalesces individual concurrent decision requests into optimal batches for
the underlying inference engine (hosted HTTP/2 or local matrix multiplication),
balancing solitary latency with high-throughput amortized batching.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Sequence

from arbiter.provider.base import DecisionProvider
from arbiter.schema.internal import NormalizedRequest, RawScore

logger = logging.getLogger(__name__)


@dataclass
class BatchedItem:
    """An enqueued decision request waiting for batch execution."""
    request: NormalizedRequest
    future: asyncio.Future[RawScore]
    enqueued_at: float = field(default_factory=time.monotonic)


@dataclass
class BatcherStats:
    """Real-time throughput and queue telemetry."""
    total_requests: int = 0
    total_batches: int = 0
    flushes_by_max_size: int = 0
    flushes_by_timeout: int = 0
    current_window_ms: float = 8.0
    current_queue_depth: int = 0

    @property
    def avg_batch_size(self) -> float:
        if self.total_batches == 0:
            return 0.0
        return self.total_requests / self.total_batches


class MicroBatcher:
    """Asynchronous adaptive micro-batcher for a DecisionProvider.
    
    Attributes:
        provider: The inference provider to call with coalesced batches.
        window_floor_ms: Minimum accumulation delay in ms (default 8.0ms).
        window_ceiling_ms: Maximum accumulation delay under burst load in ms (default 25.0ms).
        max_batch_size: Maximum number of items in a single batch (default 64).
        adaptive: Whether to stretch the accumulation window based on queue velocity.
    """

    def __init__(
        self,
        provider: DecisionProvider,
        window_floor_ms: float = 8.0,
        window_ceiling_ms: float = 25.0,
        max_batch_size: int = 64,
        adaptive: bool = True,
    ) -> None:
        self.provider = provider
        self.window_floor_ms = window_floor_ms
        self.window_ceiling_ms = window_ceiling_ms
        self.max_batch_size = max_batch_size
        self.adaptive = adaptive

        self._queue: list[BatchedItem] = []
        self._lock = asyncio.Lock()
        self._flush_trigger = asyncio.Event()
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None
        self._current_window_ms = window_floor_ms
        self.stats = BatcherStats(current_window_ms=window_floor_ms)

    async def start(self) -> None:
        """Start the background batch processing loop."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name=f"arbiter-batcher-{self.provider.name}",
        )

    async def enqueue(self, request: NormalizedRequest) -> RawScore:
        """Enqueue a single normalized request and await its batched result.
        
        Args:
            request: The normalized decision request to execute.
            
        Returns:
            The raw score produced by the underlying provider.
        """
        if not self._running:
            await self.start()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[RawScore] = loop.create_future()
        item = BatchedItem(request=request, future=future)

        async with self._lock:
            self._queue.append(item)
            q_len = len(self._queue)
            self.stats.current_queue_depth = q_len

            # Immediate flush trigger if we hit max batch capacity
            if q_len >= self.max_batch_size:
                self._flush_trigger.set()

        return await future

    async def flush(self) -> None:
        """Force an immediate flush of all currently queued items."""
        async with self._lock:
            if self._queue:
                self._flush_trigger.set()
        # Allow worker loop to run
        await asyncio.sleep(0)

    async def close(self) -> None:
        """Gracefully drain any pending requests and terminate the worker loop."""
        self._running = False
        self._flush_trigger.set()

        if self._worker_task and not self._worker_task.done():
            # Wait for remaining queue to drain
            try:
                await asyncio.wait_for(self._worker_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass

        # If any items remained unfulfilled, fail them cleanly
        async with self._lock:
            for item in self._queue:
                if not item.future.done():
                    item.future.set_exception(
                        RuntimeError(f"MicroBatcher for {self.provider.name} closed with pending items")
                    )
            self._queue.clear()
            self.stats.current_queue_depth = 0

    async def _worker_loop(self) -> None:
        """Main loop: waits on flush window or capacity trigger, then drains batch."""
        while self._running or self._has_pending_items():
            try:
                # Wait for either an item to arrive or timeout
                await self._wait_for_window()
                
                # Extract batch
                batch_to_run: list[BatchedItem] = []
                flush_reason = "timeout"

                async with self._lock:
                    if not self._queue:
                        self._flush_trigger.clear()
                        continue

                    if len(self._queue) >= self.max_batch_size:
                        flush_reason = "max_size"
                        self.stats.flushes_by_max_size += 1
                    else:
                        self.stats.flushes_by_timeout += 1

                    # Take up to max_batch_size
                    batch_to_run = self._queue[: self.max_batch_size]
                    self._queue = self._queue[self.max_batch_size :]
                    self.stats.current_queue_depth = len(self._queue)
                    self._flush_trigger.clear()

                    # Adapt window based on queue pressure
                    if self.adaptive:
                        self._adapt_window(len(batch_to_run), len(self._queue))

                if batch_to_run:
                    await self._execute_batch(batch_to_run)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Unexpected error in MicroBatcher worker loop: %s", e)
                await asyncio.sleep(0.01)

    def _has_pending_items(self) -> bool:
        return len(self._queue) > 0

    async def _wait_for_window(self) -> None:
        """Wait for the accumulation window duration or an immediate capacity trigger."""
        delay_sec = self._current_window_ms / 1000.0
        try:
            # If trigger is already set (e.g. queue hit max_batch_size), don't sleep
            if self._flush_trigger.is_set():
                return
            await asyncio.wait_for(self._flush_trigger.wait(), timeout=delay_sec)
        except asyncio.TimeoutError:
            pass

    def _adapt_window(self, processed_count: int, remaining_count: int) -> None:
        """Adjust accumulation window dynamically.
        
        If we are seeing bursts (many items processed and items still waiting in queue),
        we stretch the window towards window_ceiling_ms to maximize batch parallelism.
        If the queue is draining to zero, we compress back towards window_floor_ms to
        minimize latency for solitary requests.
        """
        if remaining_count > 0 or processed_count >= (self.max_batch_size // 2):
            # Queue is under pressure: stretch window to batch more aggressively
            self._current_window_ms = min(
                self.window_ceiling_ms,
                self._current_window_ms * 1.25,
            )
        else:
            # Light traffic: tighten window to deliver sub-10ms response times
            self._current_window_ms = max(
                self.window_floor_ms,
                self._current_window_ms * 0.85,
            )
        self.stats.current_window_ms = round(self._current_window_ms, 2)

    async def _execute_batch(self, batch: list[BatchedItem]) -> None:
        """Pass the batched normalized requests to provider and resolve caller futures."""
        requests = [item.request for item in batch]
        t0 = time.monotonic()
        try:
            scores = await self.provider.infer(requests)
            duration_ms = (time.monotonic() - t0) * 1000.0

            if len(scores) != len(batch):
                err = RuntimeError(
                    f"Provider {self.provider.name} returned {len(scores)} scores for batch of {len(batch)}"
                )
                for item in batch:
                    if not item.future.done():
                        item.future.set_exception(err)
                return

            for item, score in zip(batch, scores):
                if not item.future.done():
                    item.future.set_result(score)

            self.stats.total_batches += 1
            self.stats.total_requests += len(batch)
            logger.debug(
                "Batch of %d flushed in %.2fms for provider %s",
                len(batch),
                duration_ms,
                self.provider.name,
            )

        except Exception as e:
            logger.error("Provider %s batch inference failed: %s", self.provider.name, e)
            for item in batch:
                if not item.future.done():
                    item.future.set_exception(e)
