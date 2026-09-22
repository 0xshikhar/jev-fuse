"""TypeSafe Jev System One Wire Standard Schemas.

Faithful models adhering to TypeSafe's OpenAPI spec (POST /v1/systemone)
with seamless support for Arbiter's governed overlay actions.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field, RootModel
from arbiter.schema.decision import Action


class ChoiceAnswer(BaseModel):
    """Selection from requested categorical choices with full probability distribution."""
    type: Literal["choice"] = Field("choice", description="Identifies a selection from choices.")
    choice: str = Field(..., description="The name of the choice with the highest probability.")
    confidence: float = Field(..., description="Confidence in the selected choice, from 0 to 1.")
    probabilities: dict[str, float] = Field(
        ...,
        description="Probability of each choice in criteria, keyed by choice name, from 0 to 1.",
    )
    # Arbiter Governed Overlay extensions (optional, ignored by standard SDKs)
    action: Action | None = Field(default=None, description="Governed policy action if evaluated.")
    margin: float | None = Field(default=None, description="Margin between top and runner-up probability.")
    reason: str | None = Field(default=None, description="Explanation for policy action.")


class NoulCriteria(BaseModel):
    """Criteria clarifying what counts as a yes (true) or no (false) answer."""
    true: str | dict[str, Any] | list[Any] | None = Field(default=None, description="What counts as yes/true.")
    false: str | dict[str, Any] | list[Any] | None = Field(default=None, description="What counts as no/false.")


class NoulAnswer(BaseModel):
    """Yes/No probability answer."""
    type: Literal["noul"] = Field("noul", description="Identifies a yes/no answer.")
    noul: float = Field(
        ...,
        description="Probability of a yes answer or true statement, from 0 to 1.",
    )
    # Arbiter Governed Overlay extensions
    confidence: float | None = Field(default=None, description="Distance from uncertainty 0.5.")
    action: Action | None = Field(default=None, description="Governed policy action if evaluated.")
    reason: str | None = Field(default=None, description="Explanation for policy action.")


class ScoreAnswer(BaseModel):
    """Expected score across ordered rubric levels."""
    type: Literal["score"] = Field("score", description="Identifies a score against rubric levels.")
    score: float = Field(
        ...,
        description="Expected score: probability-weighted average of rubric levels.",
    )
    confidence: float = Field(..., description="Confidence in the score, from 0 to 1.")
    legend: dict[str, Any] = Field(
        ...,
        description="Criteria mapped to score levels.",
    )
    probabilities: dict[str, float] = Field(
        ...,
        description="Probability of each score level, keyed by level string.",
    )
    # Arbiter Governed Overlay extensions
    action: Action | None = Field(default=None, description="Governed policy action if evaluated.")
    reason: str | None = Field(default=None, description="Explanation for policy action.")


Answer = Annotated[Union[NoulAnswer, ChoiceAnswer, ScoreAnswer], Field(discriminator="type")]


class NoulQuestion(BaseModel):
    """Yes/No evaluation question."""
    type: Literal["noul"] = Field("noul", description="Yes/no question or statement.")
    instructions: str | dict[str, Any] | list[Any] | None = Field(default=None, description="Statement or question.")
    criteria: NoulCriteria | dict[str, Any] | None = Field(default=None, description="Yes/no criteria.")


class ChoiceQuestion(BaseModel):
    """Categorical choice question."""
    type: Literal["choice"] = Field("choice", description="Categorical selection question.")
    instructions: str | dict[str, Any] | list[Any] | None = Field(default=None, description="Decision instruction.")
    criteria: dict[str, Any] | list[Any] = Field(..., description="Choice names and descriptions.")


class ScoreQuestion(BaseModel):
    """Ordered rubric rating question."""
    type: Literal["score"] = Field("score", description="Rubric scoring question.")
    instructions: str | dict[str, Any] | list[Any] | None = Field(default=None, description="What to rate.")
    criteria: list[Any] = Field(..., min_length=1, description="Ordered descriptions of score levels.")


Question = Annotated[Union[NoulQuestion, ChoiceQuestion, ScoreQuestion], Field(discriminator="type")]


class Usage(BaseModel):
    """Input and output token counts."""
    input_tokens: int = Field(default=0, description="Billable input tokens.")
    output_tokens: int = Field(default=0, description="Output tokens.")


class ModelMetadata(BaseModel):
    """Model information."""
    name: str = Field(..., description="Model name or alias.")
    description: str = Field(..., description="Description of capabilities.")
    release_date: str = Field(..., description="Release date YYYY-MM-DD.")


class ModelMetadataList(BaseModel):
    """List of available models."""
    models: list[ModelMetadata] = Field(default_factory=list, description="Available models.")


class SystemOneRequest(BaseModel):
    """TypeSafe System One request payload (POST /v1/systemone)."""
    state: Any = Field(..., description="Content being evaluated (string, object, or array).")
    model: str = Field(default="jev-latest", description="Target model name or alias.")
    questions: dict[str, Any] = Field(..., min_length=1, description="Map of question specs keyed by name.")


class SystemOneResponse(BaseModel):
    """TypeSafe System One response payload."""
    model: str = Field(..., description="Model that evaluated the request.")
    answers: dict[str, Any] = Field(..., description="Map of answers keyed by question name.")
    usage: Usage = Field(default_factory=Usage, description="Token usage accounting.")
    # Optional metadata populated by Arbiter proxy
    governed: dict[str, Any] | None = Field(default=None, description="Arbiter policy verdicts if governed.")
    latency_ms: float | None = Field(default=None, description="Total execution latency in milliseconds.")

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)
