"""Integration tests for JevDriver using httpx MockTransport."""

import json
import httpx
import pytest

from arbiter.provider import (
    DecisionProvider,
    JevDriver,
    ProviderAuthenticationError,
    ProviderTimeoutError,
    RateLimitExceededError,
)
from arbiter.schema import DecisionKind, NormalizedRequest


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
    with pytest.raises(ProviderAuthenticationError, match="JEV_API_KEY is not set"):
        JevDriver()


def test_jev_driver_protocol_conformance():
    driver = JevDriver(api_key="mock_key")
    assert isinstance(driver, DecisionProvider)
    assert driver.name == "jev"


@pytest.mark.asyncio
async def test_jev_driver_single_infer_choice():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/decide"
        assert request.headers["Authorization"] == "Bearer mock_key"
        data = json.loads(request.content)
        assert data["task"] == "element-selection"
        assert data["choices"] == ["#btn-submit", "#btn-cancel"]
        return httpx.Response(
            200,
            json={
                "value": "#btn-submit",
                "score": 0.94,
                "candidate_scores": {"#btn-submit": 0.94, "#btn-cancel": 0.06},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai/v1") as client:
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
        if data["kind"] == "score":
            return httpx.Response(200, json={"value": 0.85, "score": 0.85})
        elif data["kind"] == "bool":
            return httpx.Response(200, json={"value": "true", "score": 0.92})
        return httpx.Response(400, json={"error": "unknown"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai/v1") as client:
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
        assert request.url.path == "/v1/decide/batch"
        data = json.loads(request.content)
        assert len(data["requests"]) == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {"value": "#btn-submit", "score": 0.95},
                    {"value": 0.12, "score": 0.12},
                ]
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai/v1") as client:
        driver = JevDriver(api_key="mock_key", http_client=client)
        req1 = make_norm_request(task="t1", kind=DecisionKind.CHOICE)
        req2 = make_norm_request(task="t2", kind=DecisionKind.SCORE, choices=None)
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
        return httpx.Response(200, json={"value": "safe", "score": 0.98})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai/v1") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai/v1") as client:
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
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai/v1") as client:
        driver = JevDriver(api_key="bad_key", max_retries=3, http_client=client)
        req = make_norm_request()
        with pytest.raises(ProviderAuthenticationError, match="rejected credentials"):
            await driver.infer([req])
        assert call_count == 1  # Did not waste retries on 401


@pytest.mark.asyncio
async def test_jev_driver_health():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"status": "ok", "version": "1.4.0"})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.typesafe.ai/v1") as client:
        driver = JevDriver(api_key="mock_key", http_client=client)
        health = await driver.health()
        assert health.status == "healthy"
        assert health.latency_ms is not None
        assert health.details["version"] == "1.4.0"
