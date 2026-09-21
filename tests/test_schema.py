"""Unit tests for canonical decision schemas, validation, and JSON serialization."""

import pytest
from pydantic import ValidationError

from arbiter.schema import (
    Action,
    ArbiterError,
    CalibratedScore,
    DecisionKind,
    DecisionRequest,
    DecisionResponse,
    NormalizedRequest,
    RawScore,
)


def test_decision_kind_values():
    assert DecisionKind.CHOICE == "choice"
    assert DecisionKind.SCORE == "score"
    assert DecisionKind.BOOL == "bool"
    assert len(DecisionKind) == 3


def test_action_enum_values():
    expected_actions = {
        "allow", "ask", "deny",
        "continue", "escalate",
        "keep", "truncate", "drop",
        "abstain"
    }
    actual_actions = {action.value for action in Action}
    assert actual_actions == expected_actions


def test_decision_request_choice_valid():
    req = DecisionRequest(
        task="element-selection",
        kind=DecisionKind.CHOICE,
        input="Click the flight search submit button",
        choices=["#btn-submit", "#btn-cancel", "#link-help"],
        client_id="browser-use",
    )
    assert req.task == "element-selection"
    assert req.kind == DecisionKind.CHOICE
    assert len(req.choices) == 3
    assert req.client_id == "browser-use"
    assert req.provider == "auto"


def test_decision_request_choice_requires_min_two_choices():
    # Missing choices
    with pytest.raises(ValidationError, match="requires at least 2 distinct candidate choices"):
        DecisionRequest(
            task="element-selection",
            kind=DecisionKind.CHOICE,
            input="Click button",
            choices=None,
        )

    # Empty choices
    with pytest.raises(ValidationError, match="requires at least 2 distinct candidate choices"):
        DecisionRequest(
            task="element-selection",
            kind=DecisionKind.CHOICE,
            input="Click button",
            choices=[],
        )

    # Single choice
    with pytest.raises(ValidationError, match="requires at least 2 distinct candidate choices"):
        DecisionRequest(
            task="element-selection",
            kind=DecisionKind.CHOICE,
            input="Click button",
            choices=["#btn-submit"],
        )


def test_decision_request_score_valid():
    req = DecisionRequest(
        task="bash-risk",
        kind=DecisionKind.SCORE,
        input="rm -rf /tmp/build",
        client_id="claude-code",
    )
    assert req.kind == DecisionKind.SCORE
    assert req.choices is None


def test_decision_request_score_rejects_choices():
    with pytest.raises(ValidationError, match="Choices cannot be provided for DecisionKind.SCORE"):
        DecisionRequest(
            task="bash-risk",
            kind=DecisionKind.SCORE,
            input="rm -rf /tmp/build",
            choices=["safe", "dangerous"],
        )


def test_decision_request_bool_valid():
    req = DecisionRequest(
        task="patch-verification",
        kind=DecisionKind.BOOL,
        input="Fixes null pointer dereference in auth loop",
    )
    assert req.kind == DecisionKind.BOOL
    assert req.choices is None


def test_decision_request_bool_rejects_choices():
    with pytest.raises(ValidationError, match="Choices cannot be provided for DecisionKind.BOOL"):
        DecisionRequest(
            task="patch-verification",
            kind=DecisionKind.BOOL,
            input="Fixes bug",
            choices=["true", "false"],
        )


def test_decision_request_json_roundtrip():
    req = DecisionRequest(
        task="code-risk",
        kind=DecisionKind.CHOICE,
        input="git push --force origin main",
        choices=["allow", "deny"],
        client_id="codex",
        context={"branch": "main", "repo": "arbiter"},
        idempotency_key="idemp_12345",
    )
    json_data = req.model_dump_json()
    reconstituted = DecisionRequest.model_validate_json(json_data)
    assert reconstituted == req
    assert reconstituted.context["branch"] == "main"
    assert reconstituted.idempotency_key == "idemp_12345"


def test_decision_response_valid():
    res = DecisionResponse(
        trace_id="tr_abc123",
        task="bash-risk",
        provider="jev",
        cached=False,
        value=0.92,
        raw_score=0.94,
        confidence=0.91,
        action=Action.ALLOW,
        latency_ms=18.4,
        checkpoint_or_model="jev-latest",
        reason=None,
    )
    assert res.trace_id == "tr_abc123"
    assert res.action == Action.ALLOW
    assert res.confidence == 0.91
    assert not res.cached


def test_decision_response_confidence_bounds():
    with pytest.raises(ValidationError):
        DecisionResponse(
            trace_id="tr_123",
            task="test",
            provider="jev",
            value=True,
            raw_score=1.5,
            confidence=1.05,  # Out of bounds [0.0, 1.0]
            action=Action.ALLOW,
            latency_ms=10.0,
            checkpoint_or_model="jev",
        )

    with pytest.raises(ValidationError):
        DecisionResponse(
            trace_id="tr_123",
            task="test",
            provider="jev",
            value=True,
            raw_score=-0.5,
            confidence=-0.1,  # Out of bounds [0.0, 1.0]
            action=Action.ALLOW,
            latency_ms=10.0,
            checkpoint_or_model="jev",
        )


def test_normalized_request_and_scores():
    norm = NormalizedRequest(
        trace_id="tr_norm_001",
        task="prune",
        kind=DecisionKind.CHOICE,
        input="tool output: listing 400 files",
        choices=("keep", "truncate", "drop"),
        client_id="claude-code",
        provider="jev",
        context={"tokens": 1200},
        input_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        timestamp_ns=1700000000000000000,
    )
    assert norm.choices == ("keep", "truncate", "drop")
    assert norm.input_hash.startswith("e3b0c4")

    raw = RawScore(
        value="truncate",
        raw_score=0.88,
        candidate_scores={"keep": 0.05, "truncate": 0.88, "drop": 0.07},
    )
    assert raw.value == "truncate"
    assert raw.candidate_scores["truncate"] == 0.88

    calibrated = CalibratedScore(
        value="truncate",
        raw_score=0.88,
        confidence=0.84,
        is_abstention=False,
    )
    assert calibrated.confidence == 0.84
    assert not calibrated.is_abstention


def test_arbiter_error_defaults_to_safe_ask():
    err = ArbiterError(
        error_code="provider_timeout",
        message="TypeSafe Jev API took longer than 800ms",
        trace_id="tr_timeout_999",
    )
    assert err.error_code == "provider_timeout"
    assert err.suggested_action == Action.ASK  # Safe default to avoid silent auto-allows
