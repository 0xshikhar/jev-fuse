"""End-to-end conformance and integration tests for SystemOne Universal Wire Standard

Tests Trojan Horse proxy mode (POST /v1/systemone and POST /systemone),
Fast-Jev-Compaction payload compatibility, singleflight concurrency,
governed margin deadbands, and full-fidelity SQLite logging.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from jevfuse.engine import JevFuseEngine
from jevfuse.provider.jev import JevDriver
from jevfuse.schema import (
    Action,
    ChoiceAnswer,
    SystemOneResponse,
    Usage,
)
from jevfuse.server.app import create_app

# Recorded TypeSafe fixture responses
FIXTURE_SYSTEMONE_RESPONSE = {
    "model": "jev-latest",
    "answers": {
        "urgency_noul": {
            "type": "noul",
            "noul": 0.98,
        },
        "intent_choice": {
            "type": "choice",
            "choice": "refund",
            "confidence": 0.88,
            "probabilities": {
                "refund": 0.88,
                "cancellation": 0.08,
                "general": 0.04,
            },
        },
        "quality_score": {
            "type": "score",
            "score": 2.7,
            "confidence": 0.92,
            "legend": {"0": "poor", "1": "fair", "2": "good", "3": "excellent"},
            "probabilities": {"0": 0.02, "1": 0.08, "2": 0.10, "3": 0.80},
        },
    },
    "usage": {
        "input_tokens": 145,
        "output_tokens": 18,
    },
}


@pytest.fixture
def mock_typesafe_transport() -> httpx.MockTransport:
    """Mock upstream TypeSafe server responding to POST /v1/systemone and GET /v1/models."""
    call_counts = {"systemone": 0, "models": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        path = request.url.path
        if path in ("/v1/systemone", "/systemone"):
            call_counts["systemone"] += 1
            body = json.loads(request.content)
            state = body.get("state")
            questions = body.get("questions", {})

            # Simulate fast-jev-compaction response if compaction questions present
            if "t1_call" in questions:
                return httpx.Response(
                    200,
                    json={
                        "model": body.get("model", "jev-latest"),
                        "answers": {
                            "t1_call": {"type": "noul", "noul": 0.82},
                            "t1_result": {"type": "noul", "noul": 0.22},
                        },
                        "usage": {"input_tokens": 850, "output_tokens": 8},
                    },
                )

            # Simulate uncertainty / deadband if question requested
            if "close_choice" in questions:
                return httpx.Response(
                    200,
                    json={
                        "model": "jev-latest",
                        "answers": {
                            "close_choice": {
                                "type": "choice",
                                "choice": "option_a",
                                "confidence": 0.51,
                                "probabilities": {"option_a": 0.51, "option_b": 0.49},
                            }
                        },
                        "usage": {"input_tokens": 40, "output_tokens": 2},
                    },
                )

            if "deadband_noul" in questions:
                return httpx.Response(
                    200,
                    json={
                        "model": "jev-latest",
                        "answers": {
                            "deadband_noul": {
                                "type": "noul",
                                "noul": 0.52,
                            }
                        },
                        "usage": {"input_tokens": 40, "output_tokens": 2},
                    },
                )

            # Default fixture response
            return httpx.Response(200, json=FIXTURE_SYSTEMONE_RESPONSE)

        elif path in ("/v1/models", "/models"):
            call_counts["models"] += 1
            return httpx.Response(
                200,
                json={"models": [{"name": "jev-latest", "description": "TypeSafe Jev", "release_date": "2026-09-15"}]},
            )

        return httpx.Response(404, json={"error": "not found"})

    handler.call_counts = call_counts
    transport = httpx.MockTransport(handler)
    transport.call_counts = call_counts  # type: ignore[attr-defined]
    return transport


@pytest.fixture
async def systemone_engine(tmp_path: Any, mock_typesafe_transport: httpx.MockTransport) -> JevFuseEngine:
    db_file = str(tmp_path / "sysone_decisions.db")
    cache_file = str(tmp_path / "sysone_cache.db")

    client = httpx.AsyncClient(transport=mock_typesafe_transport, base_url="https://api.typesafe.ai")
    driver = JevDriver(api_key="sk-test-key", http_client=client)

    engine = JevFuseEngine(
        provider=driver,
        db_path=db_file,
        cache_db_path=cache_file,
    )
    await engine.start()
    yield engine
    await engine.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_systemone_wire_conformance_and_proxy(
    systemone_engine: JevFuseEngine,
    mock_typesafe_transport: httpx.MockTransport,
) -> None:
    """Verify official POST /v1/systemone contract matching TypeSafe answers byte-for-byte."""
    app = create_app(engine=systemone_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        req_payload = {
            "model": "jev-latest",
            "state": "Customer requesting full refund for damaged package.",
            "questions": {
                "urgency_noul": {
                    "type": "noul",
                    "instructions": "Is this inquiry urgent?",
                },
                "intent_choice": {
                    "type": "choice",
                    "instructions": "What is customer intent?",
                    "criteria": {"refund": "wants money back", "cancellation": "cancel account", "general": "general"},
                },
                "quality_score": {
                    "type": "score",
                    "instructions": "Service rating",
                    "criteria": ["poor", "fair", "good", "excellent"],
                },
            },
        }

        # 1. Forward request through JEV Fuse proxy with custom auth header
        resp = await client.post(
            "/v1/systemone",
            json=req_payload,
            headers={"Authorization": "Bearer caller-custom-key"},
        )
        assert resp.status_code == 200
        res = resp.json()

        # 2. Check contract fidelity
        assert res["model"] == "jev-latest"
        assert res["usage"]["input_tokens"] == 145
        assert res["usage"]["output_tokens"] == 18

        # Check all 3 primitives
        answers = res["answers"]
        assert answers["urgency_noul"]["type"] == "noul"
        assert answers["urgency_noul"]["noul"] == 0.98

        assert answers["intent_choice"]["type"] == "choice"
        assert answers["intent_choice"]["choice"] == "refund"
        assert answers["intent_choice"]["confidence"] == 0.88
        assert answers["intent_choice"]["probabilities"]["refund"] == 0.88

        assert answers["quality_score"]["type"] == "score"
        assert answers["quality_score"]["score"] == 2.7
        assert answers["quality_score"]["confidence"] == 0.92

        # 3. Check response latency header populated
        assert "X-JevFuse-Latency-Ms" in resp.headers
        assert "X-Fuse-Latency-Ms" in resp.headers


@pytest.mark.asyncio
async def test_trojan_horse_fast_jev_compaction_integration(
    systemone_engine: JevFuseEngine,
) -> None:
    """
    Verify real-world fast-jev-compaction payload works with zero code changes.
    Compaction sends CompactionState object and pairs of noul questions (call & result).
    """
    app = create_app(engine=systemone_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        # Exact payload structure generated by fast-jev-compaction / src / request.ts
        compaction_payload = {
            "model": "jev-latest",
            "state": {
                "context": "Working on JEV Fuse proxy mode implementation.",
                "goal": "Implement universal systemone wire standard",
                "history": [
                    {
                        "i": 0,
                        "role": "user",
                        "text": "Please implement POST /v1/systemone proxy mode",
                    },
                    {
                        "i": 1,
                        "role": "assistant",
                        "text": "Running tests...",
                        "tool_calls": [
                            {
                                "id": "t1",
                                "tool": "run_command",
                                "input": "uv run pytest",
                                "result": "58 passed",
                            }
                        ],
                    },
                ],
            },
            "questions": {
                "t1_call": {
                    "type": "noul",
                    "instructions": "Should tool call t1 be kept in context?",
                },
                "t1_result": {
                    "type": "noul",
                    "instructions": "Should tool result t1 stay verbatim?",
                },
            },
        }

        # Test both /v1/systemone and /systemone alias
        for path in ("/v1/systemone", "/systemone"):
            resp = await client.post(path, json=compaction_payload)
            assert resp.status_code == 200
            res = resp.json()
            assert "answers" in res
            assert res["answers"]["t1_call"]["noul"] == 0.82
            assert res["answers"]["t1_result"]["noul"] == 0.22
            assert res["usage"]["input_tokens"] == 850


@pytest.mark.asyncio
async def test_governed_margin_overlay_and_abstention(
    systemone_engine: JevFuseEngine,
) -> None:
    """
    Verify JEV Fuse's core moat: deadband margin abstention overlay.
    - Choice margin p(top) - p(second) < 0.15 -> action: ask
    - Noul probability abs(noul - 0.5) < 0.10 -> action: ask
    """
    app = create_app(engine=systemone_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        # 1. Test close choice margin (0.51 vs 0.49 -> margin 0.02 < 0.15)
        resp_choice = await client.post(
            "/v1/systemone",
            json={
                "model": "jev-latest",
                "state": "Ambiguous message",
                "questions": {
                    "close_choice": {
                        "type": "choice",
                        "instructions": "Categorize",
                        "criteria": {"option_a": "a", "option_b": "b"},
                    }
                },
            },
        )
        assert resp_choice.status_code == 200
        res_choice = resp_choice.json()
        ans_choice = res_choice["answers"]["close_choice"]
        assert ans_choice["action"] == Action.ASK.value
        assert ans_choice["margin"] == 0.02
        assert "below deadband" in ans_choice["reason"]
        assert res_choice["governed"]["close_choice"]["action"] == Action.ASK.value

        # 2. Test noul deadband (noul = 0.52 -> within [0.40, 0.60])
        resp_noul = await client.post(
            "/v1/systemone",
            json={
                "model": "jev-latest",
                "state": "Uncertain statement",
                "questions": {
                    "deadband_noul": {
                        "type": "noul",
                        "instructions": "Is this definitely true?",
                    }
                },
            },
        )
        assert resp_noul.status_code == 200
        res_noul = resp_noul.json()
        ans_noul = res_noul["answers"]["deadband_noul"]
        assert ans_noul["action"] == Action.ASK.value
        assert "uncertainty band" in ans_noul["reason"]


@pytest.mark.asyncio
async def test_singleflight_concurrency_coalescing(
    systemone_engine: JevFuseEngine,
    mock_typesafe_transport: httpx.MockTransport,
) -> None:
    """Verify singleflight deduplicates 10 concurrent requests into 1 upstream call."""
    app = create_app(engine=systemone_engine)
    transport = ASGITransport(app=app)

    initial_upstream_calls = mock_typesafe_transport.call_counts["systemone"]  # type: ignore[attr-defined]

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        req_payload = {
            "model": "jev-latest",
            "state": "Concurrent singleflight benchmark state payload",
            "questions": {
                "t1_call": {"type": "noul", "instructions": "keep call?"},
            },
        }

        # Fire 10 simultaneous requests
        tasks = [client.post("/v1/systemone", json=req_payload) for _ in range(10)]
        responses = await asyncio.gather(*tasks)

        # All 10 succeeded with identical answer
        for r in responses:
            assert r.status_code == 200
            assert r.json()["answers"]["t1_call"]["noul"] == 0.82

        # Upstream was called exactly ONCE thanks to singleflight
        final_upstream_calls = mock_typesafe_transport.call_counts["systemone"]  # type: ignore[attr-defined]
        calls_made = final_upstream_calls - initial_upstream_calls
        assert calls_made == 1


@pytest.mark.asyncio
async def test_sqlite_decision_log_full_fidelity(
    systemone_engine: JevFuseEngine,
) -> None:
    """Verify full-fidelity SQLite decision logging preserving questions, answers, and tokens."""
    app = create_app(engine=systemone_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        req_payload = {
            "model": "jev-latest",
            "state": "Telemetry audit test state",
            "questions": {
                "t1_call": {"type": "noul", "instructions": "test audit"},
            },
        }
        resp = await client.post("/v1/systemone", json=req_payload)
        assert resp.status_code == 200

    # Flush log writer to SQLite
    await systemone_engine.log_writer.flush()

    # Query log reader
    traces = systemone_engine.log_reader.query_records(limit=5)
    assert len(traces) >= 1
    target_trace = next(t for t in traces if t.task == "systemone")

    assert target_trace.model == "jev-latest"
    assert target_trace.status == "success"
    assert target_trace.input_tokens > 0
    assert target_trace.questions_json is not None
    assert "t1_call" in target_trace.questions_json
    assert target_trace.answers_json is not None
    assert "0.82" in target_trace.answers_json


@pytest.mark.asyncio
async def test_typesafe_models_endpoint(
    systemone_engine: JevFuseEngine,
) -> None:
    """Verify GET /v1/models and GET /models compatibility."""
    app = create_app(engine=systemone_engine)
    transport = ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        for path in ("/v1/models", "/models"):
            resp = await client.get(path)
            assert resp.status_code == 200
            data = resp.json()
            assert "models" in data
            model_names = [m["name"] for m in data["models"]]
            assert "jev-latest" in model_names


@pytest.mark.asyncio
async def test_official_typesafe_python_sdk_roundtrip(
    systemone_engine: JevFuseEngine,
) -> None:
    """
    Principal Review milestone:
    Done when: the official Python SDK, configured only with base_url and an API key,
    runs a three-question request through JEV Fuse and the answers match a direct call,
    byte-for-byte on the answers object.
    """
    import uvicorn
    from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

    app = create_app(engine=systemone_engine)

    # Run lightweight uvicorn server on loopback port
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.3)

    try:
        # Configure official TypeSafe Python SDK with JEV Fuse base_url
        client = AsyncTypeSafeClient(
            base_url="http://127.0.0.1:8765",
            api_key="sk-test-caller-key",
        )

        res = await client.system_one(
            state="Customer requesting full refund for damaged package.",
            questions={
                "urgency_noul": Noul(instructions="Is this inquiry urgent?"),
                "intent_choice": Choice(
                    instructions="What is customer intent?",
                    criteria={"refund": "wants money back", "cancellation": "cancel account", "general": "general"},
                ),
                "quality_score": Score(
                    instructions="Service rating",
                    criteria=["poor", "fair", "good", "excellent"],
                ),
            },
        )

        # Assert full fidelity using official SDK typed objects
        assert res.model == "jev-latest"
        assert res.answers["urgency_noul"].noul == 0.98
        assert res.answers["intent_choice"].choice == "refund"
        assert res.answers["intent_choice"].confidence == 0.88
        assert res.answers["intent_choice"].probabilities["refund"] == 0.88
        assert res.answers["quality_score"].score == 2.7
        assert res.answers["quality_score"].confidence == 0.92
        assert res.usage.input_tokens == 145

        # Also test client.models.list() using official SDK
        models_res = await client.models.list()
        assert len(models_res.models) >= 1
        assert any(m.name == "jev-latest" for m in models_res.models)

    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_upstream_error_passthrough_status_codes(systemone_engine: JevFuseEngine) -> None:
    """Verify that Jev upstream errors (401, 429, 529) pass through with exact HTTP status codes."""
    from jevfuse.provider.exceptions import (
        ProviderUnavailableError,
        RateLimitExceededError,
    )
    app = create_app(engine=systemone_engine)

    # 1. Test 429 passthrough with Retry-After
    async def mock_rate_limit(*args, **kwargs):
        raise RateLimitExceededError("Rate limit exceeded", retry_after=4.0, status_code=429, raw_body=b'{"error": "rate_limited"}')

    systemone_engine.provider.system_one = mock_rate_limit  # type: ignore

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/systemone", json={"state": "test", "questions": {"q": {"type": "noul", "instructions": "test"}}})
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "4"
        assert b"rate_limited" in resp.content

    # 2. Test 529 site overloaded passthrough
    async def mock_overloaded(*args, **kwargs):
        raise ProviderUnavailableError("Site overloaded", status_code=529, raw_body=b'{"error": "site_overloaded"}')

    systemone_engine.provider.system_one = mock_overloaded  # type: ignore

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/v1/systemone", json={"state": "test", "questions": {"q": {"type": "noul", "instructions": "test"}}})
        assert resp.status_code == 529
        assert b"site_overloaded" in resp.content


@pytest.mark.asyncio
async def test_cache_hit_preserves_governed_overlay_action(
    systemone_engine: JevFuseEngine,
) -> None:
    """Verify that cache hits preserve stored overlay action (e.g. ASK) in the audit log."""
    db_path = systemone_engine.log_writer._db_path
    # Close mock and prepare engine with deadband response
    deadband_payload = {
        "model": "jev-latest",
        "state": "Customer uncertain query",
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": "Determine intent",
                "criteria": {"a": "option a", "b": "option b"},
            }
        },
    }
    deadband_res = SystemOneResponse(
        model="jev-latest",
        answers={
            "intent": ChoiceAnswer(
                choice="a",
                confidence=0.51,
                probabilities={"a": 0.51, "b": 0.49},  # margin = 0.02 < 0.15 -> ASK
            )
        },
        usage=Usage(input_tokens=10, output_tokens=1),
    )

    async def mock_deadband(*args, **kwargs):
        return deadband_res

    systemone_engine.provider.system_one = mock_deadband  # type: ignore

    # First call: upstream populates cache
    res1 = await systemone_engine.systemone(deadband_payload)
    assert res1.answers["intent"]["action"] == "ask"

    # Second call: cache hit!
    res2 = await systemone_engine.systemone(deadband_payload)
    assert res2.answers["intent"]["action"] == "ask"

    await systemone_engine.log_writer.flush()

    # Query SQLite decision log to verify cache hit recorded action = ask
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT action, cached FROM decisions WHERE provider = 'cache' ORDER BY rowid DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row["cached"] == 1
            assert row["action"] == "ask"

