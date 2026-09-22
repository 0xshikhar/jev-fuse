"""Arbiter Runtime Engine: Cohesive control plane coordinating cache, batcher, policy, and logging."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Sequence

from arbiter.batch.batcher import MicroBatcher
from arbiter.cache.hasher import compute_input_hash
from arbiter.cache.store import CompositeCache
from arbiter.log.models import DecisionRecord
from arbiter.log.reader import DecisionLogReader
from arbiter.log.writer import DecisionLogWriter
from arbiter.policy.engine import PolicyEngine
from arbiter.policy.schema import PolicyContext, PolicyDefinition, PolicyRule, RuleCondition
from arbiter.provider.base import DecisionProvider, ProviderHealth
from arbiter.provider.jev import JevDriver
from arbiter.schema.decision import Action, DecisionKind, DecisionRequest, DecisionResponse
from arbiter.schema.internal import NormalizedRequest, RawScore

logger = logging.getLogger(__name__)


def create_default_policy_engine() -> PolicyEngine:
    """Initialize a PolicyEngine with default security and agent governance policies."""
    engine = PolicyEngine(default_unrouted_action=Action.ASK)

    # 1. Shell Safety Guard Policy (arbiter-guard)
    # Binary task: is_destructive. If confident that destructive -> DENY. If confident safe -> ALLOW. Otherwise ASK.
    guard_policy = PolicyDefinition(
        task="shell-guard",
        version=1,
        default_action=Action.ASK,
        rules=[
            PolicyRule(
                when=RuleCondition(value=True, confidence_gte=0.85),
                then=Action.DENY,
            ),
            PolicyRule(
                when=RuleCondition(value=False, confidence_gte=0.80),
                then=Action.ALLOW,
            ),
            PolicyRule(
                when=RuleCondition(confidence_lt=0.80),
                then=Action.ASK,
            ),
        ],
    )
    engine.register_policy(guard_policy)

    # 2. Context Pruning Policy (arbiter-prune)
    # Score task: relevance (0.0 to 1.0). High relevance -> KEEP, mid -> TRUNCATE, low -> DROP.
    prune_policy = PolicyDefinition(
        task="context-prune",
        version=1,
        default_action=Action.KEEP,
        rules=[
            PolicyRule(
                when=RuleCondition(confidence_gte=0.75),
                then=Action.KEEP,
            ),
            PolicyRule(
                when=RuleCondition(confidence_gte=0.35, confidence_lt=0.75),
                then=Action.TRUNCATE,
            ),
            PolicyRule(
                when=RuleCondition(confidence_lt=0.35),
                then=Action.DROP,
            ),
        ],
    )
    engine.register_policy(prune_policy)

    return engine


class ArbiterEngine:
    """The central unified runtime engine for Arbiter."""

    def __init__(
        self,
        provider: DecisionProvider | None = None,
        cache: CompositeCache | None = None,
        policy_engine: PolicyEngine | None = None,
        log_writer: DecisionLogWriter | None = None,
        db_path: str = "arbiter_decisions.db",
        cache_db_path: str = "arbiter_cache.db",
    ) -> None:
        # 1. Decision Provider (Defaults to JevDriver or fallback)
        if provider is None:
            api_key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "mock-key")
            self.provider = JevDriver(api_key=api_key)
        else:
            self.provider = provider

        # 2. Adaptive Micro-Batcher
        self.batcher = MicroBatcher(
            provider=self.provider,
            window_floor_ms=8.0,
            window_ceiling_ms=25.0,
            max_batch_size=64,
            adaptive=True,
        )

        # 3. Two-Tier Cache (L1 Memory + L2 SQLite)
        if cache is None:
            self.cache = CompositeCache(memory_max_size=2048, sqlite_path=cache_db_path, default_ttl_seconds=3600)
        else:
            self.cache = cache

        # 4. Declarative Policy Engine
        self.policy_engine = policy_engine or create_default_policy_engine()

        # 5. SQLite WAL Decision Log & DuckDB Reader
        self.db_path = db_path
        self.log_writer = log_writer or DecisionLogWriter(db_path=db_path)
        self.log_reader = DecisionLogReader(db_path=db_path)

        self._started = False

    async def start(self) -> None:
        """Start async background tasks (log writer, batcher)."""
        if self._started:
            return
        await self.log_writer.start()
        await self.batcher.start()
        self._started = True

    async def close(self) -> None:
        """Gracefully shutdown all components."""
        self._started = False
        await self.batcher.close()
        await self.log_writer.close()
        await self.cache.close()
        await self.provider.close()
        self.log_reader.close()

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Process a canonical decision request through the full policy pipeline."""
        if not self._started:
            await self.start()

        t0 = time.monotonic()

        # 1. Compute deterministic canonical hash
        input_hash = compute_input_hash(
            task=request.task,
            input_text=request.input,
            choices=request.choices,
            context=request.context,
            idempotency_key=request.idempotency_key,
        )

        # 2. Check Two-Tier Composite Cache (sub-millisecond hot path)
        cached_entry = await self.cache.get(input_hash)
        if cached_entry is not None:
            raw_score = cached_entry.raw_score
            confidence = max(0.0, min(1.0, float(raw_score.raw_score)))
            policy_ctx = PolicyContext(
                task=request.task,
                client_id=request.client_id,
                value=raw_score.value,
                confidence=confidence,
                context=request.context,
            )
            action, reason = self.policy_engine.evaluate(policy_ctx)
            latency_ms = round((time.monotonic() - t0) * 1000.0, 3)

            cached_trace_id = str(uuid.uuid4())
            self.log_writer.log(
                DecisionRecord(
                    trace_id=cached_trace_id,
                    task=request.task,
                    client_id=request.client_id,
                    provider=cached_entry.provider,
                    input_hash=input_hash,
                    input_preview=request.input[:500] if request.input else None,
                    decision_value=raw_score.value,
                    raw_score=raw_score.raw_score,
                    confidence=confidence,
                    action=action,
                    latency_ms=latency_ms,
                    cached=True,
                    context=request.context or {},
                    reason=reason,
                )
            )

            return DecisionResponse(
                trace_id=cached_trace_id,
                task=request.task,
                provider=cached_entry.provider,
                cached=True,
                value=raw_score.value,
                raw_score=raw_score.raw_score,
                confidence=confidence,
                action=action,
                latency_ms=latency_ms,
                checkpoint_or_model=cached_entry.provider,
                reason=reason,
            )

        # 3. Cache Miss: Normalize and Enqueue in MicroBatcher
        trace_id = str(uuid.uuid4())
        norm_req = NormalizedRequest(
            trace_id=trace_id,
            task=request.task,
            kind=request.kind,
            input=request.input,
            choices=tuple(request.choices) if request.choices else None,
            client_id=request.client_id,
            provider=self.provider.name,
            context=request.context,
            input_hash=input_hash,
            timestamp_ns=time.time_ns(),
        )

        provider_err: str | None = None
        try:
            raw_score = await self.batcher.enqueue(norm_req)
        except Exception as e:
            provider_err = str(e)
            raw_score = RawScore(
                value=False if request.kind == DecisionKind.BOOL else (request.choices[0] if request.choices else 0.0),
                raw_score=0.0,
            )

        # 4. Calibration & Confidence Evaluation
        confidence = max(0.0, min(1.0, float(raw_score.raw_score)))

        # 5. Declarative Policy Evaluation
        policy_ctx = PolicyContext(
            task=request.task,
            client_id=request.client_id,
            value=raw_score.value,
            confidence=confidence,
            context=request.context,
            provider_error=provider_err,
        )
        action, reason = self.policy_engine.evaluate(policy_ctx)

        latency_ms = round((time.monotonic() - t0) * 1000.0, 3)

        # 6. Build Final Response
        response = DecisionResponse(
            trace_id=trace_id,
            task=request.task,
            provider=self.provider.name,
            cached=False,
            value=raw_score.value,
            raw_score=raw_score.raw_score,
            confidence=confidence,
            action=action,
            latency_ms=latency_ms,
            checkpoint_or_model=self.provider.name,
            reason=reason,
        )

        # 7. Asynchronous Telemetry Logging (Zero latency overhead via queue)
        self.log_writer.log(
            DecisionRecord(
                trace_id=trace_id,
                task=request.task,
                client_id=request.client_id,
                provider=self.provider.name,
                input_hash=input_hash,
                input_preview=request.input[:500] if request.input else None,
                decision_value=raw_score.value,
                raw_score=raw_score.raw_score,
                confidence=confidence,
                action=action,
                latency_ms=latency_ms,
                cached=False,
                context=request.context or {},
                reason=reason,
            )
        )

        # 8. Save to Two-Tier Cache for future idempotency
        if provider_err is None:
            await self.cache.set(input_hash, raw_score, self.provider.name)

        return response

    async def systemone(self, payload: dict[str, Any]) -> dict[str, Any]:
        """TypeSafe Jev 100% drop-in endpoint compatibility (POST /v1/systemone)."""
        state = payload.get("state", "")
        questions: dict[str, Any] = payload.get("questions", {})

        t0 = time.monotonic()
        answers: dict[str, Any] = {}

        # Execute all questions concurrently through Arbiter's engine
        async def evaluate_question(q_key: str, q_spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            q_type = q_spec.get("type", "noul")
            instructions = q_spec.get("instructions", "")
            criteria = q_spec.get("criteria")

            if q_type == "choice":
                choices_list: list[str] = []
                if isinstance(criteria, dict):
                    choices_list = list(criteria.keys())
                elif isinstance(criteria, list):
                    choices_list = [str(c) for c in criteria]
                else:
                    choices_list = ["option_a", "option_b"]

                req = DecisionRequest(
                    task=f"systemone-{q_key}",
                    kind=DecisionKind.CHOICE,
                    input=f"{instructions}\n\nState: {state}",
                    choices=choices_list,
                )
            elif q_type == "score":
                req = DecisionRequest(
                    task=f"systemone-{q_key}",
                    kind=DecisionKind.SCORE,
                    input=f"{instructions}\n\nState: {state}",
                )
            else:  # noul (boolean probability)
                req = DecisionRequest(
                    task=f"systemone-{q_key}",
                    kind=DecisionKind.BOOL,
                    input=f"{instructions}\n\nState: {state}",
                )

            res = await self.decide(req)

            if q_type == "choice":
                return q_key, {
                    "type": "choice",
                    "choice": str(res.value),
                    "probabilities": {str(res.value): res.confidence},
                    "confidence": res.confidence,
                    "action": res.action.value,
                }
            elif q_type == "score":
                return q_key, {
                    "type": "score",
                    "score": float(res.value) if isinstance(res.value, (int, float)) else res.raw_score,
                    "confidence": res.confidence,
                    "action": res.action.value,
                }
            else:  # noul
                return q_key, {
                    "type": "noul",
                    "noul": res.confidence,
                    "confidence": res.confidence,
                    "action": res.action.value,
                }

        results = await asyncio.gather(*(evaluate_question(k, v) for k, v in questions.items()))
        for k, ans in results:
            answers[k] = ans

        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        total_tokens = len(state.split()) + 50

        return {
            "model": f"arbiter-{self.provider.name}",
            "answers": answers,
            "usage": {"input_tokens": total_tokens, "output_tokens": 0},
            "routing": {"model": self.provider.name, "reason": "arbiter-governed"},
            "latency_ms": elapsed_ms,
        }

    async def record_feedback(self, trace_id: str, label: str) -> bool:
        """Record ground truth human label for active learning and calibration."""
        return await self.log_writer.attach_label(trace_id, label)
