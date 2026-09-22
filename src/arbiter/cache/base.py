"""Base models and protocols for decision caching."""

import time
from typing import Protocol, runtime_checkable
from pydantic import BaseModel, Field

from arbiter.schema.internal import RawScore


class CacheEntry(BaseModel):
    """Cached decision raw score with TTL metadata."""
    input_hash: str = Field(..., description="SHA-256 fingerprint")
    raw_score: RawScore = Field(..., description="Cached model output")
    provider: str = Field(..., description="Originating provider (e.g. 'jev', 'laya-mlx')")
    created_at_ns: int = Field(..., description="Creation timestamp in nanoseconds")
    expires_at_ns: int = Field(..., description="Expiration timestamp in nanoseconds")

    def is_expired(self, now_ns: int | None = None) -> bool:
        """Check if entry has passed its TTL."""
        current_ns = now_ns if now_ns is not None else time.time_ns()
        return current_ns >= self.expires_at_ns


@runtime_checkable
class CacheStore(Protocol):
    """Protocol for cache backends."""

    async def get(self, input_hash: str) -> CacheEntry | None:
        """Retrieve cached entry by hash if present and unexpired."""
        ...

    async def set(
        self,
        input_hash: str,
        raw_score: RawScore,
        provider: str,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """Store raw score with given TTL."""
        ...

    async def delete(self, input_hash: str) -> None:
        """Delete entry by hash."""
        ...

    async def clear(self) -> None:
        """Clear all entries."""
        ...
