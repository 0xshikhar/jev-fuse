"""FastAPI REST Gateway exposing canonical Arbiter endpoints and TypeSafe Jev drop-in API."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from arbiter.engine import ArbiterEngine
from arbiter.policy.schema import PolicyDefinition
from arbiter.schema.decision import DecisionRequest, DecisionResponse


class FeedbackPayload(BaseModel):
    trace_id: str = Field(..., description="Unique trace ID from DecisionResponse")
    label: str = Field(..., description="Ground truth outcome: 'allowed', 'denied', or true class")


class RegisterPolicyPayload(BaseModel):
    policy: PolicyDefinition


def create_app(engine: ArbiterEngine | None = None) -> FastAPI:
    """Application factory for Arbiter FastAPI Gateway."""
    
    app_engine = engine or ArbiterEngine()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        await app_engine.start()
        yield
        await app_engine.close()

    app = FastAPI(
        title="Arbiter Decision Gateway",
        description="Governed decision runtime and policy control plane for AI coding agents.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Enable CORS for local playground and browser-use agents
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach engine to app state for dependency access
    app.state.engine = app_engine

    # -------------------------------------------------------------
    # 1. Health & Readiness Probes
    # -------------------------------------------------------------
    @app.get("/healthz", tags=["System"])
    @app.get("/v1/health", tags=["System"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "arbiter-gateway"}

    @app.get("/readyz", tags=["System"])
    async def ready() -> dict[str, Any]:
        p_health = await app_engine.provider.health()
        if p_health.status == "unhealthy":
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=p_health.message)
        return {"ready": True, "provider": p_health.model_dump()}

    # -------------------------------------------------------------
    # 2. Canonical Arbiter Decision Endpoint
    # -------------------------------------------------------------
    @app.post("/v1/decide", response_model=DecisionResponse, tags=["Decisions"])
    async def decide(request: DecisionRequest) -> DecisionResponse:
        """Evaluate a decision request through caching, micro-batching, and policy governance."""
        try:
            return await app_engine.decide(request)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    # -------------------------------------------------------------
    # 3. TypeSafe Jev 100% Drop-In Compatibility Endpoint
    # -------------------------------------------------------------
    @app.post("/v1/systemone", tags=["TypeSafe Compatibility"])
    @app.post("/v1/predict", tags=["TypeSafe Compatibility"])
    async def systemone(payload: dict[str, Any]) -> dict[str, Any]:
        """TypeSafe Jev drop-in endpoint. Existing SDKs can point here by setting base_url."""
        if "state" not in payload or "questions" not in payload:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Payload must include 'state' and 'questions' dictionary",
            )
        try:
            return await app_engine.systemone(payload)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    # -------------------------------------------------------------
    # 4. Active Learning & Human Ground Truth Feedback
    # -------------------------------------------------------------
    @app.post("/v1/feedback", tags=["Feedback"])
    async def feedback(payload: FeedbackPayload) -> dict[str, Any]:
        """Record ground truth label for a decision trace."""
        success = await app_engine.record_feedback(payload.trace_id, payload.label)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trace ID not found or update failed")
        return {"status": "recorded", "trace_id": payload.trace_id}

    # -------------------------------------------------------------
    # 5. Policy & Task Registry
    # -------------------------------------------------------------
    @app.get("/v1/tasks", tags=["Policy"])
    async def list_tasks() -> dict[str, Any]:
        policies = {
            task: policy.model_dump()
            for task, policy in app_engine.policy_engine._policies.items()
        }
        return {"tasks": policies}

    @app.post("/v1/tasks/register", tags=["Policy"])
    async def register_task(payload: RegisterPolicyPayload) -> dict[str, str]:
        app_engine.policy_engine.register_policy(payload.policy)
        return {"status": "registered", "task": payload.policy.task}

    # -------------------------------------------------------------
    # 6. Provider Information
    # -------------------------------------------------------------
    @app.get("/v1/providers", tags=["Providers"])
    async def list_providers() -> dict[str, Any]:
        p_health = await app_engine.provider.health()
        return {
            "active_provider": app_engine.provider.name,
            "health": p_health.model_dump(),
            "batcher_stats": {
                "total_requests": app_engine.batcher.stats.total_requests,
                "total_batches": app_engine.batcher.stats.total_batches,
                "avg_batch_size": app_engine.batcher.stats.avg_batch_size,
                "current_window_ms": app_engine.batcher.stats.current_window_ms,
                "current_queue_depth": app_engine.batcher.stats.current_queue_depth,
            },
        }

    # -------------------------------------------------------------
    # 7. Telemetry & Analytics Metrics
    # -------------------------------------------------------------
    @app.get("/v1/metrics", tags=["Metrics"])
    async def metrics() -> dict[str, Any]:
        total_decisions = app_engine.log_reader.get_total_decisions()
        ece = app_engine.log_reader.calculate_ece(num_bins=10)
        action_dist = app_engine.log_reader.get_action_distribution()
        return {
            "total_logged_decisions": total_decisions,
            "expected_calibration_error": ece,
            "action_distribution": action_dist,
            "batcher": {
                "total_requests": app_engine.batcher.stats.total_requests,
                "total_batches": app_engine.batcher.stats.total_batches,
                "avg_batch_size": app_engine.batcher.stats.avg_batch_size,
            },
        }

    # -------------------------------------------------------------
    # 8. Real-Time Admin Dashboard (Zero-build HTML/CSS)
    # -------------------------------------------------------------
    @app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
    async def dashboard() -> HTMLResponse:
        total = app_engine.log_reader.get_total_decisions()
        actions = app_engine.log_reader.get_action_distribution()
        recent = app_engine.log_reader.get_recent_decisions(limit=10)

        rows = "".join(
            f"""<tr>
                <td style="padding: 8px; border-bottom: 1px solid #222;"><code>{r.trace_id[:8]}...</code></td>
                <td style="padding: 8px; border-bottom: 1px solid #222;">{r.task}</td>
                <td style="padding: 8px; border-bottom: 1px solid #222;"><span class="badge {r.action.value if hasattr(r.action, 'value') else str(r.action)}">{r.action.value if hasattr(r.action, 'value') else str(r.action)}</span></td>
                <td style="padding: 8px; border-bottom: 1px solid #222;">{r.confidence:.2f}</td>
                <td style="padding: 8px; border-bottom: 1px solid #222;">{r.latency_ms:.1f}ms</td>
            </tr>"""
            for r in recent
        )

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Arbiter Policy Dashboard</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 24px; }}
        h1 {{ color: #58a6ff; margin-bottom: 8px; }}
        .subtitle {{ color: #8b949e; margin-bottom: 24px; font-size: 14px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
        .metric {{ font-size: 28px; font-weight: bold; color: #f0f6fc; margin-top: 8px; }}
        table {{ width: 100%; border-collapse: collapse; background: #161b22; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }}
        th {{ background: #21262d; text-align: left; padding: 10px; font-size: 13px; color: #8b949e; }}
        .badge {{ padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; text-transform: uppercase; }}
        .allow {{ background: #238636; color: #fff; }}
        .deny {{ background: #da3633; color: #fff; }}
        .ask {{ background: #d29922; color: #000; }}
        .keep {{ background: #1f6feb; color: #fff; }}
        .drop {{ background: #8b949e; color: #000; }}
    </style>
</head>
<body>
    <h1>Arbiter Control Plane</h1>
    <div class="subtitle">Real-time governed decision metrics and audit stream</div>
    
    <div class="grid">
        <div class="card"><div>Total Decisions</div><div class="metric">{total}</div></div>
        <div class="card"><div>Active Provider</div><div class="metric" style="font-size: 20px;">{app_engine.provider.name}</div></div>
        <div class="card"><div>Avg Batch Size</div><div class="metric">{app_engine.batcher.stats.avg_batch_size:.1f}</div></div>
        <div class="card"><div>Queue Latency Floor</div><div class="metric">{app_engine.batcher.stats.current_window_ms:.1f}ms</div></div>
    </div>

    <div class="card">
        <h3 style="margin-top: 0;">Recent Decision Traces</h3>
        <table>
            <thead>
                <tr>
                    <th>Trace ID</th>
                    <th>Task</th>
                    <th>Action</th>
                    <th>Confidence</th>
                    <th>Latency</th>
                </tr>
            </thead>
            <tbody>
                {rows if rows else '<tr><td colspan="5" style="padding: 16px; text-align: center; color: #8b949e;">No decisions recorded yet. Send requests to /v1/decide or /v1/systemone</td></tr>'}
            </tbody>
        </table>
    </div>
</body>
</html>"""
        return HTMLResponse(content=html)

    return app
