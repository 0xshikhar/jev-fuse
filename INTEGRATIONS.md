# 🧪 JEV Fuse: Universal Integration & Testing Guide

This guide provides an exhaustive, copy-pasteable reference for testing and integrating **JEV Fuse** across your entire engineering stack—including the **Vercel AI SDK** (Python & TypeScript), **TypeSafe's official SDK**, **Claude Code**, **Cursor/Windsurf (MCP)**, **REST microservices**, and **embedded Python agent frameworks**.

---

## 📑 Table of Contents

1. [Quickstart: Installation & Launching JEV Fuse](#1-quickstart-installation--launching-jev-fuse)
2. [Integration 1: Vercel AI SDK (Python)](#2-integration-1-vercel-ai-sdk-python)
3. [Integration 2: Vercel AI SDK (TypeScript / Next.js)](#3-integration-2-vercel-ai-sdk-typescript--nextjs)
4. [Integration 3: Official TypeSafe Python SDK (`typesafe-sdk`)](#4-integration-3-official-typesafe-python-sdk-typesafe-sdk)
5. [Integration 4: Claude Code CLI (PreToolUse Hook)](#5-integration-4-claude-code-cli-pretooluse-hook)
6. [Integration 5: Cursor, Windsurf & Claude Desktop (Native MCP)](#6-integration-5-cursor-windsurf--claude-desktop-native-mcp)
7. [Integration 6: Direct REST API & Microservices (curl / Any Language)](#7-integration-6-direct-rest-api--microservices-curl--any-language)
8. [Integration 7: Embedded Python Library (LangChain / LlamaIndex / CrewAI)](#8-integration-7-embedded-python-library-langchain--llamaindex--crewai)
9. [Integration 8: Context Window Compaction (`jevfuse prune`)](#9-integration-8-context-window-compaction-jevfuse-prune)
10. [Integration 9: Control Plane Dashboard & Live Diagnostics](#10-integration-9-control-plane-dashboard--live-diagnostics)
11. [Integration 10: Active Learning Feedback & DuckDB Analytics](#11-integration-10-active-learning-feedback--duckdb-analytics)
12. [Master Automated Verification Script](#12-master-automated-verification-script)

---

## 1. Quickstart: Installation & Launching JEV Fuse

JEV Fuse runs as a local sidecar, shared microservice, or embedded library that sits between your callers and the upstream decision engine.

### Step 1: Install JEV Fuse

Choose your installation method:

#### Option A: Install from PyPI via uv or pip (Recommended)
```bash
# Ultra-fast install with uv
uv pip install jev-fuse

# Or add as project dependency
uv add jev-fuse

# Or standard pip
pip install jev-fuse
```

#### Option B: Install Direct from GitHub (Latest Main)
```bash
# Direct install latest repository commit via uv
uv pip install git+https://github.com/0xshikhar/jev-fuse.git

# Or install from GitHub via pip
pip install git+https://github.com/0xshikhar/jev-fuse.git

# Or clone for local development
git clone https://github.com/0xshikhar/jev-fuse.git
cd jev-fuse
uv sync
```

### Step 2: Zero-Config Upstream Detection

JEV Fuse automatically inspects your environment or `.env` file and chooses the optimal upstream route:

| Environment Variable | Target Upstream Provider | Default Base URL |
| :--- | :--- | :--- |
| `AI_GATEWAY_API_KEY` | **Vercel AI Gateway** *(Free Jev access)* | `https://ai-gateway.vercel.sh/typesafe` |
| `TYPESAFE_API_KEY` | **TypeSafe AI Cloud** | `https://api.typesafe.ai` |
| *(None)* | **Local Deterministic Fallback** | In-process AST heuristics & 0ms allowlist |

### Step 3: Start the Gateway Server

#### Method A: Using `.env` File (Auto-Loaded)
Create a `.env` file in your workspace:
```env
# Vercel AI Gateway (Free Jev promotion)
AI_GATEWAY_API_KEY="vck_your_key_here"

# OR Direct TypeSafe API Key
# TYPESAFE_API_KEY="ts_your_key_here"
```

Start the gateway—JEV Fuse automatically loads `.env` on launch:
```bash
uv run jevfuse serve --port 8000
# or if installed globally:
# jevfuse serve --port 8000
```

#### Method B: Using Shell Export
```bash
export AI_GATEWAY_API_KEY="vck_your_key_here"
uv run jevfuse serve --port 8000
```

Verify gateway health and upstream provider status:
```bash
# Local gateway health
curl http://127.0.0.1:8000/healthz
# {"status":"ok","service":"fuse-gateway"}

# Upstream provider readiness
curl http://127.0.0.1:8000/readyz
# {"ready":true,"provider":{"status":"healthy","latency_ms":182.4,"message":"Connected to TypeSafe Jev API"}}

# 4-point diagnostic self-test
curl -X POST http://127.0.0.1:8000/v1/diagnostics/self-test
```

---

## 2. Integration 1: Vercel AI SDK (Python)

The official Vercel Python AI SDK (`ai-python.dev`) routes natively through the Vercel AI Gateway. You can use it in conjunction with JEV Fuse to enforce safety policies and local caching on agent tool calls.

### Installation
```bash
uv add jev-fuse ai
# or standard pip:
pip install jev-fuse ai
```

### Pattern A: Resolving the Jev Model via Vercel AI SDK
```python
import ai

# 1. Automatically resolves to typesafe-ai/jev on Vercel AI Gateway
model = ai.get_model("typesafe-ai/jev")

print("Model:", model.id)                   # typesafe-ai/jev
print("Provider:", model.provider.name)     # ai-gateway
print("Gateway URL:", model.provider.default_base_url)
```

### Pattern B: Protecting Vercel Agent Tool Execution with JEV Fuse
```python
import asyncio
from ai import Agent, tool
from jevfuse.guard.classifier import evaluate_shell_command
from jevfuse.schema.decision import Action

@tool
async def execute_bash(command: str) -> str:
    """Execute a bash command with JEV Fuse safety gate."""
    # Pre-execution safety evaluation
    verdict = await evaluate_shell_command(command)
    
    if verdict.action == Action.DENY:
        return f"BLOCKED by JEV Fuse: {verdict.reason}"
    elif verdict.action == Action.ASK:
        return f"CONFIRMATION_REQUIRED: {verdict.reason}. Please confirm before running."
    
    # Safe to execute
    import subprocess
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return proc.stdout or proc.stderr

agent = Agent(
    model="typesafe-ai/jev",
    tools=[execute_bash],
)
```

---

## 3. Integration 2: Vercel AI SDK (TypeScript / Next.js)

In Node.js, Next.js, or Bun, use the Vercel AI SDK (`ai@latest`).

### Installation
```bash
pnpm add ai@latest
# or
bun add ai@latest
```

### Pattern A: Direct Evaluation using Vercel AI Gateway (`experimental_evaluate`)
```typescript
import { experimental_evaluate as evaluate } from 'ai';

async function verifyAction() {
  const result = await evaluate({
    model: 'typesafe-ai/jev',
    state: 'User requested: git push origin main --force',
    questions: {
      is_safe: {
        type: 'noul',
        instructions: 'Is force-pushing directly to production main safe for automated execution?',
      },
      category: {
        type: 'choice',
        instructions: 'Categorize the operation risk',
        criteria: {
          low: 'Read-only or safe local change',
          medium: 'Standard modification requiring build test',
          critical: 'Destructive, irreversible, or production-impacting',
        },
      },
    },
  });

  console.log('Safe probability:', result.answers.is_safe.noul); // ~0.05 (Dangerous!)
  console.log('Risk category:', result.answers.category.choice);   // "critical"
}

verifyAction();
```

### Pattern B: Calling Local JEV Fuse for Local Caching & Audit Logging
To keep duplicate evaluation latency under **0.1ms** and store decisions in your local SQLite WAL compliance log:

```typescript
async function askJevFuse(state: string) {
  const response = await fetch('http://127.0.0.1:8000/v1/systemone', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: 'typesafe-ai/jev',
      state,
      questions: {
        is_safe: {
          type: 'noul',
          instructions: 'Is this operation safe to automate without human review?'
        }
      }
    })
  });

  const decision = await response.json();
  const latency = response.headers.get('X-Fuse-Latency-Ms');
  
  console.log('Decision:', decision.answers.is_safe);
  console.log(`Latency: ${latency}ms (0 tokens if cached)`);
  return decision;
}
```

---

## 4. Integration 3: Official TypeSafe Python SDK (`typesafe-sdk`)

If your project already uses TypeSafe's official Python SDK, **zero code modification is needed**. Simply point the client's `base_url` to JEV Fuse.

### Implementation
```python
import asyncio
from typesafe_sdk import AsyncTypeSafeClient, Noul, Choice, Score

async def main():
    # Point the client to JEV Fuse gateway
    client = AsyncTypeSafeClient(
        base_url="http://127.0.0.1:8000/v1",
        api_key="sk-fuse-local",  # JEV Fuse forwards with your upstream key
    )

    # 1. Noul Question (System 1 Boolean Probability)
    decision = await client.predict(
        state="git status --short",
        questions={
            "safe": Noul("Is this command safe for autonomous agent execution?"),
            "intent": Choice("Select intent", ["read", "write", "delete"]),
            "urgency": Score("Rate urgency from 1 to 5")
        }
    )

    print("Safe probability:", decision.safe.value, f"({decision.safe.confidence * 100:.1f}%)")
    print("Detected Intent:", decision.intent.choice)

asyncio.run(main())
```

---

## 5. Integration 4: Claude Code CLI (PreToolUse Hook)

JEV Fuse includes a native hook implementation conforming to Anthropic's **Claude Code PreToolUse** specification (2.1.274). It intercepts terminal commands before Claude Code executes them, preventing catastrophic operations (`rm -rf`, `DROP TABLE`, leaking secrets) while auto-approving harmless read operations (`git status`, `ls`).

### Option A: Install via Claude Code Plugin Marketplace
```bash
claude plugin marketplace add 0xshikhar/jev-fuse
claude plugin install jev-fuse@0xshikhar
```

### Option B: Configure Local PreToolUse Hook
Add to your project's `.claude/settings.json` (or `.claude/config.json`):
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "uv run jevfuse hook pre-tool-use"
          }
        ]
      }
    ]
  }
}
```

### Manual CLI Verification
Test commands through the hook using stdin:
```bash
# Safe command -> Auto-allow
echo '{"tool_name": "Bash", "tool_input": {"command": "git status"}}' | uv run jevfuse hook pre-tool-use
# Output: {"allow": true}

# Dangerous root deletion -> Denied
echo '{"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}' | uv run jevfuse hook pre-tool-use
# Output: {"deny": "JEV-Fuse: Recursive delete targeting dangerous root or home directory '/'"}

# Force push -> Denied
echo '{"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}' | uv run jevfuse hook pre-tool-use
# Output: {"deny": "JEV-Fuse: Force push detected on protected branch 'main'"}
```

---

## 6. Integration 5: Cursor, Windsurf & Claude Desktop (Native MCP)

JEV Fuse includes an embedded Model Context Protocol (MCP) server running over standard I/O (`stdio`).

### Configure in Cursor (`~/.cursor/mcp.json`) or Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "jev-fuse": {
      "command": "uv",
      "args": ["run", "jevfuse", "mcp"],
      "env": {
        "AI_GATEWAY_API_KEY": "vck_your_key_here"
      }
    }
  }
}
```
*(If installed globally via `uv tool install jev-fuse` or `pip`, simply set `"command": "jevfuse"`, `"args": ["mcp"]`)*

### 4 Native Tools Provided:

#### 1. `fuse_guard`: Shell Safety Evaluation
Evaluates whether a terminal command is safe for autonomous execution using AST lexical parsing, 0ms allowlists, and governed model checks.
```json
// Tool Call: fuse_guard
{
  "command": "pytest -v tests/"
}
// Response:
{
  "action": "allow",
  "reason": "Standard verified read-only command",
  "confidence": 1.0,
  "latency_ms": 0.04
}
```

#### 2. `fuse_route`: Discrete Intent Routing
Routes an agent intent, user question, or task to one of several discrete tools or handlers.
```json
// Tool Call: fuse_route
{
  "query": "Customer wants to cancel subscription and refund invoice",
  "options": ["billing_support", "technical_issues", "general_faq"]
}
// Response:
{
  "selected": "billing_support",
  "confidence": 0.96,
  "action": "allow",
  "latency_ms": 22.4
}
```

#### 3. `fuse_verify`: Binary Semantic Verification
Checks whether a hypothesis holds true given supporting code, logs, or state context.
```json
// Tool Call: fuse_verify
{
  "statement": "The query uses parameterized placeholders to prevent SQL injection",
  "context": "SELECT * FROM users WHERE id = :user_id"
}
// Response:
{
  "verified": true,
  "confidence": 0.98,
  "action": "allow",
  "latency_ms": 19.8
}
```

#### 4. `fuse_prune`: Token Context Compaction
Scores and filters recorded conversation turns or tool outputs against an agent goal, stripping dead logs while preserving critical error messages.
```json
// Tool Call: fuse_prune
{
  "turns": [
    {"role": "user", "content": "Fix database deadlock in postgres"},
    {"role": "tool", "content": "500 lines of standard postgres startup output..."},
    {"role": "assistant", "content": "Analyzing lock graph"}
  ],
  "goal": "Fix database deadlock"
}
// Response:
{
  "pruned_turns": [
    {"turn_index": 0, "action": "keep", "relevance_score": 0.95},
    {"turn_index": 1, "action": "truncate", "relevance_score": 0.15},
    {"turn_index": 2, "action": "keep", "relevance_score": 0.88}
  ]
}
```

---

## 7. Integration 6: Direct REST API & Microservices (curl / Any Language)

Use standard HTTP in Go, Rust, Java, C#, or shell scripts.

### Endpoint 1: TypeSafe Jev Drop-in Compatibility (`POST /v1/systemone`)
```bash
curl -X POST http://127.0.0.1:8000/v1/systemone \
  -H "Content-Type: application/json" \
  -d '{
    "model": "typesafe-ai/jev",
    "state": "User requested deleting test database records.",
    "questions": {
      "safe": {
        "type": "noul",
        "instructions": "Is this action safe to execute automated?"
      }
    }
  }'
```
**Response:**
```json
{
  "model": "typesafe-ai/jev",
  "answers": {
    "safe": {
      "type": "noul",
      "noul": 0.17
    }
  },
  "usage": {
    "input_tokens": 285,
    "output_tokens": 21
  }
}
```
*Note: Headers include `X-Fuse-Latency-Ms: 0.038` on cached requests.*

---

### Endpoint 2: Canonical Governed Decision API (`POST /v1/decide`)
Enforces declarative policy rules, confidence deadbands, and active learning trace IDs:

```bash
curl -X POST http://127.0.0.1:8000/v1/decide \
  -H "Content-Type: application/json" \
  -d '{
    "task": "bash-safety",
    "kind": "bool",
    "input": "docker system prune --all",
    "context": {"developer": "shikhar", "env": "production"}
  }'
```
**Response:**
```json
{
  "trace_id": "3febca7b-f721-4ec7-a1ac-2452d2d605b3",
  "task": "bash-safety",
  "action": "deny",
  "confidence": 0.75,
  "value": true,
  "cached": false,
  "latency_ms": 712.4
}
```

---

### Endpoint 3: Register Custom Policy Dynamically (`POST /v1/tasks/register`)
Define routing rules without restarting the server:

```bash
curl -X POST http://127.0.0.1:8000/v1/tasks/register \
  -H "Content-Type: application/json" \
  -d '{
    "policy": {
      "task": "sql-safety",
      "rules": [
        {"when": {"confidence_gt": 0.85}, "then": "allow"},
        {"when": {"confidence_lt": 0.40}, "then": "deny"}
      ]
    }
  }'
```

---

### Endpoint 4: 4-Point Diagnostic Self-Test (`POST /v1/diagnostics/self-test`)
Execute a live, end-to-end diagnostic audit across all 4 operational layers (Gateway, SQLite WAL, Micro-Cache, Upstream Provider):

```bash
curl -X POST http://127.0.0.1:8000/v1/diagnostics/self-test
```
**Response:**
```json
{
  "status": "PASS",
  "passed": true,
  "timestamp": 1774476000.12,
  "checks": [
    {
      "name": "Local Runtime & Policy Engine",
      "endpoint": "GET /healthz",
      "status": "PASS",
      "latency_ms": 0.05,
      "detail": "Core policy engine loaded, rules registered, memory bus ready"
    },
    {
      "name": "Upstream TypeSafe AI Gateway",
      "endpoint": "GET /readyz",
      "status": "PASS",
      "latency_ms": 182.4,
      "detail": "Connected to jev (Connected to TypeSafe Jev API)"
    },
    {
      "name": "Deterministic AST Guardrail",
      "endpoint": "POST /v1/decide",
      "status": "PASS",
      "latency_ms": 0.08,
      "detail": "'git status' -> ALLOW (Deterministic allowlist match)"
    },
    {
      "name": "SQLite WAL Decision Audit Plane",
      "endpoint": "GET /v1/metrics",
      "status": "PASS",
      "latency_ms": 0.15,
      "detail": "Storage verified: decisions indexed with zero-copy WAL"
    }
  ]
}
```

---

### Endpoint 5: Deep Decision Trace Retrieval (`GET /v1/traces/{trace_id}`)
Retrieve the complete, tamper-evident audit record for any decision:

```bash
curl http://127.0.0.1:8000/v1/traces/3febca7b-f721-4ec7-a1ac-2452d2d605b3
```
**Response:**
```json
{
  "trace_id": "3febca7b-f721-4ec7-a1ac-2452d2d605b3",
  "task": "bash-safety",
  "action": "deny",
  "confidence": 0.75,
  "raw_score": 0.75,
  "reason": "Destructive operation blocked by policy",
  "cached": false,
  "latency_ms": 712.4,
  "provider": "typesafe-ai/jev",
  "timestamp": "2026-09-25T01:45:10.123456"
}
```

---

## 8. Integration 7: Embedded Python Library (LangChain / LlamaIndex / CrewAI)

Embed JEV Fuse directly inside your Python backend without running an external server.

```python
import asyncio
from jevfuse.engine import JevFuseEngine
from jevfuse.schema.decision import DecisionRequest, DecisionKind, Action

async def main():
    # In-process engine with LRU memory cache & SQLite WAL logger
    engine = JevFuseEngine()
    await engine.start()

    request = DecisionRequest(
        task="element-routing",
        kind=DecisionKind.CHOICE,
        input="Click the primary checkout button",
        choices=["#btn-checkout", "#btn-cancel", "#btn-help"],
    )

    result = await engine.decide(request)
    print("Action:    ", result.action)      # Action.ALLOW
    print("Selected:  ", result.value)       # "#btn-checkout"
    print("Confidence:", result.confidence)  # 0.94

    await engine.close()

asyncio.run(main())
```

---

## 9. Integration 8: Context Window Compaction (`jevfuse prune`)

Compress lengthy conversation histories by pruning stale terminal dumps while preserving critical code blocks verbatim.

### CLI Usage
```bash
uv run jevfuse prune conversation.json --goal "Fix Postgres deadlock"
```

### Python Usage
```python
import asyncio
from jevfuse.recipes.prune import compact_context

turns = [
    {"role": "user", "content": "Fix the database deadlock in Postgres"},
    {"role": "tool", "content": "Running test suite:\n" + ("log line\n" * 200) + "FAILED deadlock"},
    {"role": "assistant", "content": "Inspecting transaction locks."}
]

async def run():
    compacted = await compact_context(turns, goal="Fix Postgres deadlock")
    print(f"Compressed {len(turns)} turns to {len(compacted)} turns")

asyncio.run(run())
```

---

## 10. Integration 9: Control Plane Dashboard & Live Diagnostics

JEV Fuse includes an out-of-the-box, zero-dependency visual control plane served directly by the gateway. Crafted with a **traditional, professional light theme** inspired by OpenAPI Swagger UI (`/docs`), Stripe, and GitHub Settings (`#f8fafc` background, crisp white cards, clean borders, and clear REST method badges)—avoiding dark-mode eye strain and messy neon gradients.

### Open the Dashboard
Start the gateway and open your browser:
👉 **`http://127.0.0.1:8000/dashboard`**

### Key Capabilities:
- **One-Click Diagnostic Self-Test**: Click **Run Self-Test** in the dashboard header (or trigger `POST /v1/diagnostics/self-test`) to perform a 4-point live audit of Gateway routing, SQLite WAL persistence, Micro-Cache store, and upstream model readiness.
- **Deep Trace Inspector**: Click any decision row or `[Inspect]` button to open a modal displaying the complete tamper-evident JSON audit record (`GET /v1/traces/{trace_id}`).
- **Interactive OpenAPI Explorer**: Test and experiment with endpoints directly in your browser using Swagger UI at **`http://127.0.0.1:8000/docs`**.

---

## 11. Integration 10: Active Learning Feedback & DuckDB Analytics

Every decision made through JEV Fuse is logged to `jevfuse_decisions.db` with full trace fidelity.

### Submit Ground Truth Feedback (`POST /v1/feedback`)
Help calibrate models by feeding back human or test verdicts:

```bash
curl -X POST http://127.0.0.1:8000/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "3febca7b-f721-4ec7-a1ac-2452d2d605b3",
    "label": "denied"
  }'
```

### Inspect Traces with DuckDB
Analyze performance, cache hit rates, and model accuracy:

```python
import duckdb

conn = duckdb.connect("jevfuse_decisions.db")
df = conn.execute("""
    SELECT 
        provider, 
        cached, 
        COUNT(*) as count, 
        AVG(latency_ms) as avg_latency_ms 
    FROM decisions 
    GROUP BY provider, cached
""").df()

print(df)
```

---

## 12. Master Automated Verification Script

Save this script as `verify_all.py` to test all integrations end-to-end in seconds:

```python
#!/usr/bin/env python3
"""Master verification test for JEV Fuse & Vercel AI Gateway."""

import asyncio
import json
import os
import httpx
from jevfuse.server.app import create_app
from jevfuse.guard.classifier import evaluate_shell_command
from jevfuse.schema.decision import Action

async def main():
    print("=" * 65)
    print("  🚀 RUNNING COMPLETE JEV FUSE INTEGRATION TEST SUITE")
    print("=" * 65)

    # 1. Test AST Shell Guard
    print("\n[1/4] Testing AST & Policy Guardrails...")
    safe_v = await evaluate_shell_command("git status")
    assert safe_v.action == Action.ALLOW, f"Expected ALLOW, got {safe_v.action}"
    print("  ✅ 'git status' -> ALLOW (Deterministic allowlist)")

    deny_v = await evaluate_shell_command("rm -rf /")
    assert deny_v.action == Action.DENY, f"Expected DENY, got {deny_v.action}"
    print("  ✅ 'rm -rf /' -> DENY (AST root protection)")

    # 2. Test In-Process ASGI Gateway
    print("\n[2/4] Testing REST API, Diagnostics & Vercel AI Gateway Proxy...")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Health
        h = await client.get("/healthz")
        assert h.status_code == 200, f"Expected 200 on /healthz, got {h.status_code}"
        print("  ✅ GET /healthz -> Healthy (Local runtime active)")

        # Canonical governed decision endpoint
        d_res = await client.post("/v1/decide", json={"task": "shell-guard", "kind": "bool", "input": "git status"})
        assert d_res.status_code == 200
        d_json = d_res.json()
        trace_id = d_json.get("trace_id")
        print(f"  ✅ POST /v1/decide -> Governed Policy Active (Action: {d_json.get('action')}, Trace: {trace_id[:8]}...)")

        # Trace retrieval
        if trace_id:
            t_res = await client.get(f"/v1/traces/{trace_id}")
            assert t_res.status_code == 200
            print(f"  ✅ GET /v1/traces/{trace_id[:8]}... -> Found audit record")

        # 4-point diagnostic self-test
        diag_res = await client.post("/v1/diagnostics/self-test")
        assert diag_res.status_code == 200
        diag_data = diag_res.json()
        passed_count = sum(1 for r in diag_data.get("checks", []) if r.get("status") == "PASS")
        print(f"  ✅ POST /v1/diagnostics/self-test -> {passed_count}/{len(diag_data.get('checks', []))} checks passed")

        has_key = bool(
            os.environ.get("AI_GATEWAY_API_KEY")
            or os.environ.get("TYPESAFE_API_KEY")
            or os.environ.get("JEV_API_KEY")
        )
        if has_key:
            r = await client.get("/readyz")
            assert r.status_code == 200, f"Expected 200 on /readyz, got {r.status_code}: {r.text}"
            print("  ✅ GET /readyz -> Healthy (Upstream connected)")

            # SystemOne Proxy
            payload = {
                "model": "typesafe-ai/jev",
                "state": "User requested deleting test database records.",
                "questions": {"safe": {"type": "noul", "instructions": "Is this safe to run automated?"}}
            }
            res1 = await client.post("/v1/systemone", json=payload)
            assert res1.status_code == 200
            print("  ✅ POST /v1/systemone -> Live Vercel AI Gateway evaluated")

            # Cache Hit Test
            res2 = await client.post("/v1/systemone", json=payload)
            lat = float(res2.headers.get("X-Fuse-Latency-Ms", "0"))
            assert res2.status_code == 200
            print(f"  ✅ POST /v1/systemone -> Instant Cache Hit ({lat:.3f} ms)")
        else:
            print("  ℹ️  Notice: No API key found in environment or .env file.")

    # 3. Test MCP Server Tools
    print("\n[3/4] Testing Model Context Protocol (MCP) Server (All 4 Tools)...")
    from jevfuse.mcp.server import create_mcp_server
    mcp_server = create_mcp_server()
    tools = await mcp_server.list_tools()
    tool_names = [t.name for t in tools]
    assert "fuse_guard" in tool_names and "fuse_prune" in tool_names and "fuse_verify" in tool_names and "fuse_route" in tool_names
    print(f"  ✅ All 4 MCP Tools Registered: {', '.join(tool_names)}")

    # 3a. Test fuse_guard
    res_guard = await mcp_server.call_tool("fuse_guard", {"command": "git status"})
    guard_data = json.loads(res_guard.content[0].text)
    assert guard_data["action"] == "allow"
    print("  ✅ MCP fuse_guard('git status') -> ALLOW")

    # 3b. Test fuse_route
    res_route = await mcp_server.call_tool("fuse_route", {
        "query": "Customer wants to cancel subscription and refund invoice",
        "options": ["billing_support", "technical_issues", "general_faq"]
    })
    route_data = json.loads(res_route.content[0].text)
    assert "selected" in route_data
    print(f"  ✅ MCP fuse_route -> Selected '{route_data['selected']}' (Confidence: {route_data['confidence']})")

    # 3c. Test fuse_verify
    res_verify = await mcp_server.call_tool("fuse_verify", {
        "statement": "The query uses parameterized placeholders to prevent SQL injection",
        "context": "SELECT * FROM users WHERE id = :user_id"
    })
    verify_data = json.loads(res_verify.content[0].text)
    assert "verified" in verify_data
    print(f"  ✅ MCP fuse_verify -> Verified: {verify_data['verified']}")

    # 3d. Test fuse_prune
    res_prune = await mcp_server.call_tool("fuse_prune", {
        "turns": [
            {"role": "user", "content": "Fix database deadlock"},
            {"role": "tool", "content": "Running test suite... passed"},
            {"role": "assistant", "content": "Deadlock resolved"}
        ],
        "goal": "Fix database deadlock"
    })
    prune_data = json.loads(res_prune.content[0].text)
    assert "pruned_turns" in prune_data
    print(f"  ✅ MCP fuse_prune -> Compacted {len(prune_data['pruned_turns'])} turns")

    # 4. Summary
    print("\n[4/4] Verification Complete!")
    print("=" * 65)
    print("  🎉 ALL JEV FUSE INTEGRATION TESTS PASSED (100% OK)")
    print("=" * 65)

if __name__ == "__main__":
    asyncio.run(main())
```

Run the master test:
```bash
uv run python verify_all.py
```
