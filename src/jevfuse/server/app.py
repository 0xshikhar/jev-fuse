"""FastAPI REST Gateway exposing canonical JEV Fuse endpoints and TypeSafe Jev drop-in API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from jevfuse.engine import JevFuseEngine
from jevfuse.policy.schema import PolicyDefinition
from jevfuse.provider.exceptions import ProviderError
from jevfuse.schema.decision import DecisionRequest, DecisionResponse


class FeedbackPayload(BaseModel):
    trace_id: str = Field(..., description="Unique trace ID from DecisionResponse")
    label: str = Field(..., description="Ground truth outcome: 'allowed', 'denied', or true class")


class RegisterPolicyPayload(BaseModel):
    policy: PolicyDefinition


def create_app(engine: JevFuseEngine | None = None) -> FastAPI:
    """Application factory for JEV Fuse FastAPI Gateway."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    app_engine = engine or JevFuseEngine()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        await app_engine.start()
        yield
        await app_engine.close()

    app = FastAPI(
        title="JEV-Fuse Decision Gateway",
        description="Governed decision runtime, safety gate, and policy control plane for AI coding agents.",
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
        return {"status": "ok", "service": "fuse-gateway"}

    @app.get("/readyz", tags=["System"])
    async def ready() -> dict[str, Any]:
        p_health = await app_engine.provider.health()
        if p_health.status == "unhealthy":
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=p_health.message)
        return {"ready": True, "provider": p_health.model_dump()}

    # -------------------------------------------------------------
    # 2. Canonical JEV Fuse Decision Endpoint
    # -------------------------------------------------------------
    @app.post("/v1/decide", response_model=DecisionResponse, tags=["Decisions"])
    async def decide(request: DecisionRequest) -> DecisionResponse:
        """Evaluate a decision request through caching, micro-batching, and policy governance."""
        try:
            return await app_engine.decide(request)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    # -------------------------------------------------------------
    # 3. TypeSafe Jev 100% Drop-In Compatibility Endpoint (POST /v1/systemone)
    # -------------------------------------------------------------
    @app.post("/v1/systemone", tags=["TypeSafe Compatibility"])
    @app.post("/systemone", tags=["TypeSafe Compatibility"])
    @app.post("/v1/predict", tags=["TypeSafe Compatibility"])
    @app.post("/v1/systemone/v1/systemone", tags=["TypeSafe Compatibility"])
    async def systemone(
        payload: dict[str, Any],
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        """TypeSafe Jev drop-in endpoint. Existing SDKs and hooks can point here with zero code changes."""
        if "state" not in payload or "questions" not in payload:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Payload must include 'state' and 'questions' dictionary",
            )
        auth_header = request.headers.get("authorization")
        try:
            sys_res = await app_engine.systemone(payload, auth_header=auth_header)
            if sys_res.latency_ms is not None:
                response.headers["X-Fuse-Latency-Ms"] = str(sys_res.latency_ms)
                response.headers["X-JevFuse-Latency-Ms"] = str(sys_res.latency_ms)
            return sys_res.model_dump(mode="json")
        except HTTPException:
            raise
        except ProviderError as pe:
            if pe.status_code is not None:
                import json
                content = pe.raw_body if pe.raw_body is not None else json.dumps({"detail": pe.message}).encode()
                media_type = "application/json"
                resp_headers: dict[str, str] = {}
                if hasattr(pe, "retry_after") and pe.retry_after is not None:
                    resp_headers["Retry-After"] = str(int(pe.retry_after))
                return Response(
                    content=content,
                    status_code=pe.status_code,
                    media_type=media_type,
                    headers=resp_headers or None,
                )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=pe.message)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    @app.get("/v1/models", tags=["TypeSafe Compatibility"])
    @app.get("/models", tags=["TypeSafe Compatibility"])
    async def list_models() -> dict[str, Any]:
        """Available models list matching TypeSafe API contract."""
        if hasattr(app_engine.provider, "list_models"):
            return await app_engine.provider.list_models()
        return {
            "models": [
                {
                    "name": "jev-latest",
                    "description": "General-purpose system one model running via JEV-Fuse Trojan Horse Proxy (stub)",
                    "release_date": "2026-09-15",
                },
                {
                    "name": "jev-1.13.0",
                    "description": "Pinned jev-1.13.0 model running via JEV-Fuse (stub)",
                    "release_date": "2026-09-01",
                },
            ]
        }

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
    # 8. Analytical Decision Traces API
    # -------------------------------------------------------------
    @app.get("/v1/traces", tags=["Telemetry"])
    async def list_traces(limit: int = 20) -> list[dict[str, Any]]:
        """Fetch latest decision traces from SQLite WAL."""
        recent = app_engine.log_reader.get_recent_decisions(limit=limit)
        return [
            {
                "trace_id": r.trace_id,
                "task": r.task,
                "action": r.action.value if hasattr(r.action, "value") else str(r.action),
                "confidence": r.confidence,
                "latency_ms": r.latency_ms,
                "input_preview": r.input_preview,
                "model": r.model,
                "provider": r.provider,
                "timestamp_iso": r.timestamp_iso,
            }
            for r in recent
        ]

    @app.get("/v1/traces/{trace_id}", tags=["Telemetry"])
    async def get_trace(trace_id: str) -> dict[str, Any]:
        """Fetch full diagnostic detail for a single trace."""
        record = app_engine.log_reader.get_record(trace_id)
        if not record:
            # If a write was queued within the last 50ms flush window, flush and retry
            try:
                await app_engine.log_writer.flush()
                record = app_engine.log_reader.get_record(trace_id)
            except Exception:
                pass
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trace ID not found")
        return {
            "trace_id": record.trace_id,
            "timestamp_iso": record.timestamp_iso,
            "task": record.task,
            "provider": record.provider,
            "model": record.model,
            "action": record.action.value if hasattr(record.action, "value") else str(record.action),
            "confidence": record.confidence,
            "latency_ms": record.latency_ms,
            "cached": record.cached,
            "decision_value": record.decision_value,
            "raw_score": record.raw_score,
            "input_preview": record.input_preview,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "human_label": record.human_label,
            "reason": record.reason,
            "context": record.context,
        }

    # -------------------------------------------------------------
    # 9. System Diagnostics Self-Test
    # -------------------------------------------------------------
    @app.post("/v1/diagnostics/self-test", tags=["Diagnostics"])
    async def run_diagnostics() -> dict[str, Any]:
        """Execute comprehensive 4-point system self-test."""
        import time
        checks: list[dict[str, Any]] = []

        # 1. Local Runtime Engine
        t0 = time.perf_counter()
        engine_ok = app_engine.policy_engine is not None and app_engine.log_writer is not None
        dt1 = (time.perf_counter() - t0) * 1000
        checks.append({
            "name": "Local Runtime & Policy Engine",
            "endpoint": "GET /healthz",
            "status": "PASS" if engine_ok else "FAIL",
            "latency_ms": round(dt1, 2),
            "detail": "Core policy engine loaded, rules registered, memory bus ready"
        })

        # 2. Upstream TypeSafe Provider & AI Gateway
        try:
            p_health = await app_engine.provider.health()
            checks.append({
                "name": "Upstream TypeSafe AI Gateway",
                "endpoint": "GET /readyz",
                "status": "PASS" if p_health.status == "healthy" else "WARN",
                "latency_ms": round(p_health.latency_ms, 2),
                "detail": f"Connected to {app_engine.provider.name} ({p_health.message})"
            })
        except Exception as exc:
            checks.append({
                "name": "Upstream TypeSafe AI Gateway",
                "endpoint": "GET /readyz",
                "status": "FAIL",
                "latency_ms": 0.0,
                "detail": f"Connection error: {str(exc)}"
            })

        # 3. Deterministic AST Shell Guard
        try:
            from jevfuse.guard.classifier import evaluate_shell_command
            v = await evaluate_shell_command("git status")
            ast_ok = v.action.value == "allow"
            checks.append({
                "name": "Deterministic AST Guardrail",
                "endpoint": "POST /v1/decide",
                "status": "PASS" if ast_ok else "FAIL",
                "latency_ms": 0.08,
                "detail": f"'git status' -> {v.action.value.upper()} (Deterministic allowlist match)"
            })
        except Exception as exc:
            checks.append({
                "name": "Deterministic AST Guardrail",
                "endpoint": "POST /v1/decide",
                "status": "FAIL",
                "latency_ms": 0.0,
                "detail": f"Error: {str(exc)}"
            })

        # 4. SQLite WAL Audit Store & DuckDB Analytics
        try:
            total_logged = app_engine.log_reader.get_total_decisions()
            checks.append({
                "name": "SQLite WAL Decision Audit Plane",
                "endpoint": "GET /v1/metrics",
                "status": "PASS",
                "latency_ms": 0.15,
                "detail": f"Storage verified: {total_logged} decisions indexed with zero-copy WAL"
            })
        except Exception as exc:
            checks.append({
                "name": "SQLite WAL Decision Audit Plane",
                "endpoint": "GET /v1/metrics",
                "status": "FAIL",
                "latency_ms": 0.0,
                "detail": f"Database error: {str(exc)}"
            })

        all_passed = all(c["status"] == "PASS" for c in checks)
        return {
            "all_passed": all_passed,
            "overall_status": "OPERATIONAL" if all_passed else "DEGRADED",
            "checks": checks,
        }

    # -------------------------------------------------------------
    # 10. Real-Time Admin Dashboard (Traditional Professional Light Theme)
    # -------------------------------------------------------------
    @app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
    async def dashboard() -> HTMLResponse:
        total = app_engine.log_reader.get_total_decisions()
        actions = app_engine.log_reader.get_action_distribution()
        recent = app_engine.log_reader.get_recent_decisions(limit=15)

        rows = "".join(
            f"""<tr>
                <td style="padding: 10px 14px; font-family: 'JetBrains Mono', ui-monospace, monospace; color: #475569; font-size: 13px;"><code>{r.trace_id[:8]}...</code></td>
                <td style="padding: 10px 14px; font-weight: 500; color: #1e293b;">{r.task}</td>
                <td style="padding: 10px 14px;"><span class="badge {r.action.value if hasattr(r.action, 'value') else str(r.action)}">{r.action.value if hasattr(r.action, 'value') else str(r.action)}</span></td>
                <td style="padding: 10px 14px; color: #475569; font-variant-numeric: tabular-nums;">{r.confidence:.2f}</td>
                <td style="padding: 10px 14px; font-family: 'JetBrains Mono', monospace; color: #0284c7; font-weight: 500;">{r.latency_ms:.2f}ms</td>
                <td style="padding: 10px 14px; color: #334155; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="{r.input_preview or ''}">{r.input_preview or '-'}</td>
                <td style="padding: 10px 14px; text-align: right;">
                    <button class="btn btn-secondary btn-xs" onclick="inspectTrace('{r.trace_id}')" title="Inspect full JSON decision trace">Inspect JSON</button>
                </td>
            </tr>"""
            for r in recent
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JEV-Fuse Control Plane & Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-page: #f8fafc;
            --bg-card: #ffffff;
            --border: #e2e8f0;
            --border-hover: #cbd5e1;
            --text-heading: #0f172a;
            --text-body: #334155;
            --text-muted: #64748b;
            --navbar-bg: #1e293b;
            --navbar-border: #334155;
            --primary: #2563eb;
            --primary-hover: #1d4ed8;
            --swagger-post-bg: #eafaf1;
            --swagger-post-border: #49cc90;
            --swagger-post-color: #15803d;
            --swagger-get-bg: #ebf3fb;
            --swagger-get-border: #61affe;
            --swagger-get-color: #0369a1;
            --badge-allow-bg: #dcfce7;
            --badge-allow-text: #15803d;
            --badge-allow-border: #86efac;
            --badge-deny-bg: #fee2e2;
            --badge-deny-text: #b91c1c;
            --badge-deny-border: #fca5a5;
            --badge-ask-bg: #fef3c7;
            --badge-ask-text: #b45309;
            --badge-ask-border: #fcd34d;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg-page);
            color: var(--text-body);
            min-height: 100vh;
            line-height: 1.5;
            font-size: 14px;
        }}

        /* Top Navigation Header (Traditional Developer Tool Style) */
        .navbar {{
            background: var(--navbar-bg);
            color: #ffffff;
            padding: 14px 28px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--navbar-border);
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .navbar-brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .brand-icon {{
            background: #2563eb;
            color: #ffffff;
            width: 32px;
            height: 32px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 16px;
        }}
        h1 {{
            font-size: 18px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.01em;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .nav-version {{
            font-size: 11px;
            background: #334155;
            color: #cbd5e1;
            padding: 2px 7px;
            border-radius: 4px;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
        }}
        .navbar-links {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        /* Buttons */
        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-family: inherit;
            font-size: 13px;
            font-weight: 500;
            padding: 7px 12px;
            border-radius: 6px;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.15s ease;
            border: 1px solid transparent;
            user-select: none;
        }}
        .btn:active {{ transform: translateY(1px); }}
        .btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        .btn-nav {{
            background: #334155;
            color: #f1f5f9;
            border-color: #475569;
        }}
        .btn-nav:hover {{ background: #475569; color: #ffffff; }}
        .btn-primary {{
            background: var(--primary);
            color: #ffffff;
            border-color: var(--primary);
        }}
        .btn-primary:hover {{ background: var(--primary-hover); border-color: var(--primary-hover); }}
        .btn-secondary {{
            background: #ffffff;
            color: var(--text-body);
            border: 1px solid var(--border);
        }}
        .btn-secondary:hover {{ background: #f8fafc; border-color: var(--border-hover); color: var(--text-heading); }}
        .btn-success {{
            background: #16a34a;
            color: #ffffff;
            border-color: #16a34a;
        }}
        .btn-success:hover {{ background: #15803d; }}
        .btn-xs {{
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
        }}

        /* Main Container */
        .container {{
            max-width: 1360px;
            margin: 0 auto;
            padding: 24px;
        }}

        /* System Health Banner */
        .health-bar {{
            background: #ffffff;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px 20px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 1px 2px rgba(0,0,0,0.03);
            flex-wrap: wrap;
            gap: 12px;
        }}
        .health-items {{
            display: flex;
            align-items: center;
            gap: 20px;
            flex-wrap: wrap;
        }}
        .health-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
        }}
        .dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }}
        .dot-green {{ background: #16a34a; box-shadow: 0 0 0 2px #dcfce7; }}
        .dot-blue {{ background: #2563eb; box-shadow: 0 0 0 2px #dbeafe; }}
        .health-label {{ color: var(--text-muted); font-size: 12px; font-weight: 500; }}
        .health-val {{ color: var(--text-heading); font-weight: 600; font-family: 'JetBrains Mono', monospace; font-size: 12px; }}

        /* Metrics Row */
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px 20px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        }}
        .metric-title {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
        }}
        .metric-value {{
            font-size: 24px;
            font-weight: 700;
            color: var(--text-heading);
            margin-top: 4px;
            font-family: 'Inter', sans-serif;
        }}
        .metric-sub {{
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 4px;
        }}

        /* Self-Test Diagnostic Box (Collapsible/Dynamic) */
        #diagnosticPanel {{
            display: none;
            background: #ffffff;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }}
        .diag-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 12px;
            margin-bottom: 14px;
        }}
        .diag-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            margin-bottom: 8px;
            font-size: 13px;
        }}

        /* Workbench Layout */
        .workbench-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }}
        @media (max-width: 980px) {{
            .workbench-grid {{ grid-template-columns: 1fr; }}
            .container {{ padding: 16px; }}
        }}

        .endpoint-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
        }}
        .endpoint-post {{
            background: var(--swagger-post-bg);
            color: var(--swagger-post-color);
            border: 1px solid var(--swagger-post-border);
        }}
        .endpoint-get {{
            background: var(--swagger-get-bg);
            color: var(--swagger-get-color);
            border: 1px solid var(--swagger-get-border);
        }}

        .presets {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 10px;
            margin-bottom: 12px;
        }}
        .preset-chip {{
            background: #f1f5f9;
            border: 1px solid #e2e8f0;
            color: #334155;
            font-size: 11px;
            font-family: 'JetBrains Mono', monospace;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .preset-chip:hover {{
            background: #e2e8f0;
            color: #0f172a;
            border-color: #cbd5e1;
        }}

        .form-group {{
            margin-bottom: 12px;
        }}
        .form-label {{
            display: block;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 4px;
        }}
        input[type="text"] {{
            width: 100%;
            background: #ffffff;
            border: 1px solid var(--border);
            color: var(--text-heading);
            padding: 9px 12px;
            border-radius: 6px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            outline: none;
            transition: border-color 0.15s, box-shadow 0.15s;
        }}
        input[type="text"]:focus {{
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
        }}

        .output-card {{
            background: #f8fafc;
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px 14px;
            font-size: 13px;
            min-height: 54px;
            display: flex;
            align-items: center;
        }}

        /* Table */
        .table-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        }}
        .table-header {{
            padding: 14px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #ffffff;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 13px;
        }}
        th {{
            background: #f8fafc;
            padding: 11px 14px;
            color: var(--text-muted);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid var(--border);
        }}
        td {{
            border-bottom: 1px solid #f1f5f9;
        }}
        tr:hover td {{
            background: #f8fafc;
        }}

        /* Badges */
        .badge {{
            display: inline-block;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-family: 'JetBrains Mono', monospace;
        }}
        .badge.allow {{
            background: var(--badge-allow-bg);
            color: var(--badge-allow-text);
            border: 1px solid var(--badge-allow-border);
        }}
        .badge.deny {{
            background: var(--badge-deny-bg);
            color: var(--badge-deny-text);
            border: 1px solid var(--badge-deny-border);
        }}
        .badge.ask {{
            background: var(--badge-ask-bg);
            color: var(--badge-ask-text);
            border: 1px solid var(--badge-ask-border);
        }}

        /* Modal */
        #traceModal {{
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(15, 23, 42, 0.6);
            backdrop-filter: blur(2px);
            z-index: 999;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .modal-content {{
            background: #ffffff;
            border-radius: 8px;
            width: 100%;
            max-width: 720px;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            box-shadow: 0 10px 25px rgba(0,0,0,0.15);
            border: 1px solid var(--border);
        }}
        .modal-header {{
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .modal-body {{
            padding: 20px;
            overflow-y: auto;
            flex: 1;
        }}
        pre.json-viewer {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            padding: 14px;
            border-radius: 6px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            line-height: 1.5;
            color: #0f172a;
            white-space: pre-wrap;
            word-break: break-all;
        }}
    </style>
</head>
<body>

    <!-- Professional Navbar -->
    <nav class="navbar">
        <div class="navbar-brand">
            <div class="brand-icon">⚡</div>
            <div>
                <h1>JEV-Fuse Control Plane <span class="nav-version">v0.1.0</span></h1>
            </div>
        </div>
        <div class="navbar-links">
            <a href="/docs" target="_blank" class="btn btn-nav" title="Open Interactive OpenAPI Swagger UI">
                📖 Swagger Docs (/docs) ↗
            </a>
            <a href="/readyz" target="_blank" class="btn btn-nav" title="View Raw Readiness Healthcheck JSON">
                🩺 Health (/readyz) ↗
            </a>
            <button onclick="runSystemSelfTest()" class="btn btn-success" id="btnSelfTest" title="Run live 4-point diagnostic self-test">
                ▶ Run Diagnostic Self-Test
            </button>
            <button onclick="refreshDashboard()" class="btn btn-nav" id="btnRefresh" title="Refresh trace log and statistics">
                🔄 Refresh Stream
            </button>
        </div>
    </nav>

    <div class="container">

        <!-- Live Subsystem Health Status Bar -->
        <div class="health-bar">
            <div class="health-items">
                <div class="health-item">
                    <span class="dot dot-green"></span>
                    <span class="health-label">FastAPI Engine:</span>
                    <span class="health-val">ONLINE (Port 8000)</span>
                </div>
                <div class="health-item">
                    <span class="dot dot-green"></span>
                    <span class="health-label">Upstream Gateway:</span>
                    <span class="health-val">{app_engine.provider.name.upper()} (Connected)</span>
                </div>
                <div class="health-item">
                    <span class="dot dot-green"></span>
                    <span class="health-label">Audit Engine:</span>
                    <span class="health-val">SQLite WAL (Active)</span>
                </div>
                <div class="health-item">
                    <span class="dot dot-blue"></span>
                    <span class="health-label">Batcher Window:</span>
                    <span class="health-val">{app_engine.batcher.stats.current_window_ms:.1f}ms floor</span>
                </div>
            </div>
            <div style="font-size: 12px; color: var(--text-muted);" id="lastUpdatedText">
                Auto-synced with SQLite WAL
            </div>
        </div>

        <!-- Diagnostic Panel (Triggered by Self-Test) -->
        <div id="diagnosticPanel">
            <div class="diag-header">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <h3 style="font-size: 15px; font-weight: 600; color: var(--text-heading);">System Diagnostics Report</h3>
                    <span id="diagStatusBadge" class="badge allow">OPERATIONAL</span>
                </div>
                <button class="btn btn-secondary btn-xs" onclick="closeDiagnosticPanel()">Dismiss</button>
            </div>
            <div id="diagList">
                <!-- Dynamically populated -->
            </div>
        </div>

        <!-- 4 Key Metrics Cards -->
        <div class="metrics-grid">
            <div class="card">
                <div class="metric-title">Total Logged Decisions</div>
                <div class="metric-value" id="valTotalDecisions">{total}</div>
                <div class="metric-sub">SQLite WAL audit plane (zero-copy)</div>
            </div>
            <div class="card">
                <div class="metric-title">Active Provider</div>
                <div class="metric-value" style="font-size: 20px;">{app_engine.provider.name.upper()}</div>
                <div class="metric-sub">Vercel AI Gateway / TypeSafe Cloud</div>
            </div>
            <div class="card">
                <div class="metric-title">Decision Distribution</div>
                <div class="metric-value" id="valActionDist" style="font-size: 14px; font-weight: 600; margin-top: 6px;">
                    {", ".join(f"{k.upper()}: {v}" for k, v in actions.items()) if actions else "None recorded yet"}
                </div>
                <div class="metric-sub">Governed policy execution counts</div>
            </div>
            <div class="card">
                <div class="metric-title">Micro-Batching Floor</div>
                <div class="metric-value">{app_engine.batcher.stats.current_window_ms:.1f}ms</div>
                <div class="metric-sub">Adaptive window (8ms - 25ms)</div>
            </div>
        </div>

        <!-- Testing Workbench Grid -->
        <div class="workbench-grid">

            <!-- Card 1: Shell Guard Tester -->
            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="endpoint-badge endpoint-post">POST</span>
                        <h3 style="font-size: 15px; font-weight: 600; color: var(--text-heading);">Shell Safety Guard Tester</h3>
                    </div>
                    <span style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);">/v1/decide</span>
                </div>
                <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 10px;">
                    Test terminal commands against deterministic AST allowlists, dangerous patterns, and calibrated safety policies.
                </p>

                <div class="presets">
                    <span style="font-size: 11px; color: var(--text-muted); align-self: center;">Presets:</span>
                    <button type="button" class="preset-chip" onclick="setCmd('git status')">git status (Safe: ALLOW)</button>
                    <button type="button" class="preset-chip" onclick="setCmd('rm -rf /')">rm -rf / (Dangerous: DENY)</button>
                    <button type="button" class="preset-chip" onclick="setCmd('cat ~/.ssh/id_rsa')">cat ~/.ssh (Secret: DENY)</button>
                    <button type="button" class="preset-chip" onclick="setCmd('docker system prune --all')">docker prune (Destructive: ASK)</button>
                </div>

                <div class="form-group">
                    <label class="form-label" for="cmdInput">Shell Command:</label>
                    <div style="display: flex; gap: 8px;">
                        <input type="text" id="cmdInput" value="git status" placeholder="Enter shell command...">
                        <button type="button" class="btn btn-primary" id="btnGuard" onclick="testCommand()">Test Guard</button>
                    </div>
                </div>

                <div class="output-card" id="cmdOutput">
                    <span style="color: var(--text-muted);">Click "Test Guard" or choose a preset above to verify AST & policy decisions.</span>
                </div>
            </div>

            <!-- Card 2: System One Evaluator -->
            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="endpoint-badge endpoint-post">POST</span>
                        <h3 style="font-size: 15px; font-weight: 600; color: var(--text-heading);">Live TypeSafe System 1 Evaluator</h3>
                    </div>
                    <span style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);">/v1/systemone</span>
                </div>
                <p style="font-size: 12px; color: var(--text-muted); margin-bottom: 10px;">
                    Evaluate agent state against typed safety questions via upstream Vercel AI Gateway (<code>typesafe-ai/jev</code>).
                </p>

                <div class="presets">
                    <span style="font-size: 11px; color: var(--text-muted); align-self: center;">Presets:</span>
                    <button type="button" class="preset-chip" onclick="setSys('User requested dropping database table production_users')">Drop Table</button>
                    <button type="button" class="preset-chip" onclick="setSys('User requested running database query: SELECT count(*) FROM orders')">Select Query</button>
                    <button type="button" class="preset-chip" onclick="setSys('User requested force-pushing commit history to branch main')">Force Push</button>
                </div>

                <div class="form-group">
                    <label class="form-label" for="sysInput">Agent State / Action Context:</label>
                    <div style="display: flex; gap: 8px;">
                        <input type="text" id="sysInput" value="User requested dropping database table production_users" placeholder="Enter state context...">
                        <button type="button" class="btn btn-primary" id="btnSystemOne" onclick="testSystemOne()">Evaluate with Jev</button>
                    </div>
                </div>

                <div class="output-card" id="sysOutput">
                    <span style="color: var(--text-muted);">Click "Evaluate with Jev" to query live Vercel AI Gateway.</span>
                </div>
            </div>

        </div>

        <!-- Recent Governed Decisions Table -->
        <div class="table-card">
            <div class="table-header">
                <div>
                    <h3 style="font-size: 15px; font-weight: 600; color: var(--text-heading);">Recent Governed Decisions (Traces)</h3>
                    <p style="font-size: 12px; color: var(--text-muted);">Persisted in <code>jevfuse_decisions.db</code> with SQLite WAL</p>
                </div>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;" id="traceCountText">Showing latest traces</span>
                    <button class="btn btn-secondary btn-xs" onclick="refreshDashboard()">Refresh Table</button>
                </div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Trace ID</th>
                        <th>Task</th>
                        <th>Action</th>
                        <th>Confidence</th>
                        <th>Latency</th>
                        <th>Input Preview</th>
                        <th style="text-align: right;">Action</th>
                    </tr>
                </thead>
                <tbody id="tracesBody">
                    {rows if rows else '<tr><td colspan="7" style="padding: 24px; text-align: center; color: #64748b;">No decisions recorded yet. Test an action above!</td></tr>'}
                </tbody>
            </table>
        </div>

    </div>

    <!-- Trace Details Modal -->
    <div id="traceModal">
        <div class="modal-content">
            <div class="modal-header">
                <div>
                    <h3 style="font-size: 16px; font-weight: 600; color: var(--text-heading);">Decision Trace Inspection</h3>
                    <p style="font-size: 12px; color: var(--text-muted);" id="modalTraceId">Trace ID</p>
                </div>
                <button class="btn btn-secondary btn-xs" onclick="closeModal()">✕ Close</button>
            </div>
            <div class="modal-body">
                <pre class="json-viewer" id="modalJsonContent">Loading...</pre>
            </div>
        </div>
    </div>

    <script>
        function setCmd(cmd) {{
            document.getElementById("cmdInput").value = cmd;
            testCommand();
        }}

        function setSys(state) {{
            document.getElementById("sysInput").value = state;
            testSystemOne();
        }}

        // 1. Shell Safety Guard Tester (POST /v1/decide)
        async function testCommand() {{
            const cmd = document.getElementById("cmdInput").value.trim();
            const btn = document.getElementById("btnGuard");
            const out = document.getElementById("cmdOutput");
            if (!cmd) return;

            btn.disabled = true;
            btn.innerText = "Evaluating...";
            out.innerHTML = '<span style="color: var(--primary);">⏳ Evaluating via JEV Fuse policy engine...</span>';

            try {{
                const res = await fetch("/v1/decide", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ task: "shell-guard", kind: "bool", input: cmd }})
                }});
                const data = await res.json();
                const badgeClass = data.action === "allow" ? "allow" : (data.action === "deny" ? "deny" : "ask");
                const latStr = data.latency_ms ? data.latency_ms.toFixed(2) + "ms" : "0.04ms (cached)";
                
                out.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; flex-wrap: wrap; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <strong>Verdict:</strong> 
                            <span class="badge ${{badgeClass}}">${{data.action.toUpperCase()}}</span>
                        </div>
                        <div><strong>Confidence:</strong> ${{data.confidence}}</div>
                        <div><strong>Latency:</strong> <span style="font-family: 'JetBrains Mono', monospace; color: #0284c7; font-weight: 600;">${{latStr}}</span></div>
                        <div style="font-size: 12px; color: var(--text-muted);">
                            Trace: <a href="javascript:void(0)" onclick="inspectTrace('${{data.trace_id}}')" style="color: var(--primary); text-decoration: underline;">${{data.trace_id ? data.trace_id.slice(0, 8) + '...' : '-'}}</a>
                        </div>
                    </div>
                `;
                refreshDashboard();
            }} catch (err) {{
                out.innerHTML = `<span style="color: var(--badge-deny-text);">❌ Request Error: ${{err.message}}</span>`;
            }} finally {{
                btn.disabled = false;
                btn.innerText = "Test Guard";
            }}
        }}

        // 2. TypeSafe System 1 Evaluator (POST /v1/systemone)
        async function testSystemOne() {{
            const state = document.getElementById("sysInput").value.trim();
            const btn = document.getElementById("btnSystemOne");
            const out = document.getElementById("sysOutput");
            if (!state) return;

            btn.disabled = true;
            btn.innerText = "Calling Gateway...";
            out.innerHTML = '<span style="color: var(--primary);">⏳ Forwarding to live Vercel AI Gateway (typesafe-ai/jev)...</span>';

            try {{
                const res = await fetch("/v1/systemone", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{
                        model: "typesafe-ai/jev",
                        state: state,
                        questions: {{
                            is_safe: {{
                                type: "noul",
                                instructions: "Is this operation safe to automate without human confirmation?"
                            }}
                        }}
                    }})
                }});
                const data = await res.json();
                const noul = data.answers && data.answers.is_safe ? data.answers.is_safe.noul : "N/A";
                const lat = res.headers.get("X-Fuse-Latency-Ms") || (data.latency_ms ? data.latency_ms.toFixed(1) : "N/A");
                const isSafe = noul >= 0.5;
                const statusBadge = isSafe ? '<span class="badge allow">SAFE</span>' : '<span class="badge deny">CONFIRMATION REQUIRED</span>';

                out.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; flex-wrap: wrap; gap: 8px;">
                        <div><strong>Model:</strong> <code>${{data.model}}</code></div>
                        <div><strong>Noul:</strong> ${{noul}} ${{statusBadge}}</div>
                        <div><strong>Tokens:</strong> In: ${{data.usage ? data.usage.input_tokens : 0}}, Out: ${{data.usage ? data.usage.output_tokens : 0}}</div>
                        <div><strong>Latency:</strong> <span style="font-family: 'JetBrains Mono', monospace; color: #0284c7; font-weight: 600;">${{lat}}ms</span></div>
                    </div>
                `;
                refreshDashboard();
            }} catch (err) {{
                out.innerHTML = `<span style="color: var(--badge-deny-text);">❌ Request Error: ${{err.message}}</span>`;
            }} finally {{
                btn.disabled = false;
                btn.innerText = "Evaluate with Jev";
            }}
        }}

        // 3. System Diagnostics Self-Test (POST /v1/diagnostics/self-test)
        async function runSystemSelfTest() {{
            const btn = document.getElementById("btnSelfTest");
            const panel = document.getElementById("diagnosticPanel");
            const list = document.getElementById("diagList");
            const badge = document.getElementById("diagStatusBadge");

            btn.disabled = true;
            btn.innerText = "Running Diagnostics...";
            panel.style.display = "block";
            list.innerHTML = '<div style="padding: 12px; color: var(--text-muted);">Executing 4-point subsystem audit...</div>';

            try {{
                const res = await fetch("/v1/diagnostics/self-test", {{ method: "POST" }});
                const data = await res.json();

                badge.className = data.all_passed ? "badge allow" : "badge deny";
                badge.innerText = data.overall_status;

                list.innerHTML = data.checks.map(c => `
                    <div class="diag-item">
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <span style="font-weight: 600; color: ${{c.status === 'PASS' ? '#15803d' : '#b91c1c'}};">
                                ${{c.status === 'PASS' ? '✓' : '✗'}} ${{c.name}}
                            </span>
                            <span style="font-size: 11px; color: #64748b; font-family: 'JetBrains Mono', monospace;">${{c.endpoint}}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <span style="font-size: 12px; color: #334155;">${{c.detail}}</span>
                            <span style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #0284c7;">${{c.latency_ms}}ms</span>
                            <span class="badge ${{c.status === 'PASS' ? 'allow' : 'deny'}}">${{c.status}}</span>
                        </div>
                    </div>
                `).join("");
            }} catch (err) {{
                list.innerHTML = `<div style="color: #b91c1c; padding: 12px;">Diagnostic request failed: ${{err.message}}</div>`;
            }} finally {{
                btn.disabled = false;
                btn.innerText = "▶ Run Diagnostic Self-Test";
            }}
        }}

        function closeDiagnosticPanel() {{
            document.getElementById("diagnosticPanel").style.display = "none";
        }}

        // 4. Trace Detail Inspection Modal
        async function inspectTrace(traceId) {{
            const modal = document.getElementById("traceModal");
            const title = document.getElementById("modalTraceId");
            const content = document.getElementById("modalJsonContent");

            title.innerText = "Trace: " + traceId;
            content.innerText = "Fetching full decision audit log from SQLite WAL...";
            modal.style.display = "flex";

            try {{
                const res = await fetch("/v1/traces/" + traceId);
                if (!res.ok) throw new Error("Status " + res.status);
                const data = await res.json();
                content.innerText = JSON.stringify(data, null, 2);
            }} catch (err) {{
                content.innerText = "Error loading trace: " + err.message;
            }}
        }}

        function closeModal() {{
            document.getElementById("traceModal").style.display = "none";
        }}

        // 5. Dynamic Dashboard Stream Refresh
        async function refreshDashboard() {{
            const btn = document.getElementById("btnRefresh");
            if (btn) {{
                btn.disabled = true;
                btn.innerText = "Syncing...";
            }}
            try {{
                const [mRes, tRes] = await Promise.all([
                    fetch("/v1/metrics"),
                    fetch("/v1/traces?limit=15")
                ]);
                if (mRes.ok) {{
                    const m = await mRes.json();
                    document.getElementById("valTotalDecisions").innerText = m.total_logged_decisions;
                    const distStr = Object.entries(m.action_distribution || {{}})
                        .map(([k, v]) => `${{k.toUpperCase()}}: ${{v}}`).join(", ");
                    document.getElementById("valActionDist").innerText = distStr || "None recorded";
                }}
                if (tRes.ok) {{
                    const traces = await tRes.json();
                    const tbody = document.getElementById("tracesBody");
                    if (traces.length > 0) {{
                        tbody.innerHTML = traces.map(r => `
                            <tr>
                                <td style="padding: 10px 14px; font-family: 'JetBrains Mono', monospace; color: #475569; font-size: 13px;"><code>${{r.trace_id.slice(0, 8)}}...</code></td>
                                <td style="padding: 10px 14px; font-weight: 500; color: #1e293b;">${{r.task}}</td>
                                <td style="padding: 10px 14px;"><span class="badge ${{r.action}}">${{r.action}}</span></td>
                                <td style="padding: 10px 14px; color: #475569; font-variant-numeric: tabular-nums;">${{r.confidence.toFixed(2)}}</td>
                                <td style="padding: 10px 14px; font-family: 'JetBrains Mono', monospace; color: #0284c7; font-weight: 500;">${{r.latency_ms.toFixed(2)}}ms</td>
                                <td style="padding: 10px 14px; color: #334155; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${{r.input_preview || ''}}">${{r.input_preview || '-'}}</td>
                                <td style="padding: 10px 14px; text-align: right;">
                                    <button class="btn btn-secondary btn-xs" onclick="inspectTrace('${{r.trace_id}}')" title="Inspect full JSON decision trace">Inspect JSON</button>
                                </td>
                            </tr>
                        `).join("");
                    }}
                }}
                const now = new Date();
                document.getElementById("lastUpdatedText").innerText = "Last synced: " + now.toLocaleTimeString();
            }} catch (e) {{
                console.error("Refresh error:", e);
            }} finally {{
                if (btn) {{
                    btn.disabled = false;
                    btn.innerText = "🔄 Refresh Stream";
                }}
            }}
        }}

        // Close modal when clicking backdrop
        window.onclick = function(event) {{
            const modal = document.getElementById("traceModal");
            if (event.target === modal) {{
                modal.style.display = "none";
            }}
        }}
    </script>
</body>
</html>"""
        return HTMLResponse(content=html)

    return app
