"""TypeSafe Jev hosted decision provider implementation."""

import asyncio
import os
import random
import time
from typing import Any, Sequence
import httpx

from arbiter.provider.base import DecisionProvider, ProviderHealth
from arbiter.provider.exceptions import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitExceededError,
)
from arbiter.schema.decision import DecisionKind
from arbiter.schema.internal import NormalizedRequest, RawScore

DEFAULT_JEV_BASE_URL = "https://api.typesafe.ai/v1"
DEFAULT_TIMEOUT_MS = 800.0
DEFAULT_MAX_RETRIES = 3


class JevDriver(DecisionProvider):
    """Hosted provider communicating with TypeSafe Jev cloud decision API over HTTP/2."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ):
        resolved_key = api_key or os.environ.get("JEV_API_KEY")
        if not resolved_key:
            raise ProviderAuthenticationError(
                "JEV_API_KEY is not set. Please export JEV_API_KEY or provide api_key to JevDriver."
            )

        self._api_key = resolved_key
        self._base_url = (base_url or os.environ.get("JEV_BASE_URL") or DEFAULT_JEV_BASE_URL).rstrip("/")
        self._timeout_seconds = timeout_ms / 1000.0
        self._max_retries = max_retries

        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                http2=True,
                timeout=httpx.Timeout(self._timeout_seconds),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "User-Agent": "arbiter-runtime/0.1.0",
                    "Content-Type": "application/json",
                },
            )
            self._owns_client = True

    @property
    def name(self) -> str:
        return "jev"

    def _format_request_payload(self, req: NormalizedRequest) -> dict[str, Any]:
        """Convert NormalizedRequest to Jev API JSON format."""
        payload: dict[str, Any] = {
            "task": req.task,
            "kind": req.kind.value,
            "input": req.input,
            "trace_id": req.trace_id,
            "client_id": req.client_id,
            "context": req.context,
        }
        if req.choices is not None:
            payload["choices"] = list(req.choices)
        return payload

    def _parse_raw_score(self, item: dict[str, Any], kind: DecisionKind) -> RawScore:
        """Parse raw response payload into canonical RawScore."""
        raw_val = item.get("value")
        score_val = float(item.get("score") if item.get("score") is not None else item.get("raw_score", 0.0))
        cand_scores = item.get("candidate_scores")

        # Cast value appropriately
        typed_val: str | float | bool
        if kind == DecisionKind.BOOL:
            if isinstance(raw_val, bool):
                typed_val = raw_val
            elif isinstance(raw_val, str):
                typed_val = raw_val.strip().lower() in ("true", "1", "yes", "allow")
            else:
                typed_val = bool(raw_val)
        elif kind == DecisionKind.SCORE:
            typed_val = float(raw_val) if raw_val is not None else score_val
        else:
            typed_val = str(raw_val)

        return RawScore(
            value=typed_val,
            raw_score=score_val,
            candidate_scores=cand_scores,
        )

    async def _send_with_retries(self, method: str, url: str, json_data: Any) -> dict[str, Any]:
        """Execute HTTP request with exponential backoff for 429 and transient 5xx."""
        attempt = 0
        backoff_base = 0.1  # 100ms base

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "arbiter-runtime/0.1.0",
        }

        while True:
            try:
                response = await self._client.request(method, url, json=json_data, headers=headers)
                
                # Check 401/403 auth issues
                if response.status_code in (401, 403):
                    raise ProviderAuthenticationError(
                        f"TypeSafe Jev API rejected credentials (status {response.status_code}): {response.text}"
                    )

                # Check 429 rate limit
                if response.status_code == 429:
                    attempt += 1
                    if attempt > self._max_retries:
                        retry_after_str = response.headers.get("Retry-After")
                        retry_after = float(retry_after_str) if retry_after_str and retry_after_str.isdigit() else None
                        raise RateLimitExceededError(
                            f"TypeSafe Jev API rate limit exceeded after {self._max_retries} retries",
                            retry_after=retry_after,
                        )
                    # Exponential backoff with jitter
                    sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                    await asyncio.sleep(sleep_time)
                    continue

                # Check 5xx transient server errors
                if 500 <= response.status_code < 600:
                    attempt += 1
                    if attempt > self._max_retries:
                        raise ProviderUnavailableError(
                            f"TypeSafe Jev API unavailable (status {response.status_code}): {response.text}"
                        )
                    sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                    await asyncio.sleep(sleep_time)
                    continue

                # Check any other 4xx errors
                if 400 <= response.status_code < 500:
                    raise ProviderError(
                        f"TypeSafe Jev API client error (status {response.status_code}): {response.text}",
                        error_code="validation_error",
                    )

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise ProviderTimeoutError(
                        f"TypeSafe Jev API timed out after {self._timeout_seconds * 1000:.0f}ms ({exc})"
                    ) from exc
                sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                await asyncio.sleep(sleep_time)

            except httpx.RequestError as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise ProviderUnavailableError(
                        f"Failed to connect to TypeSafe Jev API ({exc})"
                    ) from exc
                sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                await asyncio.sleep(sleep_time)

    async def infer(self, batch: Sequence[NormalizedRequest]) -> list[RawScore]:
        """Execute a batch of normalized decision requests against Jev API."""
        if not batch:
            return []

        if len(batch) == 1:
            req = batch[0]
            payload = self._format_request_payload(req)
            result_data = await self._send_with_retries("POST", "/decide", payload)
            return [self._parse_raw_score(result_data, req.kind)]

        # Multi-request batch execution
        batch_payload = {"requests": [self._format_request_payload(r) for r in batch]}
        try:
            batch_result = await self._send_with_retries("POST", "/decide/batch", batch_payload)
            results = batch_result.get("results", [])
            if len(results) != len(batch):
                raise ProviderError(
                    f"Jev API returned mismatched batch length (expected {len(batch)}, got {len(results)})"
                )
            return [self._parse_raw_score(res, req.kind) for res, req in zip(results, batch)]
        except ProviderError as e:
            # If batch endpoint is not supported, fall back to concurrent single requests
            if e.error_code == "validation_error":
                tasks = [self.infer([r]) for r in batch]
                single_results = await asyncio.gather(*tasks)
                return [r[0] for r in single_results]
            raise

    async def health(self) -> ProviderHealth:
        """Probe Jev API liveness and report round-trip latency."""
        start_time = time.perf_counter()
        try:
            resp = await self._send_with_retries("GET", "/health", None)
            latency = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                status="healthy",
                latency_ms=round(latency, 2),
                message="Connected to TypeSafe Jev API",
                details=resp if isinstance(resp, dict) else {},
            )
        except Exception as exc:
            latency = (time.perf_counter() - start_time) * 1000.0
            return ProviderHealth(
                status="unhealthy",
                latency_ms=round(latency, 2),
                message=f"Jev health check failed: {str(exc)}",
            )

    async def close(self) -> None:
        """Close underlying HTTP client if owned."""
        if self._owns_client and hasattr(self, "_client"):
            await self._client.aclose()

    async def __aenter__(self) -> "JevDriver":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
