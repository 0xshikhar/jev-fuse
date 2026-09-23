"""Error schemas and structured error codes for Arbiter."""

from typing import Literal

from pydantic import BaseModel, Field

from jevfuse.schema.decision import Action

ErrorCode = Literal[
    "unrouted_task",
    "provider_unavailable",
    "provider_timeout",
    "rate_limited",
    "invalid_choices",
    "policy_misconfigured",
    "stale_calibration",
    "validation_error",
    "internal_error",
]


class ArbiterError(BaseModel):
    """Structured error payload returned on 4xx/5xx or MCP tool failures."""
    error_code: ErrorCode = Field(..., description="Machine-readable error identifier")
    message: str = Field(..., description="Human-readable error description")
    trace_id: str | None = Field(default=None, description="Request trace ID if assigned")
    suggested_action: Action = Field(
        default=Action.ASK,
        description="Safe fallback action for calling agent to take upon encountering this error"
    )
