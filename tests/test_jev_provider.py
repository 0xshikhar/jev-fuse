"""Integration tests for JevDriver using httpx MockTransport targeting POST /v1/systemone."""

import json

import httpx
import pytest

from jevfuse.provider import (
    DecisionProvider,
    JevDriver,
    ProviderAuthenticationError,
    RateLimitExceededError,
)
from jevfuse.schema import (
    DecisionKind,
    NormalizedRequest,
    SystemOneRequest,
)


def make_norm_request(
    task: str = "element-selection",
    kind: DecisionKind = DecisionKind.CHOICE,
    input_text: str = "Click submit button",
    choices: tuple[str, ...] | None = ("#btn-submit", "#btn-cancel"),
) -> NormalizedRequest:
    return NormalizedRequest(
        trace_id="tr_test_001",
        task=task,
        kind=kind,
        input=input_text,
        choices=choices,
        client_id="test-client",
        provider="jev",
        input_hash="hash_12345",
        timestamp_ns=1700000000000000000,
    )


def test_jev_driver_missing_api_key(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("VERCEL_AI_GATEWAY_API_KEY", raising=False)
    with pytest.raises(ProviderAuthenticationError, match="TYPESAFE_API_KEY"):
        JevDriver()


def test_jev_driver_ai_gateway_auto_detection(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    monkeypatch.delenv("JEV_BASE_URL", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_mock_test")
    driver = JevDriver()
    assert driver._base_url == "https://ai-gateway.vercel.sh/typesafe"
    assert driver._api_key == "vck_mock_test"


def test_jev_driver_protocol_conformance():
    driver = JevDriver(api_key="mock_key")
    assert isinstance(driver, DecisionProvider)
    assert driver.name == "jev"


@pytest.mark.asyncio
async def test_jev_driver_system_one_direct():
    """Verify direct invocation of system_one method with SystemOneRequest."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/systemone"
        assert request.headers["Authorization"] == "Bearer mock_key"
        data = json.loads(request.content)
        assert data["model"] == "jev-latest"
        assert data["state"] == "User payment processed twice"
        assert "billing" in data["questions"]
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "billing": {
                        "type": "noul",
                        "noul": 0.98,
                    }
                },
                "usage": {"input_tokens": 42, "output_tokens": 1},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="mock_key", http_client=client)
        req = SystemOneRequest(
            state="User payment processed twice",
            model="jev-latest",
            questions={"billing": {"type": "noul", "instructions": "Is this billing related?"}},
        )
        res = await driver.system_one(req)
        assert res.model == "jev-latest"
        assert res.answers["billing"]["noul"] == 0.98
        assert res.usage.input_tokens == 42


@pytest.mark.asyncio
async def test_jev_driver_single_infer_choice():
    """Verify NormalizedRequest infer calls POST /v1/systemone."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/systemone"
        assert request.headers["Authorization"] == "Bearer mock_key"
        data = json.loads(request.content)
        assert data["state"] == "Click submit button"
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "q_0_element-selection": {
                        "type": "choice",
                        "choice": "#btn-submit",
                        "confidence": 0.94,
                        "probabilities": {"#btn-submit": 0.94, "#btn-cancel": 0.06},
                    }
                },
                "usage": {"input_tokens": 20, "output_tokens": 2},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="mock_key", http_client=client)
        req = make_norm_request()
        scores = await driver.infer([req])

        assert len(scores) == 1
        assert scores[0].value == "#btn-submit"
        assert scores[0].raw_score == 0.94
        assert scores[0].candidate_scores == {"#btn-submit": 0.94, "#btn-cancel": 0.06}


@pytest.mark.asyncio
async def test_jev_driver_single_infer_score_and_bool():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        answers = {}
        for k, v in data["questions"].items():
            if v["type"] == "score":
                answers[k] = {"type": "score", "score": 0.85, "confidence": 0.85, "legend": {}, "probabilities": {"3": 0.85}}
            elif v["type"] == "noul":
                answers[k] = {"type": "noul", "noul": 0.92}
        return httpx.Response(200, json={"model": "jev-latest", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 1}})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="mock_key", http_client=client)

        score_req = make_norm_request(task="bash-risk", kind=DecisionKind.SCORE, choices=None)
        score_res = await driver.infer([score_req])
        assert score_res[0].value == 0.85

        bool_req = make_norm_request(task="verify-patch", kind=DecisionKind.BOOL, choices=None)
        bool_res = await driver.infer([bool_req])
        assert bool_res[0].value is True
        assert bool_res[0].raw_score == 0.92


@pytest.mark.asyncio
async def test_jev_driver_batch_infer():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/systemone"
        data = json.loads(request.content)
        answers = {}
        for k in data["questions"]:
            if "t1" in k:
                answers[k] = {"type": "choice", "choice": "#btn-submit", "confidence": 0.95, "probabilities": {"#btn-submit": 0.95}}
            else:
                answers[k] = {"type": "score", "score": 0.12, "confidence": 0.12, "legend": {}, "probabilities": {"0": 0.12}}
        return httpx.Response(
            200,
            json={"model": "jev-latest", "answers": answers, "usage": {"input_tokens": 30, "output_tokens": 2}},
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="mock_key", http_client=client)
        req1 = make_norm_request(task="t1", kind=DecisionKind.CHOICE, input_text="shared input")
        req2 = make_norm_request(task="t2", kind=DecisionKind.SCORE, choices=None, input_text="shared input")
        scores = await driver.infer([req1, req2])

        assert len(scores) == 2
        assert scores[0].value == "#btn-submit"
        assert scores[1].value == 0.12


@pytest.mark.asyncio
async def test_jev_driver_rate_limit_backoff_and_recovery():
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={
            "model": "jev-latest",
            "answers": {
                "q_0_element-selection": {
                    "type": "choice",
                    "choice": "safe",
                    "confidence": 0.98,
                    "probabilities": {"safe": 0.98},
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 1},
        })

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="mock_key", max_retries=2, http_client=client)
        req = make_norm_request()
        scores = await driver.infer([req])

        assert len(scores) == 1
        assert scores[0].value == "safe"
        assert call_count == 2


@pytest.mark.asyncio
async def test_jev_driver_rate_limit_exhausted():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="mock_key", max_retries=2, http_client=client)
        req = make_norm_request()
        with pytest.raises(RateLimitExceededError):
            await driver.infer([req])


@pytest.mark.asyncio
async def test_jev_driver_auth_failure_no_retries():
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, json={"error": "Invalid API key"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="bad_key", max_retries=3, http_client=client)
        req = make_norm_request()
        with pytest.raises(ProviderAuthenticationError, match="rejected credentials"):
            await driver.infer([req])
        assert call_count == 1


@pytest.mark.asyncio
async def test_jev_driver_health():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"models": [{"name": "jev-latest", "description": "TypeSafe System One", "release_date": "2026-09-15"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai") as client:
        driver = JevDriver(api_key="mock_key", http_client=client)
        health = await driver.health()
        assert health.status == "healthy"
        assert health.latency_ms is not None
        assert "models" in health.details
