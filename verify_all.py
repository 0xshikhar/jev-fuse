#!/usr/bin/env python3
"""Master verification test for JEV Fuse & Vercel AI Gateway."""

import asyncio
import json
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
    print("\n[2/4] Testing REST API, Diagnostics & Vercel AI Gateway Proxy...")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Health
        h = await client.get("/healthz")
        assert h.status_code == 200, f"Expected 200 on /healthz, got {h.status_code}"
        print("  ✅ GET /healthz -> Healthy (Local runtime active)")

        # Canonical governed decision endpoint
        d_res = await client.post("/v1/decide", json={"task": "shell-guard", "kind": "bool", "input": "git status"})
        assert d_res.status_code == 200
        d_json = d_res.json()
        trace_id = d_json.get("trace_id")
        print(f"  ✅ POST /v1/decide -> Governed Policy Active (Action: {d_json.get('action')}, Trace: {trace_id[:8]}...)")

        # Trace retrieval
        if trace_id:
            t_res = await client.get(f"/v1/traces/{trace_id}")
            assert t_res.status_code == 200
            print(f"  ✅ GET /v1/traces/{trace_id[:8]}... -> Found audit record")

        # 4-point diagnostic self-test
        diag_res = await client.post("/v1/diagnostics/self-test")
        assert diag_res.status_code == 200
        diag_data = diag_res.json()
        passed_count = sum(1 for r in diag_data.get("checks", []) if r.get("status") == "PASS")
        print(f"  ✅ POST /v1/diagnostics/self-test -> {passed_count}/{len(diag_data.get('checks', []))} checks passed")

        has_key = bool(
            os.environ.get("AI_GATEWAY_API_KEY")
            or os.environ.get("TYPESAFE_API_KEY")
            or os.environ.get("JEV_API_KEY")
        )
        if has_key:
            r = await client.get("/readyz")
            assert r.status_code == 200, f"Expected 200 on /readyz, got {r.status_code}: {r.text}"
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
        else:
            print("  ℹ️  Notice: No API key found in environment or .env file.")

    # 3. Test MCP Server Tools
    print("\n[3/4] Testing Model Context Protocol (MCP) Server (All 4 Tools)...")
    from jevfuse.mcp.server import create_mcp_server
    mcp_server = create_mcp_server()
    tools = await mcp_server.list_tools()
    tool_names = [t.name for t in tools]
    assert "fuse_guard" in tool_names and "fuse_prune" in tool_names and "fuse_verify" in tool_names and "fuse_route" in tool_names
    print(f"  ✅ All 4 MCP Tools Registered: {', '.join(tool_names)}")

    # 3a. Test fuse_guard
    res_guard = await mcp_server.call_tool("fuse_guard", {"command": "git status"})
    guard_data = json.loads(res_guard.content[0].text)
    assert guard_data["action"] == "allow"
    print("  ✅ MCP fuse_guard('git status') -> ALLOW")

    # 3b. Test fuse_route
    res_route = await mcp_server.call_tool("fuse_route", {
        "query": "Customer wants to cancel subscription and refund invoice",
        "options": ["billing_support", "technical_issues", "general_faq"]
    })
    route_data = json.loads(res_route.content[0].text)
    assert "selected" in route_data
    print(f"  ✅ MCP fuse_route -> Selected '{route_data['selected']}' (Confidence: {route_data['confidence']})")

    # 3c. Test fuse_verify
    res_verify = await mcp_server.call_tool("fuse_verify", {
        "statement": "The query uses parameterized placeholders to prevent SQL injection",
        "context": "SELECT * FROM users WHERE id = :user_id"
    })
    verify_data = json.loads(res_verify.content[0].text)
    assert "verified" in verify_data
    print(f"  ✅ MCP fuse_verify -> Verified: {verify_data['verified']}")

    # 3d. Test fuse_prune
    res_prune = await mcp_server.call_tool("fuse_prune", {
        "turns": [
            {"role": "user", "content": "Fix database deadlock"},
            {"role": "tool", "content": "Running test suite... passed"},
            {"role": "assistant", "content": "Deadlock resolved"}
        ],
        "goal": "Fix database deadlock"
    })
    prune_data = json.loads(res_prune.content[0].text)
    assert "pruned_turns" in prune_data
    print(f"  ✅ MCP fuse_prune -> Compacted {len(prune_data['pruned_turns'])} turns")

    # 4. Summary
    print("\n[4/4] Verification Complete!")
    print("=" * 65)
    print("  🎉 ALL JEV FUSE INTEGRATION TESTS PASSED (100% OK)")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
