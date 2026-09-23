"""Policy linter and coverage simulation utility."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from jevfuse.policy.engine import PolicyEngine
from jevfuse.policy.schema import PolicyContext, PolicyDefinition
from jevfuse.schema.decision import Action


@dataclass
class LintFinding:
    level: str  # "ERROR", "WARNING", "INFO"
    message: str
    rule_index: int | None = None


@dataclass
class CoverageReport:
    total_grid_points: int
    action_counts: dict[Action, int] = field(default_factory=dict)
    unmatched_grid_points: int = 0
    warnings: list[str] = field(default_factory=list)


def lint_policy(policy: PolicyDefinition) -> list[LintFinding]:
    """Perform static safety and redundancy checks on a PolicyDefinition."""
    findings: list[LintFinding] = []

    if not policy.rules:
        findings.append(LintFinding(
            level="WARNING",
            message=f"Task '{policy.task}' has zero rules defined. Every decision will resolve to default '{policy.default_action.value}'."
        ))

    # Check for duplicate / shadowed rules
    seen_conditions: list[str] = []
    for idx, rule in enumerate(policy.rules):
        cond_repr = rule.when.model_dump_json()
        if cond_repr in seen_conditions:
            findings.append(LintFinding(
                level="WARNING",
                message=f"Rule #{idx} is shadowed by an earlier identical condition.",
                rule_index=idx,
            ))
        seen_conditions.append(cond_repr)

    # Check default action safety
    if policy.default_action == Action.ALLOW:
        findings.append(LintFinding(
            level="WARNING",
            message="Policy default_action is set to 'allow'. It is strongly recommended to fail-closed with 'ask' or 'deny'."
        ))

    return findings


def simulate_coverage(
    policy: PolicyDefinition,
    test_values: Sequence[Any] = ("safe", "dangerous", True, False, "low-risk", "high-risk"),
    confidence_step: float = 0.05,
    client_ids: Sequence[str] = ("default",),
) -> CoverageReport:
    """
    Synthesize a 2D grid of (value, confidence) pairs and report the resulting action distribution.
    Verifies that policy does not have silent, unhandled confidence bands.
    """
    engine = PolicyEngine()
    engine.register_policy(policy)

    report = CoverageReport(total_grid_points=0)
    for a in Action:
        report.action_counts[a] = 0

    conf_values = [round(c * confidence_step, 2) for c in range(int(1.0 / confidence_step) + 1)]

    for client_id in client_ids:
        for val in test_values:
            for conf in conf_values:
                report.total_grid_points += 1
                ctx = PolicyContext(
                    task=policy.task,
                    client_id=client_id,
                    value=val,
                    confidence=conf,
                    is_calibrated=True,
                )
                action, reason = engine.evaluate(ctx)
                report.action_counts[action] += 1
                if reason == "default_action":
                    report.unmatched_grid_points += 1

    return report
