"""Pydantic model and serialization for decision log records."""

from datetime import datetime, timezone
import json
import time
from typing import Any
from pydantic import BaseModel, Field

from arbiter.schema.decision import Action


class DecisionRecord(BaseModel):
    """Complete record of a decision evaluated by Arbiter."""
    trace_id: str = Field(..., description="Unique decision trace ID")
    timestamp_ns: int = Field(default_factory=time.time_ns, description="Timestamp in nanoseconds")
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO-8601 UTC timestamp"
    )
    task: str = Field(..., description="Target task name")
    client_id: str = Field(default="default", description="Client identifier")
    provider: str = Field(..., description="Execution provider (e.g. 'jev', 'laya-mlx', 'cache')")
    input_hash: str = Field(..., description="SHA-256 fingerprint of input")
    input_preview: str | None = Field(default=None, description="Truncated input text for UI display")
    decision_value: str | float | bool = Field(..., description="Model outcome")
    raw_score: float = Field(..., description="Pre-calibration raw score")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Calibrated confidence [0.0, 1.0]")
    action: Action = Field(..., description="Action verdict evaluated by policy")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Execution latency in milliseconds")
    cached: bool = Field(default=False, description="Whether decision was served from cache")
    human_label: str | None = Field(default=None, description="Ground truth feedback label if reviewed")
    human_labeled_at: str | None = Field(default=None, description="Timestamp when label was reviewed")
    context: dict[str, Any] = Field(default_factory=dict, description="Metadata dictionary")
    reason: str | None = Field(default=None, description="Policy reason or error note")

    def to_sql_tuple(self) -> tuple[Any, ...]:
        """Convert record to parameter tuple matching schema.sql column order."""
        return (
            self.trace_id,
            self.timestamp_ns,
            self.timestamp_iso,
            self.task,
            self.client_id,
            self.provider,
            self.input_hash,
            self.input_preview[:500] if self.input_preview else None,
            str(self.decision_value),
            float(self.raw_score),
            float(self.confidence),
            self.action.value,
            float(self.latency_ms),
            1 if self.cached else 0,
            self.human_label,
            self.human_labeled_at,
            json.dumps(self.context, ensure_ascii=False) if self.context else None,
            self.reason,
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "DecisionRecord":
        """Instantiate DecisionRecord from an SQLite row dictionary."""
        val = row["decision_value"]
        # Try casting to bool/float if applicable
        parsed_val: str | float | bool = val
        if val.lower() == "true":
            parsed_val = True
        elif val.lower() == "false":
            parsed_val = False
        else:
            try:
                parsed_val = float(val)
            except ValueError:
                parsed_val = val

        context_dict = {}
        if row.get("context_json"):
            try:
                context_dict = json.loads(row["context_json"])
            except Exception:
                context_dict = {}

        return cls(
            trace_id=row["trace_id"],
            timestamp_ns=row["timestamp_ns"],
            timestamp_iso=row["timestamp_iso"],
            task=row["task"],
            client_id=row["client_id"],
            provider=row["provider"],
            input_hash=row["input_hash"],
            input_preview=row.get("input_preview"),
            decision_value=parsed_val,
            raw_score=float(row["raw_score"]),
            confidence=float(row["confidence"]),
            action=Action(row["action"]),
            latency_ms=float(row["latency_ms"]),
            cached=bool(row["cached"]),
            human_label=row.get("human_label"),
            human_labeled_at=row.get("human_labeled_at"),
            context=context_dict,
            reason=row.get("reason"),
        )
