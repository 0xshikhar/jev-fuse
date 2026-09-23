"""Public canonical schemas for decision requests, responses, and actions."""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DecisionKind(str, Enum):
    """The fundamental kinds of small, typed judgments."""
    CHOICE = "choice"   # Select one among N labeled candidates
    SCORE = "score"     # Bounded continuous assessment [0.0, 1.0]
    BOOL = "bool"       # Binary verification (True / False)


class Action(str, Enum):
    """Deterministic actions emitted by the Policy Engine."""
    # Permission gating vocabulary
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    # Agent loop vocabulary (e.g. jev-router pattern)
    CONTINUE = "continue"
    ESCALATE = "escalate"

    # Context compaction vocabulary (e.g. fast-jev-compaction pattern)
    KEEP = "keep"
    TRUNCATE = "truncate"
    DROP = "drop"

    # Safety & out-of-domain abstention
    ABSTAIN = "abstain"


class DecisionRequest(BaseModel):
    """Canonical incoming decision request across REST and MCP."""
    task: str = Field(..., min_length=1, description="Registered task name (e.g. 'bash-risk', 'prune')")
    kind: DecisionKind = Field(..., description="Decision classification kind")
    input: str = Field(..., min_length=1, description="Content to evaluate (diff, prompt, command)")
    choices: list[str] | None = Field(default=None, description="Candidate choices (required for CHOICE)")
    client_id: str = Field(default="default", description="Client identifier (e.g. 'claude-code', 'codex')")
    provider: Literal["auto", "jev", "laya"] = Field(default="auto", description="Execution provider")
    context: dict[str, Any] = Field(default_factory=dict, description="Metadata passed to policy rules")
    idempotency_key: str | None = Field(default=None, description="Optional client deduplication key")

    @model_validator(mode="after")
    def validate_choices_for_kind(self) -> "DecisionRequest":
        if self.kind == DecisionKind.CHOICE:
            if not self.choices or len(self.choices) < 2:
                raise ValueError("DecisionKind.CHOICE requires at least 2 distinct candidate choices")
        elif self.choices is not None and len(self.choices) > 0:
            raise ValueError(f"Choices cannot be provided for DecisionKind.{self.kind.name}")
        return self


class DecisionResponse(BaseModel):
    """Canonical decision response returned to agent clients."""
    trace_id: str = Field(..., description="Unique request trace identifier")
    task: str = Field(..., description="Registered task name")
    provider: str = Field(..., description="Provider that performed inference (e.g. 'jev', 'laya-mlx')")
    cached: bool = Field(default=False, description="True if answered from sub-millisecond hash cache")
    value: str | float | bool = Field(..., description="Structured outcome matching DecisionKind")
    raw_score: float = Field(..., description="Pre-calibration raw logit/probability from model")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Empirically calibrated probability [0.0, 1.0]")
    action: Action = Field(..., description="Action verdict evaluated by Policy Engine")
    latency_ms: float = Field(..., ge=0.0, description="Total execution latency in milliseconds")
    checkpoint_or_model: str = Field(..., description="Model identifier or checkpoint hash")
    reason: str | None = Field(default=None, description="Explanation populated on abstain, override, or error")
