"""Arbiter CLI: Command line interface for running the server, MCP, and agent recipes."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from arbiter.engine import ArbiterEngine
from arbiter.mcp.server import create_mcp_server
from arbiter.recipes.guard import evaluate_command
from arbiter.recipes.prune import compact_context
from arbiter.server.app import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jev-arbiter",
        description="Jev Arbiter: Governed decision runtime, policy control plane, and MCP supervisor for TypeSafe Jev & local models.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # arbiter serve
    serve_parser = subparsers.add_parser("serve", help="Start the FastAPI REST gateway")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    # arbiter mcp
    mcp_parser = subparsers.add_parser("mcp", help="Run the Model Context Protocol stdio server")

    # arbiter guard
    guard_parser = subparsers.add_parser("guard", help="Evaluate safety of a bash command")
    guard_parser.add_argument("command", nargs="+", help="Shell command to evaluate")

    # arbiter prune
    prune_parser = subparsers.add_parser("prune", help="Compact a conversation history file")
    prune_parser.add_argument("file", help="Path to JSON file containing conversation turns")
    prune_parser.add_argument("--goal", default="General coding and bug fixing", help="Active agent goal")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "serve":
        import uvicorn
        app = create_app()
        uvicorn.run(app, host=args.host, port=args.port)

    elif args.subcommand == "mcp":
        server = create_mcp_server()
        # Run stdio async loop
        asyncio.run(server.run_stdio_async())

    elif args.subcommand == "guard":
        cmd_str = " ".join(args.command)
        verdict = asyncio.run(evaluate_command(cmd_str))
        print(f"\n[Arbiter Guard Verdict]")
        print(f"  Command:    {verdict.command}")
        print(f"  Action:     {verdict.action.value.upper()}")
        print(f"  Confidence: {verdict.confidence:.2f}")
        print(f"  Reason:     {verdict.reason}\n")
        if verdict.action.value == "deny":
            sys.exit(1)
        elif verdict.action.value == "ask":
            sys.exit(2)
        else:
            sys.exit(0)

    elif args.subcommand == "prune":
        with open(args.file, "r", encoding="utf-8") as f:
            turns = json.load(f)
        compacted = asyncio.run(compact_context(turns, goal=args.goal))
        print(json.dumps(compacted, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
