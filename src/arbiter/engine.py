"""Arbiter Runtime Engine: Cohesive control plane coordinating cache, batcher, policy, and logging."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Sequence

from arbiter.batch.batcher import MicroBatcher
from arbiter.cache.hasher import compute_input_hash, compute_systemone_hash
from arbiter.cache.singleflight import AsyncSingleflight
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
from arbiter.schema.systemone import SystemOneRequest, SystemOneResponse, Usage

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

        # 6. Singleflight Concurrency Coalescer
        self.singleflight = AsyncSingleflight()

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

    async def systemone(
        self,
        payload: dict[str, Any] | SystemOneRequest,
        auth_header: str | None = None,
    ) -> SystemOneResponse:
        """
        TypeSafe Jev 100% drop-in endpoint compatibility (POST /v1/systemone).
        Implements Trojan Horse proxy mode with singleflight coalescing,
        governed margin overlay, and full-fidelity SQLite logging.
        """
        if not self._started:
            await self.start()

        if isinstance(payload, SystemOneRequest):
            req = payload
        else:
            req = SystemOneRequest.model_validate(payload)

        model = req.model
        state = req.state
        questions = req.questions

        t0 = time.monotonic()
        sys_hash = compute_systemone_hash(model, state, questions)
        trace_id = str(uuid.uuid4())

        # 1. Check Cache
        cached_entry = await self.cache.get(sys_hash)
        if cached_entry is not None:
            cached_data = cached_entry.raw_score if isinstance(cached_entry.raw_score, dict) else (cached_entry.raw_score.model_dump() if hasattr(cached_entry.raw_score, "model_dump") else None)
            if cached_data is not None:
                res = SystemOneResponse.model_validate(cached_data)
                elapsed_ms = round((time.monotonic() - t0) * 1000.0, 3)
                res.latency_ms = elapsed_ms

                # Preserve stored governed overlay action and compute real confidence
                cached_action = Action.ALLOW
                confidences: list[float] = []
                for ans in res.answers.values():
                    ans_dict = ans if isinstance(ans, dict) else (ans.model_dump() if hasattr(ans, "model_dump") else {})
                    ans_action = ans_dict.get("action")
                    if ans_action == Action.DENY.value:
                        cached_action = Action.DENY
                    elif ans_action == Action.ASK.value and cached_action != Action.DENY:
                        cached_action = Action.ASK
                    if "confidence" in ans_dict and ans_dict["confidence"] is not None:
                        confidences.append(float(ans_dict["confidence"]))
                    elif "noul" in ans_dict and ans_dict["noul"] is not None:
                        confidences.append(float(ans_dict["noul"]))

                avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else 1.0

                dumped_cached = res.model_dump(mode="json")
                cached_answers_dump = dumped_cached.get("answers", {})
                cached_answers_json = json.dumps(cached_answers_dump, ensure_ascii=False)

                self.log_writer.log(
                    DecisionRecord(
                        trace_id=trace_id,
                        task="systemone",
                        client_id="proxy",
                        provider="cache",
                        input_hash=sys_hash,
                        input_preview=str(state)[:500],
                        decision_value=cached_answers_json,
                        raw_score=0.0,
                        confidence=avg_conf,
                        action=cached_action,
                        latency_ms=elapsed_ms,
                        cached=True,
                        model=res.model,
                        questions_json=json.dumps(questions, ensure_ascii=False),
                        answers_json=cached_answers_json,
                        input_tokens=res.usage.input_tokens,
                        output_tokens=res.usage.output_tokens,
                        status="success",
                    )
                )
                return res

        # 2. Upstream execution through singleflight
        async def execute_upstream() -> SystemOneResponse:
            if hasattr(self.provider, "system_one"):
                return await self.provider.system_one(req, auth_header=auth_header)

            # Fallback for generic providers (e.g. MockDeterministicProvider)
            batch: list[NormalizedRequest] = []
            for q_key, q_spec in questions.items():
                q_type = q_spec.get("type", "noul") if isinstance(q_spec, dict) else getattr(q_spec, "type", "noul")
                kind = DecisionKind.CHOICE if q_type == "choice" else (DecisionKind.SCORE if q_type == "score" else DecisionKind.BOOL)
                choices_tuple = None
                if kind == DecisionKind.CHOICE and isinstance(q_spec, dict) and "criteria" in q_spec:
                    crit = q_spec["criteria"]
                    choices_tuple = tuple(crit.keys()) if isinstance(crit, dict) else tuple(str(c) for c in crit)
                batch.append(
                    NormalizedRequest(
                        trace_id=str(uuid.uuid4()),
                        task=q_key,
                        kind=kind,
                        input=str(state),
                        choices=choices_tuple,
                        client_id="systemone-proxy",
                        provider=self.provider.name,
                        input_hash=sys_hash,
                        timestamp_ns=time.time_ns(),
                    )
                )
            raw_scores = await self.provider.infer(batch)
            answers: dict[str, Any] = {}
            for b_req, r_score in zip(batch, raw_scores):
                if b_req.kind == DecisionKind.CHOICE:
                    answers[b_req.task] = {
                        "type": "choice",
                        "choice": str(r_score.value),
                        "confidence": r_score.raw_score,
                        "probabilities": r_score.candidate_scores or {str(r_score.value): r_score.raw_score},
                    }
                elif b_req.kind == DecisionKind.SCORE:
                    answers[b_req.task] = {
                        "type": "score",
                        "score": float(r_score.value),
                        "confidence": r_score.raw_score,
                        "legend": {},
                        "probabilities": r_score.candidate_scores or {},
                    }
                else:
                    answers[b_req.task] = {
                        "type": "noul",
                        "noul": float(r_score.raw_score),
                    }
            return SystemOneResponse(
                model=getattr(self.provider, "model", "jev-latest"),
                answers=answers,
                usage=Usage(input_tokens=len(str(state).split()) + 50, output_tokens=len(answers)),
            )

        provider_err: str | None = None
        try:
            sys_res = await self.singleflight.run(sys_hash, execute_upstream)
        except Exception as exc:
            provider_err = str(exc)
            elapsed_ms = round((time.monotonic() - t0) * 1000.0, 3)
            self.log_writer.log(
                DecisionRecord(
                    trace_id=trace_id,
                    task="systemone",
                    client_id="proxy",
                    provider=self.provider.name,
                    input_hash=sys_hash,
                    input_preview=str(state)[:500],
                    decision_value="error",
                    raw_score=0.0,
                    confidence=0.0,
                    action=Action.ASK,
                    latency_ms=elapsed_ms,
                    cached=False,
                    model=model,
                    questions_json=json.dumps(questions, ensure_ascii=False),
                    answers_json="{}",
                    status="error",
                    reason=provider_err,
                )
            )
            raise

        # 3. Governed margin overlay (abstention on close probabilities)
        governed_overlay: dict[str, Any] = {}
        overall_action = Action.ALLOW
        for q_key, ans_dict in sys_res.answers.items():
            ans_type = ans_dict.get("type") if isinstance(ans_dict, dict) else getattr(ans_dict, "type", None)
            if ans_type == "choice":
                probs = ans_dict.get("probabilities", {}) if isinstance(ans_dict, dict) else getattr(ans_dict, "probabilities", {})
                if isinstance(probs, dict) and len(probs) >= 2:
                    sorted_p = sorted(probs.values(), reverse=True)
                    margin = round(sorted_p[0] - sorted_p[1], 4)
                    if margin < 0.15:  # Choice margin threshold
                        if isinstance(ans_dict, dict):
                            ans_dict["action"] = Action.ASK.value
                            ans_dict["margin"] = margin
                            ans_dict["reason"] = f"Choice margin {margin} below deadband 0.15"
                        else:
                            ans_dict.action = Action.ASK
                            ans_dict.margin = margin
                            ans_dict.reason = f"Choice margin {margin} below deadband 0.15"
                        governed_overlay[q_key] = {"action": Action.ASK.value, "margin": margin}
                        overall_action = Action.ASK
            elif ans_type == "noul":
                noul = float(ans_dict.get("noul", 0.5) if isinstance(ans_dict, dict) else getattr(ans_dict, "noul", 0.5))
                if abs(noul - 0.5) < 0.10:  # Deadband [0.40, 0.60]
                    if isinstance(ans_dict, dict):
                        ans_dict["action"] = Action.ASK.value
                        ans_dict["reason"] = f"Noul probability {noul} within uncertainty band [0.40, 0.60]"
                    else:
                        ans_dict.action = Action.ASK
                        ans_dict.reason = f"Noul probability {noul} within uncertainty band [0.40, 0.60]"
                    governed_overlay[q_key] = {"action": Action.ASK.value, "noul": noul}
                    overall_action = Action.ASK

        if governed_overlay:
            sys_res.governed = governed_overlay

        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 3)
        sys_res.latency_ms = elapsed_ms

        # 4. Cache successful response
        dumped_res = sys_res.model_dump(mode="json")
        await self.cache.set(sys_hash, dumped_res, self.provider.name)

        answers_dump = dumped_res.get("answers", {})
        answers_json_str = json.dumps(answers_dump, ensure_ascii=False)

        # 5. Full-fidelity SQLite decision logging
        self.log_writer.log(
            DecisionRecord(
                trace_id=trace_id,
                task="systemone",
                client_id="proxy",
                provider=self.provider.name,
                input_hash=sys_hash,
                input_preview=str(state)[:500],
                decision_value=answers_json_str,
                raw_score=0.0,
                confidence=1.0,
                action=overall_action,
                latency_ms=elapsed_ms,
                cached=False,
                model=sys_res.model,
                questions_json=json.dumps(questions, ensure_ascii=False),
                answers_json=answers_json_str,
                input_tokens=sys_res.usage.input_tokens,
                output_tokens=sys_res.usage.output_tokens,
                status="success",
            )
        )

        return sys_res

    async def record_feedback(self, trace_id: str, label: str) -> bool:
        """Record ground truth human label for active learning and calibration."""
        return await self.log_writer.attach_label(trace_id, label)
