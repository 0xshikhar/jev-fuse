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
from arbiter.schema.systemone import (
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
    SystemOneRequest,
    SystemOneResponse,
    Usage,
)

DEFAULT_TYPESAFE_BASE_URL = "https://api.typesafe.ai"
DEFAULT_TIMEOUT_MS = 10000.0  # 10s matching official SDK
DEFAULT_MAX_RETRIES = 3


class JevDriver(DecisionProvider):
    """Hosted provider communicating with TypeSafe Jev cloud decision API over HTTP/2 using POST /v1/systemone."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_ms: float = DEFAULT_TIMEOUT_MS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ):
        resolved_key = (
            api_key
            or os.environ.get("TYPESAFE_API_KEY")
            or os.environ.get("JEV_API_KEY")
        )
        if not resolved_key:
            raise ProviderAuthenticationError(
                "TYPESAFE_API_KEY (or JEV_API_KEY) is not set. Please export TYPESAFE_API_KEY or provide api_key."
            )

        self._api_key = resolved_key
        raw_base = (
            base_url
            or os.environ.get("TYPESAFE_BASE_URL")
            or os.environ.get("JEV_BASE_URL")
            or DEFAULT_TYPESAFE_BASE_URL
        ).rstrip("/")
        # If user passed url with /v1 at the end, strip it so base client targets root
        if raw_base.endswith("/v1"):
            raw_base = raw_base[:-3]
        self._base_url = raw_base
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
                    "User-Agent": "arbiter-runtime/0.1.0 (TypeSafe-Proxy)",
                    "Content-Type": "application/json",
                },
            )
            self._owns_client = True

    @property
    def name(self) -> str:
        return "jev"

    async def _send_with_retries(
        self,
        method: str,
        path: str,
        json_data: Any,
        custom_auth: str | None = None,
    ) -> dict[str, Any]:
        """Execute HTTP request with exponential backoff for 429 and transient 5xx."""
        attempt = 0
        backoff_base = 0.1

        auth = custom_auth or f"Bearer {self._api_key}"
        headers = {
            "Authorization": auth,
            "User-Agent": "arbiter-runtime/0.1.0",
            "Content-Type": "application/json",
        }

        while True:
            try:
                response = await self._client.request(method, path, json=json_data, headers=headers)

                # Check 401/403 auth issues
                if response.status_code in (401, 403):
                    raise ProviderAuthenticationError(
                        f"TypeSafe API rejected credentials (status {response.status_code}): {response.text}"
                    )

                # Check 429 rate limit
                if response.status_code == 429:
                    attempt += 1
                    if attempt > self._max_retries:
                        retry_after_str = response.headers.get("Retry-After")
                        retry_after = float(retry_after_str) if retry_after_str and retry_after_str.isdigit() else None
                        raise RateLimitExceededError(
                            f"TypeSafe API rate limit exceeded after {self._max_retries} retries",
                            retry_after=retry_after,
                        )
                    sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                    await asyncio.sleep(sleep_time)
                    continue

                # Check 5xx transient server errors
                if 500 <= response.status_code < 600:
                    attempt += 1
                    if attempt > self._max_retries:
                        raise ProviderUnavailableError(
                            f"TypeSafe API unavailable (status {response.status_code}): {response.text}"
                        )
                    sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                    await asyncio.sleep(sleep_time)
                    continue

                # Check 4xx client errors
                if 400 <= response.status_code < 500:
                    raise ProviderError(
                        f"TypeSafe API client error (status {response.status_code}): {response.text}",
                        error_code="validation_error",
                    )

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise ProviderTimeoutError(
                        f"TypeSafe API timed out after {self._timeout_seconds * 1000:.0f}ms ({exc})"
                    ) from exc
                sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                await asyncio.sleep(sleep_time)

            except httpx.RequestError as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise ProviderUnavailableError(
                        f"Failed to connect to TypeSafe API ({exc})"
                    ) from exc
                sleep_time = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.01, 0.05)
                await asyncio.sleep(sleep_time)

    async def system_one(
        self,
        request: SystemOneRequest,
        auth_header: str | None = None,
    ) -> SystemOneResponse:
        """Call official TypeSafe Jev POST /v1/systemone endpoint."""
        body = {
            "model": request.model,
            "state": request.state,
            "questions": request.questions,
        }
        res_data = await self._send_with_retries(
            "POST",
            "/v1/systemone",
            body,
            custom_auth=auth_header,
        )
        usage_data = res_data.get("usage", {})
        return SystemOneResponse(
            model=res_data.get("model", request.model),
            answers=res_data.get("answers", {}),
            usage=Usage(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
            ),
        )

    def _parse_answer_to_raw_score(self, ans: dict[str, Any], kind: DecisionKind) -> RawScore:
        """Parse native TypeSafe systemone answer into RawScore."""
        ans_type = ans.get("type")
        if ans_type == "choice" or kind == DecisionKind.CHOICE:
            choice = ans.get("choice", "")
            confidence = float(ans.get("confidence", 0.0))
            probs = ans.get("probabilities", {})
            return RawScore(
                value=choice,
                raw_score=confidence,
                candidate_scores=probs if isinstance(probs, dict) else None,
            )
        elif ans_type == "score" or kind == DecisionKind.SCORE:
            score = float(ans.get("score", 0.0))
            confidence = float(ans.get("confidence", score))
            probs = ans.get("probabilities")
            return RawScore(
                value=score,
                raw_score=confidence,
                candidate_scores=probs if isinstance(probs, dict) else None,
            )
        else:  # noul or bool
            noul = float(ans.get("noul", 0.5))
            return RawScore(
                value=bool(noul >= 0.5),
                raw_score=noul,
                candidate_scores={"true": noul, "false": round(1.0 - noul, 4)},
            )

    async def infer(self, batch: Sequence[NormalizedRequest]) -> list[RawScore]:
        """
        Execute normalized requests using standard POST /v1/systemone.
        Folds multiple questions into one single forward pass against state.
        """
        if not batch:
            return []

        # Check if all requests share the same input (common in fan-out evaluations)
        first_input = batch[0].input
        all_same_input = all(r.input == first_input for r in batch)

        if all_same_input:
            questions: dict[str, Any] = {}
            for i, req in enumerate(batch):
                q_key = f"q_{i}_{req.task}"
                if req.kind == DecisionKind.CHOICE:
                    crit: dict[str, str] = {}
                    if req.choices:
                        for c in req.choices:
                            crit[c] = c
                    else:
                        crit = {"option_a": "option_a", "option_b": "option_b"}
                    questions[q_key] = {
                        "type": "choice",
                        "instructions": req.task,
                        "criteria": crit,
                    }
                elif req.kind == DecisionKind.SCORE:
                    questions[q_key] = {
                        "type": "score",
                        "instructions": req.task,
                        "criteria": ["low", "medium", "high", "critical"],
                    }
                else:
                    questions[q_key] = {
                        "type": "noul",
                        "instructions": req.task,
                    }

            sys_req = SystemOneRequest(
                state=first_input,
                model="jev-latest",
                questions=questions,
            )
            sys_res = await self.system_one(sys_req)
            return [
                self._parse_answer_to_raw_score(sys_res.answers.get(f"q_{i}_{req.task}", {}), req.kind)
                for i, req in enumerate(batch)
            ]

        # Heterogeneous inputs -> run concurrent single-request evaluations
        async def evaluate_single(r: NormalizedRequest) -> RawScore:
            q_key = r.task
            if r.kind == DecisionKind.CHOICE:
                crit = {c: c for c in r.choices} if r.choices else {"a": "a", "b": "b"}
                q_spec = {"type": "choice", "instructions": r.task, "criteria": crit}
            elif r.kind == DecisionKind.SCORE:
                q_spec = {"type": "score", "instructions": r.task, "criteria": ["low", "medium", "high", "critical"]}
            else:
                q_spec = {"type": "noul", "instructions": r.task}

            sys_req = SystemOneRequest(
                state=r.input,
                model="jev-latest",
                questions={q_key: q_spec},
            )
            sys_res = await self.system_one(sys_req)
            return self._parse_answer_to_raw_score(sys_res.answers.get(q_key, {}), r.kind)

        return await asyncio.gather(*(evaluate_single(r) for r in batch))

    async def health(self) -> ProviderHealth:
        """Probe TypeSafe API liveness via GET /v1/models and report latency."""
        start_time = time.perf_counter()
        try:
            resp = await self._send_with_retries("GET", "/v1/models", None)
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
                message=f"TypeSafe health check failed: {str(exc)}",
            )

    async def close(self) -> None:
        """Close underlying HTTP client if owned."""
        if self._owns_client and hasattr(self, "_client"):
            await self._client.aclose()

    async def __aenter__(self) -> "JevDriver":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
