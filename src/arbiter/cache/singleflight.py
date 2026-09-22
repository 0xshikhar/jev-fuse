"""Singleflight concurrency coalescer for deduplicating in-flight requests."""

import asyncio
from typing import Any, Callable, Coroutine, TypeVar

T = TypeVar("T")


class AsyncSingleflight:
    """
    Coalesces concurrent in-flight calls sharing the same key.
    
    If N callers invoke `run(key, fn)` concurrently before the first finishes,
    `fn()` is executed exactly once, and all N callers receive the identical result.
    """

    def __init__(self) -> None:
        self._flights: dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: str, fn: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """Execute `fn()` or join an active in-flight execution for `key`."""
        async with self._lock:
            if key in self._flights:
                future = self._flights[key]
                # Wait outside lock
                is_leader = False
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._flights[key] = future
                is_leader = True

        if not is_leader:
            return await future

        # Leader executes fn()
        try:
            result = await fn()
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            async with self._lock:
                self._flights.pop(key, None)
