"""Base protocol and health structures for Arbiter decision providers."""

from typing import Any, Literal, Protocol, Sequence, runtime_checkable
from pydantic import BaseModel, Field

from arbiter.schema.internal import NormalizedRequest, RawScore


class ProviderHealth(BaseModel):
    """Health and connectivity status of a decision provider."""
    status: Literal["healthy", "degraded", "unhealthy"] = Field(..., description="Provider health state")
    latency_ms: float | None = Field(default=None, description="Round-trip ping or probe latency in ms")
    message: str = Field(default="", description="Descriptive status or diagnostic message")
    details: dict[str, Any] = Field(default_factory=dict, description="Diagnostic metadata (e.g. memory, queue)")


@runtime_checkable
class DecisionProvider(Protocol):
    """Protocol for model execution engines (hosted cloud APIs or local weights)."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. 'jev', 'laya-mlx')."""
        ...

    async def infer(self, batch: Sequence[NormalizedRequest]) -> list[RawScore]:
        """Execute a batch of normalized decision requests and return raw scores."""
        ...

    async def health(self) -> ProviderHealth:
        """Check provider connectivity, availability, and readiness."""
        ...

    async def close(self) -> None:
        """Release underlying connections or model resources."""
        ...
