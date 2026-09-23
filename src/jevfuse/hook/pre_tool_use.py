#!/usr/bin/env python3
"""Claude Code PreToolUse command hook for JEV-Fuse.

Reads a tool call event on stdin and writes a PreToolUseResult (Claude Code 2.1.274) on stdout:
- {"allow": true}
- {"ask": "reason why user must confirm"}
- {"deny": "reason why command was refused"}

Never crashes and never prints empty stdout for Bash tool calls.
"""

from __future__ import annotations

import asyncio
import json
import sys

from jevfuse.guard.classifier import evaluate_shell_command


async def run_hook() -> None:
    try:
        raw_input = sys.stdin.read()
        if not raw_input or not raw_input.strip():
            sys.exit(0)

        event = json.loads(raw_input)
    except Exception:
        # Non-JSON or broken stdin: exit cleanly
        sys.exit(0)

    # Only inspect Bash tool calls
    if event.get("tool_name") != "Bash":
        sys.exit(0)

    tool_input = event.get("tool_input") or {}
    command = tool_input.get("command")
    if not command or not command.strip():
        sys.exit(0)

    cwd = event.get("cwd") or ""

    try:
        verdict = await evaluate_shell_command(command, cwd=cwd)
        out = verdict.to_claude_hook_result()
    except Exception as exc:
        out = {"ask": f"JEV-Fuse: error during safety evaluation ({exc}); asking user confirmation."}

    # Print single-line valid JSON and exit 0
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    sys.exit(0)


def main() -> None:
    asyncio.run(run_hook())


if __name__ == "__main__":
    main()
