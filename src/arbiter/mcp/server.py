"""Native Model Context Protocol (MCP) server for Arbiter.

Enables Claude Code, Cursor, Codex, and other MCP-enabled agents to invoke
Arbiter's policy gates, bash command auto-approval, and context compaction natively.
"""

from __future__ import annotations

import json
from typing import Any
from mcp.server.mcpserver import MCPServer

from arbiter.engine import ArbiterEngine
from arbiter.schema.decision import Action, DecisionKind, DecisionRequest


def create_mcp_server(engine: ArbiterEngine | None = None) -> MCPServer:
    """Create and configure the Jev Arbiter MCP server."""
    app_engine = engine or ArbiterEngine()
    server = MCPServer("jev-fuse")

    @server.tool()
    async def arbiter_guard(command: str, cwd: str = "") -> str:
        """Evaluate whether a terminal/bash command is safe to execute automatically.
        
        Args:
            command: The exact shell command line (e.g. 'git status', 'rm -rf dist').
            cwd: Working directory context if relevant.
            
        Returns:
            JSON string containing action ('allow', 'ask', 'deny'), confidence, and rationale.
        """
        from arbiter.guard.classifier import evaluate_shell_command
        verdict = await evaluate_shell_command(command, cwd=cwd, engine=app_engine)
        return json.dumps({
            "action": verdict.action.value,
            "confidence": verdict.confidence,
            "is_destructive": verdict.is_destructive,
            "cached": False,
            "reason": verdict.reason,
            "recommendation": "allow" if verdict.action == Action.ALLOW else ("block" if verdict.action == Action.DENY else "confirm"),
        })

    @server.tool()
    async def arbiter_prune(turns: list[dict[str, Any]], goal: str) -> str:
        """Prune conversation turns to compact agent context without loss of critical code.
        
        Args:
            turns: List of conversation turn dicts with 'role' and 'content'.
            goal: The current user goal or active problem statement.
            
        Returns:
            JSON string mapping each turn index to an action ('keep', 'truncate', 'drop').
        """
        results: list[dict[str, Any]] = []

        for idx, turn in enumerate(turns):
            content = str(turn.get("content", ""))
            role = str(turn.get("role", "user"))

            # User messages and concise code requests are always kept
            if role == "user" and len(content.split()) < 100:
                results.append({"turn_index": idx, "action": "keep", "confidence": 1.0})
                continue

            prompt = (
                f"Goal: {goal}\n\n"
                f"Turn {idx} ({role}):\n{content[:1500]}\n\n"
                "How relevant is this turn's content to successfully completing the active goal?"
            )
            req = DecisionRequest(
                task="context-prune",
                kind=DecisionKind.SCORE,
                input=prompt,
                context={"turn_index": idx, "role": role},
            )
            res = await app_engine.decide(req)

            results.append({
                "turn_index": idx,
                "action": res.action.value,
                "relevance_score": res.confidence,
            })

        return json.dumps({"pruned_turns": results})

    @server.tool()
    async def arbiter_verify(statement: str, context: str = "") -> str:
        """Verify whether a semantic statement holds true given context (binary judgment).
        
        Args:
            statement: The hypothesis or claim to verify.
            context: Supporting code, logs, or state documentation.
            
        Returns:
            JSON string containing verified (bool), confidence, and action.
        """
        prompt = f"{statement}\n\nContext:\n{context}" if context else statement
        req = DecisionRequest(
            task="semantic-verify",
            kind=DecisionKind.BOOL,
            input=prompt,
        )
        res = await app_engine.decide(req)
        return json.dumps({
            "verified": bool(res.value),
            "confidence": res.confidence,
            "action": res.action.value,
            "latency_ms": res.latency_ms,
        })

    @server.tool()
    async def arbiter_route(query: str, options: list[str]) -> str:
        """Route a user prompt or tool query to one of multiple discrete candidates.
        
        Args:
            query: The intent or state to route.
            options: List of at least two target categories or tools.
            
        Returns:
            JSON string containing selected option, confidence, and action.
        """
        req = DecisionRequest(
            task="semantic-route",
            kind=DecisionKind.CHOICE,
            input=query,
            choices=options,
        )
        res = await app_engine.decide(req)
        return json.dumps({
            "selected": str(res.value),
            "confidence": res.confidence,
            "action": res.action.value,
            "latency_ms": res.latency_ms,
        })

    return server
