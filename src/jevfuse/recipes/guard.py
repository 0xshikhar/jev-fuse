"""Arbiter Guard Recipe: Bash command auto-approval gate for coding agents.

Evaluates terminal commands against safety policies, eliminating user prompt fatigue
for harmless read-only or development commands while catching destructive actions.
"""

from __future__ import annotations

from typing import NamedTuple

from jevfuse.engine import ArbiterEngine
from jevfuse.schema.decision import Action


class GuardVerdict(NamedTuple):
    action: Action
    confidence: float
    reason: str
    command: str
    is_destructive: bool = False


async def evaluate_command(command: str, engine: ArbiterEngine | None = None) -> GuardVerdict:
    """Evaluate whether a shell command is safe to execute automatically.
    
    Delegates to the unified Arbiter Phase 8 shell classifier.
    """
    from jevfuse.guard.classifier import evaluate_shell_command
    res = await evaluate_shell_command(command, engine=engine)
    return GuardVerdict(
        action=res.action,
        confidence=res.confidence,
        reason=res.reason,
        command=res.command,
        is_destructive=res.is_destructive,
    )

