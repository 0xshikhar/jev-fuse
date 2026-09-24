"""Internal data structures used between normalizer, router, batcher, and providers."""

from typing import Any
from pydantic import BaseModel, Field
from arbiter.schema.decision import DecisionKind


class NormalizedRequest(BaseModel):
    """Canonical representation after input normalization and hash computation."""
    trace_id: str = Field(..., description="Unique request identifier")
    task: str = Field(..., description="Target task name")
    kind: DecisionKind = Field(..., description="Decision kind")
    input: str = Field(..., description="Input text")
    choices: tuple[str, ...] | None = Field(default=None, description="Immutable tuple of choices")
    client_id: str = Field(default="default", description="Client identifier")
    provider: str = Field(default="auto", description="Resolved or requested provider")
    context: dict[str, Any] = Field(default_factory=dict, description="Metadata dictionary")
    input_hash: str = Field(..., description="SHA-256 hash of task, input, and choices")
    timestamp_ns: int = Field(..., description="Request arrival timestamp in nanoseconds")


class RawScore(BaseModel):
    """Uncalibrated score directly produced by a provider."""
    value: str | float | bool = Field(..., description="Model chosen candidate or float value")
    raw_score: float = Field(..., description="Raw model confidence or logit")
    candidate_scores: dict[str, float] | None = Field(
        default=None,
        description="Distribution across all candidates for choice tasks"
    )


class CalibratedScore(BaseModel):
    """Post-calibration score with empirical probability."""
    value: str | float | bool = Field(..., description="Decision value")
    raw_score: float = Field(..., description="Original raw score")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Calibrated confidence probability")
    is_abstention: bool = Field(default=False, description="True if calibration layer forced abstention")
