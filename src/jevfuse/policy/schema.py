"""Pydantic schemas for declarative YAML policies and evaluation contexts."""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from jevfuse.schema.decision import Action


class RuleCondition(BaseModel):
    """Evaluation condition for a policy rule."""
    confidence_gte: float | None = Field(default=None, ge=0.0, le=1.0, description="Confidence must be >= threshold")
    confidence_gt: float | None = Field(default=None, ge=0.0, le=1.0, description="Confidence must be > threshold")
    confidence_lte: float | None = Field(default=None, ge=0.0, le=1.0, description="Confidence must be <= threshold")
    confidence_lt: float | None = Field(default=None, ge=0.0, le=1.0, description="Confidence must be < threshold")
    value: Any = Field(default=None, description="Exact match against decision value")
    context: dict[str, Any] | None = Field(default=None, description="Subset match against request context")

    @model_validator(mode="after")
    def validate_has_condition(self) -> "RuleCondition":
        if (
            self.confidence_gte is None
            and self.confidence_gt is None
            and self.confidence_lte is None
            and self.confidence_lt is None
            and self.value is None
            and self.context is None
        ):
            raise ValueError("RuleCondition must specify at least one constraint (confidence, value, or context)")
        return self

    def matches(self, value: Any, confidence: float, context: dict[str, Any]) -> bool:
        """Check if inputs satisfy all specified constraints."""
        if self.confidence_gte is not None and confidence < self.confidence_gte:
            return False
        if self.confidence_gt is not None and confidence <= self.confidence_gt:
            return False
        if self.confidence_lte is not None and confidence > self.confidence_lte:
            return False
        if self.confidence_lt is not None and confidence >= self.confidence_lt:
            return False

        if self.value is not None:
            # Case-insensitive string comparison if both are strings
            if isinstance(self.value, str) and isinstance(value, str):
                if self.value.strip().lower() != value.strip().lower():
                    return False
            elif self.value != value:
                return False

        if self.context is not None:
            for k, expected_v in self.context.items():
                if k not in context or context[k] != expected_v:
                    return False

        return True


class PolicyRule(BaseModel):
    """Single when -> then policy rule."""
    when: RuleCondition = Field(..., description="Condition constraints")
    then: Action = Field(..., description="Action to take when condition is met")


class ClientOverride(BaseModel):
    """Client-specific override block."""
    rules: list[PolicyRule] = Field(default_factory=list, description="Rules applied before task-level rules")
    default_action: Action | None = Field(default=None, description="Client-specific fallback action")


class PolicyDefinition(BaseModel):
    """Declarative YAML policy file schema."""
    task: str = Field(..., min_length=1, description="Associated task name")
    version: int = Field(default=1, ge=1, description="Policy version number")
    default_action: Action = Field(default=Action.ASK, description="Fallback action when no rules match")
    require_local: bool = Field(default=False, description="Enforce zero-egress local provider execution")
    rules: list[PolicyRule] = Field(default_factory=list, description="Ordered list of task rules")
    client_overrides: dict[str, ClientOverride] = Field(default_factory=dict, description="Client overrides")
    on_unrouted_task: Action = Field(default=Action.ASK, description="Action when task is not registered")
    on_stale_calibration: Action = Field(default=Action.ASK, description="Action when calibration has drifted")
    on_provider_error: Action = Field(default=Action.ASK, description="Action when provider fails or times out")


class PolicyContext(BaseModel):
    """Context passed to policy evaluator."""
    task: str
    client_id: str = "default"
    value: str | float | bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    context: dict[str, Any] = Field(default_factory=dict)
    is_calibrated: bool = False
    provider_error: str | None = None
