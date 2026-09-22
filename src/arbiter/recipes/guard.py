"""Arbiter Guard Recipe: Bash command auto-approval gate for coding agents.

Evaluates terminal commands against safety policies, eliminating user prompt fatigue
for harmless read-only or development commands while catching destructive actions.
"""

from __future__ import annotations

import re
import sys
from typing import NamedTuple

from arbiter.engine import ArbiterEngine
from arbiter.schema.decision import Action, DecisionKind, DecisionRequest


class GuardVerdict(NamedTuple):
    action: Action
    confidence: float
    reason: str
    command: str


# Deterministic high-speed pre-filter patterns
SAFE_PATTERNS = [
    r"^git\s+(status|diff|log|branch|show|remote)(\s+.*)?$",
    r"^(pytest|cargo\s+test|bun\s+test|npm\s+test)(\s+.*)?$",
    r"^(ls|pwd|cat|head|tail|grep|ripgrep|find)(\s+.*)?$",
    r"^(uv\s+run\s+pytest|python\s+-m\s+pytest)(\s+.*)?$",
]

DANGEROUS_PATTERNS = [
    r"rm\s+(-[rfRF]+\s+|--recursive\s+)(/|\*|\$HOME|\~)",
    r"git\s+push\s+.*--force",
    r"DROP\s+(DATABASE|TABLE)",
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # Fork bomb
    r"mkfs\.",
    r"chmod\s+(-R\s+)?777\s+/",
]


async def evaluate_command(command: str, engine: ArbiterEngine | None = None) -> GuardVerdict:
    """Evaluate whether a shell command is safe to execute automatically.
    
    Args:
        command: The shell command to evaluate.
        engine: Optional pre-configured ArbiterEngine.
        
    Returns:
        GuardVerdict with action (allow, ask, deny), confidence, and reason.
    """
    cleaned = command.strip()

    # Fast Path 1: Instant match on known catastrophic patterns
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, cleaned, re.IGNORECASE):
            return GuardVerdict(
                action=Action.DENY,
                confidence=1.0,
                reason="Catastrophic command detected by deterministic safety rule",
                command=cleaned,
            )

    # Fast Path 2: Instant match on known safe read-only commands
    for pat in SAFE_PATTERNS:
        if re.match(pat, cleaned, re.IGNORECASE):
            return GuardVerdict(
                action=Action.ALLOW,
                confidence=0.99,
                reason="Standard verified read-only or test execution",
                command=cleaned,
            )

    # Path 3: Semantic Decision Evaluation via Arbiter Engine
    app_engine = engine or ArbiterEngine()
    prompt = (
        f"Command: {cleaned}\n\n"
        "Does this command permanently delete user data, destroy repository history, "
        "or compromise system security?"
    )
    req = DecisionRequest(
        task="shell-guard",
        kind=DecisionKind.BOOL,
        input=prompt,
        context={"command": cleaned},
    )
    res = await app_engine.decide(req)

    return GuardVerdict(
        action=res.action,
        confidence=res.confidence,
        reason=res.reason or "Evaluated by Arbiter policy engine",
        command=cleaned,
    )
