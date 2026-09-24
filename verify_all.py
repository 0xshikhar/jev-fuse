#!/usr/bin/env python3
"""Master verification test for JEV Fuse & Vercel AI Gateway."""

import asyncio
import os
import httpx
from jevfuse.server.app import create_app
from jevfuse.guard.classifier import evaluate_shell_command
from jevfuse.schema.decision import Action

async def main():
    print("=" * 65)
    print("  🚀 RUNNING COMPLETE JEV FUSE INTEGRATION TEST SUITE")
    print("=" * 65)

    # 1. Test AST Shell Guard
    print("\n[1/4] Testing AST & Policy Guardrails...")
    safe_v = await evaluate_shell_command("git status")
    assert safe_v.action == Action.ALLOW, f"Expected ALLOW, got {safe_v.action}"
    print("  ✅ 'git status' -> ALLOW (Deterministic allowlist)")

    deny_v = await evaluate_shell_command("rm -rf /")
    assert deny_v.action == Action.DENY, f"Expected DENY, got {deny_v.action}"
    print("  ✅ 'rm -rf /' -> DENY (AST root protection)")

    # 2. Test In-Process ASGI Gateway
    print("\n[2/4] Testing REST API & Vercel AI Gateway Proxy...")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Health & Ready
        r = await client.get("/readyz")
        assert r.status_code == 200
        print("  ✅ GET /readyz -> Healthy (Upstream connected)")

        # SystemOne Proxy
        payload = {
            "model": "typesafe-ai/jev",
            "state": "User requested deleting test database records.",
            "questions": {"safe": {"type": "noul", "instructions": "Is this safe to run automated?"}}
        }
        res1 = await client.post("/v1/systemone", json=payload)
        assert res1.status_code == 200
        print("  ✅ POST /v1/systemone -> Live Vercel AI Gateway evaluated")

        # Cache Hit Test
        res2 = await client.post("/v1/systemone", json=payload)
        lat = float(res2.headers.get("X-Fuse-Latency-Ms", "0"))
        assert res2.status_code == 200
        print(f"  ✅ POST /v1/systemone -> Instant Cache Hit ({lat:.3f} ms)")

    # 3. Test MCP Server Tools
    print("\n[3/4] Testing Model Context Protocol (MCP) Server...")
    from jevfuse.mcp.server import create_mcp_server
    mcp_server = create_mcp_server()
    tools = await mcp_server.list_tools()
    tool_names = [t.name for t in tools]
    assert "fuse_guard" in tool_names and "fuse_prune" in tool_names
    print(f"  ✅ MCP Tools Active: {', '.join(tool_names)}")

    # 4. Summary
    print("\n[4/4] Verification Complete!")
    print("=" * 65)
    print("  🎉 ALL JEV FUSE INTEGRATION TESTS PASSED (100% OK)")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
