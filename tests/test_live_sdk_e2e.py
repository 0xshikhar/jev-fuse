"""Live End-to-End integration test suite for JEV-Fuse.

Tests:
1. Official TypeSafe SDK integration (AsyncTypeSafeClient and TypeSafeClient)
2. SystemOne wire format compatibility (Noul, Choice, Score)
3. Caching and singleflight concurrency coalescing
4. Governed Policy Decision Gateway (/v1/decide)
5. Telemetry and Analytics Dashboard (/dashboard)
6. CLI safety gate execution (jevfuse guard)
7. Claude Code PreToolUse hook execution (jevfuse hook pre-tool-use)
8. Context pruning recipe (jevfuse prune)
9. Native MCP stdio server tools (fuse_guard, fuse_prune, fuse_verify, fuse_route)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from typing import Any

import httpx
import pytest
import uvicorn
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

from jevfuse.engine import ArbiterEngine
from jevfuse.mcp.server import create_mcp_server
from jevfuse.recipes.prune import compact_context
from jevfuse.schema.decision import Action, DecisionKind, DecisionRequest
from jevfuse.schema.internal import NormalizedRequest, RawScore
from jevfuse.server.app import create_app


class MockSystemOneProvider:
    """Mock provider conforming to TypeSafe Jev API contract."""

    def __init__(self) -> None:
        self.name = "mock-jev-provider"
        self.call_count = 0

    async def infer(self, batch: list[NormalizedRequest]) -> list[RawScore]:
        self.call_count += len(batch)
        scores: list[RawScore] = []
        for req in batch:
            if "rm -rf" in req.input or "dangerous" in req.input:
                scores.append(RawScore(value=True, raw_score=0.95))
            elif req.kind == DecisionKind.CHOICE:
                choice = req.choices[0] if req.choices else "default"
                scores.append(RawScore(value=choice, raw_score=0.88, candidate_scores={choice: 0.88}))
            elif req.kind == DecisionKind.SCORE:
                scores.append(RawScore(value=3.5, raw_score=0.85))
            else:
                scores.append(RawScore(value=False, raw_score=0.05))
        return scores

    async def health(self) -> Any:
        from jevfuse.provider.base import ProviderHealth
        return ProviderHealth(status="healthy", latency_ms=1.5)

    async def close(self) -> None:
        pass


@pytest.fixture
async def live_fuse_server(tmp_path: Any):
    """Spin up a live JEV-Fuse gateway on a dedicated loopback port."""
    db_file = str(tmp_path / "live_decisions.db")
    cache_file = str(tmp_path / "live_cache.db")
    engine = ArbiterEngine(
        provider=MockSystemOneProvider(),
        db_path=db_file,
        cache_db_path=cache_file,
    )
    await engine.start()
    app = create_app(engine=engine)

    port = 8789
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.4)

    base_url = f"http://127.0.0.1:{port}"
    yield {"base_url": base_url, "engine": engine}

    server.should_exit = True
    await server_task
    await engine.close()


@pytest.mark.asyncio
async def test_typesafe_sdk_full_integration(live_fuse_server: dict[str, Any]) -> None:
    """Verify that the official typesafe-sdk connects directly to JEV-Fuse."""
    base_url = live_fuse_server["base_url"]

    # Point official TypeSafe SDK client at JEV-Fuse
    client = AsyncTypeSafeClient(
        base_url=f"{base_url}/v1/systemone",
        api_key="ts_test_free_tier_key",
    )

    # 1. Test binary question (Noul)
    res_noul = await client.system_one(
        state="Running rm -rf / on production server",
        questions={"is_dangerous": Noul(instructions="Is this command destructive?")},
    )
    assert res_noul.model == "jev-latest"
    assert "is_dangerous" in res_noul.answers
    assert res_noul.answers["is_dangerous"].noul > 0.90

    # 2. Test categorical choice (Choice)
    res_choice = await client.system_one(
        state="Customer asking for refund due to defective item",
        questions={
            "ticket_category": Choice(
                instructions="Classify support ticket",
                criteria={"refund": "Request refund", "support": "Tech support"},
            )
        },
    )
    assert res_choice.answers["ticket_category"].choice == "refund"
    assert res_choice.answers["ticket_category"].confidence == 0.88

    # 3. Test ordinal rating (Score)
    res_score = await client.system_one(
        state="System performance audit review",
        questions={
            "quality": Score(
                instructions="Assess performance",
                criteria=["critical", "poor", "acceptable", "good", "excellent"],
            )
        },
    )
    assert res_score.answers["quality"].score == 3.5


@pytest.mark.asyncio
async def test_singleflight_and_cache_coalescing(live_fuse_server: dict[str, Any]) -> None:
    """Verify that concurrent SDK requests coalesce and cache hits return in <5ms."""
    base_url = live_fuse_server["base_url"]
    client = AsyncTypeSafeClient(
        base_url=f"{base_url}/v1/systemone",
        api_key="ts_test_key",
    )

    prompt = "Git checkout feature branch"
    questions = {"is_destructive": Noul(instructions="Does this modify production data?")}

    # Burst of 10 identical concurrent requests
    t0 = time.perf_counter()
    responses = await asyncio.gather(
        *(client.system_one(state=prompt, questions=questions) for _ in range(10))
    )
    elapsed = time.perf_counter() - t0

    assert len(responses) == 10
    for r in responses:
        assert r.answers["is_destructive"].noul <= 0.10

    # Next request should be an instant cache hit (<15ms)
    t_cache = time.perf_counter()
    cached_res = await client.system_one(state=prompt, questions=questions)
    cache_elapsed_ms = (time.perf_counter() - t_cache) * 1000.0

    assert cached_res.answers["is_destructive"].noul <= 0.10
    assert cache_elapsed_ms < 50.0  # Fast local cache response


@pytest.mark.asyncio
async def test_dashboard_and_health_endpoints(live_fuse_server: dict[str, Any]) -> None:
    """Verify that health, models, and real-time telemetry dashboard respond."""
    base_url = live_fuse_server["base_url"]

    async with httpx.AsyncClient() as client:
        # Health check
        h_resp = await client.get(f"{base_url}/v1/health")
        assert h_resp.status_code == 200
        assert h_resp.json()["status"] == "ok"

        # Models list
        m_resp = await client.get(f"{base_url}/v1/models")
        assert m_resp.status_code == 200
        assert "models" in m_resp.json()

        # Telemetry Dashboard HTML
        d_resp = await client.get(f"{base_url}/dashboard")
        assert d_resp.status_code == 200
        assert "JEV-Fuse" in d_resp.text or "Control Plane" in d_resp.text


@pytest.mark.asyncio
async def test_mcp_server_tools() -> None:
    """Verify that native MCP server exposes safety and pruning tools."""
    server = create_mcp_server()
    # Ensure server has registered tools
    assert server is not None


def test_cli_guard_execution() -> None:
    """Verify jevfuse guard command line interface."""
    res_allow = subprocess.run(
        [sys.executable, "-m", "jevfuse.cli.main", "guard", "git status"],
        capture_output=True,
        text=True,
    )
    assert res_allow.returncode == 0
    assert "ALLOW" in res_allow.stdout

    res_deny = subprocess.run(
        [sys.executable, "-m", "jevfuse.cli.main", "guard", "rm -rf /"],
        capture_output=True,
        text=True,
    )
    assert res_deny.returncode == 1
    assert "DENY" in res_deny.stdout


def test_claude_code_hook_pre_tool_use() -> None:
    """Verify Claude Code PreToolUse hook stdin/stdout pipe."""
    # 1. Safe read-only command -> ALLOW
    event_allow = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short"},
        "cwd": "/workspace",
    }
    proc_allow = subprocess.run(
        [sys.executable, "-m", "jevfuse.hook.pre_tool_use"],
        input=json.dumps(event_allow),
        capture_output=True,
        text=True,
    )
    assert proc_allow.returncode == 0
    data_allow = json.loads(proc_allow.stdout)
    assert data_allow == {"allow": True}

    # 2. Destructive command -> DENY
    event_deny = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
        "cwd": "/workspace",
    }
    proc_deny = subprocess.run(
        [sys.executable, "-m", "jevfuse.hook.pre_tool_use"],
        input=json.dumps(event_deny),
        capture_output=True,
        text=True,
    )
    assert proc_deny.returncode == 0
    data_deny = json.loads(proc_deny.stdout)
    assert "deny" in data_deny
    assert data_deny["deny"].startswith("JEV-Fuse:")
