"""Arbiter Prune Recipe: High-speed context window compaction for AI coding sessions.

Prunes stale tool outputs and dead log dumps from conversation histories in <100ms,
keeping vital code context verbatim without lossy narrative summarization.
"""

from __future__ import annotations

from typing import Any, Sequence

from arbiter.engine import ArbiterEngine
from arbiter.schema.decision import Action, DecisionKind, DecisionRequest


async def compact_context(
    turns: Sequence[dict[str, Any]],
    goal: str,
    engine: ArbiterEngine | None = None,
    truncate_lines: int = 15,
) -> list[dict[str, Any]]:
    """Prune conversation turns to preserve token space while maintaining code fidelity.
    
    Args:
        turns: List of turn dicts with keys 'role' and 'content'.
        goal: The active user goal or bug description.
        engine: Optional pre-configured ArbiterEngine.
        truncate_lines: Number of head and tail lines to keep when truncating.
        
    Returns:
        Compacted list of turns with stale items dropped or truncated.
    """
    app_engine = engine or ArbiterEngine()
    compacted: list[dict[str, Any]] = []

    for idx, turn in enumerate(turns):
        role = turn.get("role", "assistant")
        content = str(turn.get("content", ""))

        # 1. System prompts and user instructions are kept verbatim
        if role in ("system", "user") and len(content.split()) < 200:
            compacted.append(dict(turn))
            continue

        # 2. Evaluate relevance of tool or assistant output
        prompt = (
            f"Active Goal: {goal}\n\n"
            f"Conversation Turn ({role}):\n{content[:1200]}\n\n"
            "Is this turn actively relevant to writing code or solving the stated goal?"
        )
        req = DecisionRequest(
            task="context-prune",
            kind=DecisionKind.SCORE,
            input=prompt,
            context={"role": role, "turn_idx": idx},
        )
        res = await app_engine.decide(req)

        if res.action == Action.KEEP:
            compacted.append(dict(turn))
        elif res.action == Action.TRUNCATE:
            lines = content.splitlines()
            if len(lines) > (truncate_lines * 2):
                head = lines[:truncate_lines]
                tail = lines[-truncate_lines:]
                omitted = len(lines) - (truncate_lines * 2)
                truncated_content = "\n".join(head + [f"\n... [{omitted} lines truncated by JEV-Fuse] ...\n"] + tail)
                turn_copy = dict(turn)
                turn_copy["content"] = truncated_content
                turn_copy["_pruned"] = "truncated"
                compacted.append(turn_copy)
            else:
                compacted.append(dict(turn))
        elif res.action == Action.DROP:
            # Stale turn dropped from context
            continue
        else:
            compacted.append(dict(turn))

    return compacted
