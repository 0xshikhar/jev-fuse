"""Integration and conformance tests for Arbiter REST Gateway and Native MCP Server."""

import json
from typing import Any, Sequence
import httpx
import pytest
from httpx import ASGITransport

from arbiter.engine import ArbiterEngine
from arbiter.mcp.server import create_mcp_server
from arbiter.provider.base import ProviderHealth
from arbiter.recipes.guard import evaluate_command
from arbiter.recipes.prune import compact_context
from arbiter.schema.decision import Action, DecisionKind, DecisionRequest
from arbiter.schema.internal import NormalizedRequest, RawScore
from arbiter.server.app import create_app


class MockDeterministicProvider:
    """Mock provider returning predictable raw scores for test cases."""

    def __init__(self) -> None:
        self.name_val = "mock-engine"

    @property
    def name(self) -> str:
        return self.name_val

    async def infer(self, batch: Sequence[NormalizedRequest]) -> list[RawScore]:
        scores: list[RawScore] = []
        for req in batch:
            if "rm -rf" in req.input or "destructive" in req.task:
                scores.append(RawScore(value=True, raw_score=0.92))
            elif "choice" in req.kind:
                scores.append(RawScore(value=req.choices[0] if req.choices else "opt_a", raw_score=0.88))
            elif "score" in req.kind:
                scores.append(RawScore(value=0.85, raw_score=0.85))
            else:
                scores.append(RawScore(value=False, raw_score=0.10))
        return scores

    async def health(self) -> ProviderHealth:
        return ProviderHealth(status="healthy", latency_ms=1.2)

    async def close(self) -> None:
        pass


@pytest.fixture
async def test_engine(tmp_path: Any) -> ArbiterEngine:
    db_file = str(tmp_path / "test_decisions.db")
    cache_file = str(tmp_path / "test_cache.db")
    engine = ArbiterEngine(
        provider=MockDeterministicProvider(),
        db_path=db_file,
        cache_db_path=cache_file,
    )
    await engine.start()
    yield engine
    await engine.close()


@pytest.mark.asyncio
async def test_rest_decide_endpoint(test_engine: ArbiterEngine) -> None:
    app = create_app(engine=test_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Health check
        resp = await client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Canonical decide request
        req_payload = {
            "task": "shell-guard",
            "kind": "bool",
            "input": "git push --force origin main",
            "client_id": "claude-code",
        }
        resp = await client.post("/v1/decide", json=req_payload)
        assert resp.status_code == 200
        data = resp.json()

        assert "trace_id" in data
        assert data["task"] == "shell-guard"
        assert data["provider"] == "mock-engine"
        assert data["action"] in ("allow", "ask", "deny")
        assert data["confidence"] > 0.0
        assert data["cached"] is False

        # Repeat identical request -> should hit sub-millisecond hash cache
        resp2 = await client.post("/v1/decide", json=req_payload)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["cached"] is True
        assert data2["latency_ms"] < 20.0


@pytest.mark.asyncio
async def test_rest_systemone_typesafe_dropin(test_engine: ArbiterEngine) -> None:
    """Verify 100% TypeSafe Jev API drop-in compatibility."""
    app = create_app(engine=test_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "state": "User reports double charges on invoice #1024",
            "questions": {
                "category": {
                    "type": "choice",
                    "instructions": "Which department should handle this?",
                    "criteria": {"billing": "refunds and payments", "support": "general questions"},
                },
                "urgent": {
                    "type": "noul",
                    "instructions": "This ticket requires immediate action.",
                },
                "score_rating": {
                    "type": "score",
                    "instructions": "Severity from 1 to 4",
                    "criteria": ["low", "medium", "high", "critical"],
                },
            },
        }

        resp = await client.post("/v1/systemone", json=payload)
        assert resp.status_code == 200
        res = resp.json()

        assert "model" in res
        assert "answers" in res
        assert "usage" in res
        assert "latency_ms" in res

        # Verify all 3 questions answered
        answers = res["answers"]
        assert "category" in answers
        assert answers["category"]["type"] == "choice"
        assert "choice" in answers["category"]

        assert "urgent" in answers
        assert answers["urgent"]["type"] == "noul"
        assert "noul" in answers["urgent"]

        assert "score_rating" in answers
        assert answers["score_rating"]["type"] == "score"
        assert "score" in answers["score_rating"]


@pytest.mark.asyncio
async def test_rest_feedback_and_metrics_dashboard(test_engine: ArbiterEngine) -> None:
    app = create_app(engine=test_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a decision
        dec_resp = await client.post("/v1/decide", json={
            "task": "shell-guard",
            "kind": "bool",
            "input": "ls -la",
        })
        trace_id = dec_resp.json()["trace_id"]

        # Record human ground truth
        fb_resp = await client.post("/v1/feedback", json={
            "trace_id": trace_id,
            "label": "allowed",
        })
        assert fb_resp.status_code == 200

        # Check metrics endpoint
        m_resp = await client.get("/v1/metrics")
        assert m_resp.status_code == 200
        assert m_resp.json()["total_logged_decisions"] >= 1

        # Check HTML dashboard
        dash_resp = await client.get("/dashboard")
        assert dash_resp.status_code == 200
        assert "JEV-Fuse Control Plane" in dash_resp.text


@pytest.mark.asyncio
async def test_native_mcp_tools(test_engine: ArbiterEngine) -> None:
    """Verify native Model Context Protocol tools."""
    server = create_mcp_server(engine=test_engine)
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]

    assert "fuse_guard" in tool_names
    assert "fuse_prune" in tool_names
    assert "fuse_verify" in tool_names
    assert "fuse_route" in tool_names

    # Test fuse_guard tool call directly
    res = await server.call_tool("fuse_guard", {"command": "git status"})
    assert res.content and len(res.content) > 0
    result = json.loads(res.content[0].text)
    assert result["action"] in ("allow", "ask", "deny")
    assert "confidence" in result


@pytest.mark.asyncio
async def test_showcase_recipes(test_engine: ArbiterEngine) -> None:
    # 1. Test Guard Recipe
    verdict_safe = await evaluate_command("git status", engine=test_engine)
    assert verdict_safe.action == Action.ALLOW

    verdict_danger = await evaluate_command("rm -rf /", engine=test_engine)
    assert verdict_danger.action == Action.DENY

    # 2. Test Prune Recipe
    turns = [
        {"role": "user", "content": "Fix the bug in auth.py"},
        {"role": "assistant", "content": "Running tests...\n" + ("test line\n" * 50)},
    ]
    compacted = await compact_context(turns, goal="Fix auth bug", engine=test_engine)
    assert len(compacted) >= 1
    assert compacted[0]["role"] == "user"
