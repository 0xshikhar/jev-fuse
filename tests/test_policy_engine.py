"""Unit tests for declarative Policy Engine, YAML loading, and rule evaluation."""

import pytest
from arbiter.policy import (
    Action,
    PolicyContext,
    PolicyDefinition,
    PolicyEngine,
    PolicyLoadError,
    PolicyRule,
    RuleCondition,
    lint_policy,
    load_policy_from_str,
    simulate_coverage,
)

SAMPLE_BASH_POLICY_YAML = """
task: bash-risk
version: 2
default_action: ask
require_local: false

rules:
  - when:
      confidence_gte: 0.95
      value: "safe"
    then: allow

  - when:
      confidence_gte: 0.85
      value: "dangerous"
    then: deny

  - when:
      confidence_lt: 0.80
    then: ask

client_overrides:
  claude-code:
    rules:
      - when:
          confidence_gte: 0.98
          value: "safe"
        then: allow
    default_action: ask

  ci-runner:
    default_action: deny

on_unrouted_task: ask
on_stale_calibration: ask
on_provider_error: ask
"""

SAMPLE_PRUNING_POLICY_YAML = """
task: context-pruning
version: 1
default_action: keep

rules:
  - when:
      confidence_gte: 0.90
      value: "irrelevant"
    then: drop

  - when:
      confidence_gte: 0.80
      value: "verbose"
    then: truncate

  - when:
      confidence_gte: 0.85
      value: "vital"
    then: keep
"""


def test_load_policy_from_valid_yaml():
    policy = load_policy_from_str(SAMPLE_BASH_POLICY_YAML)
    assert policy.task == "bash-risk"
    assert policy.version == 2
    assert policy.default_action == Action.ASK
    assert len(policy.rules) == 3
    assert policy.rules[0].then == Action.ALLOW
    assert "claude-code" in policy.client_overrides
    assert policy.client_overrides["ci-runner"].default_action == Action.DENY


def test_load_policy_invalid_yaml():
    with pytest.raises(PolicyLoadError, match="YAML syntax error"):
        load_policy_from_str("task: [unclosed")

    with pytest.raises(PolicyLoadError, match="Policy file must contain a top-level YAML mapping"):
        load_policy_from_str("- a\n- b")

    with pytest.raises(PolicyLoadError, match="Field required"):
        load_policy_from_str("version: 1\ndefault_action: ask")  # missing task


def test_policy_engine_rule_evaluation():
    engine = PolicyEngine()
    policy = load_policy_from_str(SAMPLE_BASH_POLICY_YAML)
    engine.register_policy(policy)

    # 1. High confidence safe -> allow
    ctx_allow = PolicyContext(
        task="bash-risk",
        client_id="default",
        value="safe",
        confidence=0.96,
        is_calibrated=True,
    )
    action, reason = engine.evaluate(ctx_allow)
    assert action == Action.ALLOW
    assert "rule_match" in reason

    # 2. High confidence dangerous -> deny
    ctx_deny = PolicyContext(
        task="bash-risk",
        client_id="default",
        value="dangerous",
        confidence=0.90,
        is_calibrated=True,
    )
    action, reason = engine.evaluate(ctx_deny)
    assert action == Action.DENY

    # 3. Low confidence -> ask (abstention)
    ctx_ask = PolicyContext(
        task="bash-risk",
        client_id="default",
        value="safe",
        confidence=0.72,
        is_calibrated=True,
    )
    action, reason = engine.evaluate(ctx_ask)
    assert action == Action.ASK

    # 4. Fallback when intermediate condition doesn't match
    ctx_fallback = PolicyContext(
        task="bash-risk",
        client_id="default",
        value="unknown",
        confidence=0.88,
        is_calibrated=True,
    )
    action, reason = engine.evaluate(ctx_fallback)
    assert action == Action.ASK
    assert reason == "default_action"


def test_client_overrides_evaluation():
    engine = PolicyEngine()
    policy = load_policy_from_str(SAMPLE_BASH_POLICY_YAML)
    engine.register_policy(policy)

    # For default client, confidence 0.96 safe is ALLOW
    ctx_default = PolicyContext(task="bash-risk", client_id="default", value="safe", confidence=0.96, is_calibrated=True)
    action, _ = engine.evaluate(ctx_default)
    assert action == Action.ALLOW

    # For claude-code override, bar is 0.98, so 0.96 falls through to client default ASK
    ctx_claude = PolicyContext(task="bash-risk", client_id="claude-code", value="safe", confidence=0.96, is_calibrated=True)
    action, reason = engine.evaluate(ctx_claude)
    assert action == Action.ASK
    assert "client_default:claude-code" in reason

    # With confidence 0.99, claude-code passes override rule -> ALLOW
    ctx_claude_pass = PolicyContext(task="bash-risk", client_id="claude-code", value="safe", confidence=0.99, is_calibrated=True)
    action, reason = engine.evaluate(ctx_claude_pass)
    assert action == Action.ALLOW
    assert "client_override:claude-code" in reason

    # CI runner default action is DENY
    ctx_ci = PolicyContext(task="bash-risk", client_id="ci-runner", value="unknown", confidence=0.85, is_calibrated=True)
    action, reason = engine.evaluate(ctx_ci)
    assert action == Action.DENY
    assert "client_default:ci-runner" in reason


def test_default_uncalibrated_fails_closed():
    engine = PolicyEngine()
    policy = load_policy_from_str(SAMPLE_BASH_POLICY_YAML)
    engine.register_policy(policy)

    # Even with 0.99 confidence and "safe", uncalibrated default MUST abstain (Action.ASK)
    ctx = PolicyContext(task="bash-risk", client_id="default", value="safe", confidence=0.99)
    assert ctx.is_calibrated is False
    action, reason = engine.evaluate(ctx)
    assert action == Action.ASK
    assert reason == "stale_calibration"


def test_calibration_and_provider_error_safety():
    engine = PolicyEngine()
    policy = load_policy_from_str(SAMPLE_BASH_POLICY_YAML)
    engine.register_policy(policy)

    # Stale calibration forces on_stale_calibration (ASK)
    ctx_stale = PolicyContext(
        task="bash-risk",
        value="safe",
        confidence=0.99,
        is_calibrated=False,
    )
    action, reason = engine.evaluate(ctx_stale)
    assert action == Action.ASK
    assert reason == "stale_calibration"

    # Provider error forces on_provider_error (ASK)
    ctx_err = PolicyContext(
        task="bash-risk",
        value="safe",
        confidence=0.99,
        provider_error="HTTP 429 Rate limit exceeded",
    )
    action, reason = engine.evaluate(ctx_err)
    assert action == Action.ASK
    assert "provider_error" in reason


def test_unrouted_task():
    engine = PolicyEngine(default_unrouted_action=Action.ASK)
    ctx = PolicyContext(task="unregistered-task", value="something", confidence=0.99)
    action, reason = engine.evaluate(ctx)
    assert action == Action.ASK
    assert reason == "unrouted_task"


def test_python_hook_escape_hatch():
    engine = PolicyEngine()
    
    def custom_hook(ctx: PolicyContext) -> Action:
        if ctx.context.get("branch") == "production":
            return Action.DENY if ctx.value != "clean" else Action.ALLOW
        return Action.ASK

    engine.register_hook("custom-deploy", custom_hook)

    # Production dirty -> DENY
    ctx_prod_dirty = PolicyContext(
        task="custom-deploy",
        value="dirty",
        confidence=0.95,
        context={"branch": "production"},
        is_calibrated=True,
    )
    action, reason = engine.evaluate(ctx_prod_dirty)
    assert action == Action.DENY
    assert reason == "python_hook"

    # Staging -> ASK
    ctx_staging = PolicyContext(
        task="custom-deploy",
        value="clean",
        confidence=0.95,
        context={"branch": "staging"},
        is_calibrated=True,
    )
    action, reason = engine.evaluate(ctx_staging)
    assert action == Action.ASK


def test_context_compaction_vocabulary():
    engine = PolicyEngine()
    policy = load_policy_from_str(SAMPLE_PRUNING_POLICY_YAML)
    engine.register_policy(policy)

    ctx_drop = PolicyContext(task="context-pruning", value="irrelevant", confidence=0.92, is_calibrated=True)
    assert engine.evaluate(ctx_drop)[0] == Action.DROP

    ctx_trunc = PolicyContext(task="context-pruning", value="verbose", confidence=0.84, is_calibrated=True)
    assert engine.evaluate(ctx_trunc)[0] == Action.TRUNCATE

    ctx_keep = PolicyContext(task="context-pruning", value="vital", confidence=0.88, is_calibrated=True)
    assert engine.evaluate(ctx_keep)[0] == Action.KEEP


def test_policy_linting_and_coverage():
    policy = load_policy_from_str(SAMPLE_BASH_POLICY_YAML)
    findings = lint_policy(policy)
    # The sample policy has safe defaults and no shadowed rules
    assert len(findings) == 0

    # Policy with duplicate shadowed rule
    bad_policy = PolicyDefinition(
        task="test",
        default_action=Action.ALLOW,
        rules=[
            PolicyRule(when=RuleCondition(confidence_gte=0.9, value="a"), then=Action.ALLOW),
            PolicyRule(when=RuleCondition(confidence_gte=0.9, value="a"), then=Action.DENY),
        ],
    )
    bad_findings = lint_policy(bad_policy)
    assert len(bad_findings) >= 2  # default allow warning + shadowed rule warning

    # Test coverage simulation
    cov = simulate_coverage(policy, test_values=["safe", "dangerous"], confidence_step=0.1)
    assert cov.total_grid_points > 0
    assert cov.action_counts[Action.ALLOW] > 0
    assert cov.action_counts[Action.DENY] > 0
    assert cov.action_counts[Action.ASK] > 0
