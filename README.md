# JEV Fuse

<p align="center">
  <strong>The open governance runtime, safety gate, and audit plane for TypeSafe Jev & typed-decision models.</strong>
</p>

<p align="center">
  <em>Drop-in proxy for TypeSafe Jev & local Laya engines - turning raw probabilities into deterministic, governed actions.</em>
</p>

<p align="center">
  <a href="https://github.com/0xshikhar/jev-fuse/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License"></a>
  <a href="https://pypi.org/project/jev-fuse/"><img src="https://img.shields.io/badge/pypi-v0.1.0-blue" alt="PyPI"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/python-3.12+-brightgreen.svg" alt="Python Version"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-Native-purple.svg" alt="MCP Native"></a>
  <a href="https://typesafe.ai"><img src="https://img.shields.io/badge/Wire-SystemOne_Compatible-orange.svg" alt="SystemOne Wire"></a>
</p>

---

## ⚡ The Problem: Raw Probabilities vs. Production Execution

Decision models (such as TypeSafe Jev or local Laya engines) return calibrated probabilities across typed schemas (Noul, Choice, Score) in under 35ms. However, putting raw probability scores directly in front of production agents, shell tools, or critical business workflows introduces severe operational vulnerabilities:

- **Razor-Thin Boundary Hazards**: If a runtime relies on naive cutoffs ($P > 0.50$), edge cases ($P = 0.51$ vs $0.49$) produce catastrophic unintended actions without fail-closed bounds.
- **Unchecked Permissions & Prompt Fatigue**: Ungoverned agents either prompt developers for confirmation on every harmless read (`git status`, `ls`) or fail open on hazardous commands.
- **Shell Parser Bypasses**: Malicious or accidental command execution cannot be caught with regexes; constructs like `find . -exec ...`, subshells `$(...)`, backticks, and redirects easily bypass pattern matching.
- **Bursty API & Quota Exhaustion**: Fast agent loops generate duplicate concurrent evaluations, exhausting rate limits and incurring unnecessary latency and token costs.
- **Zero Verifiable Audit Trail**: Without a persistent, tamper-evident decision log, developers have no replayable record explaining why an autonomous system allowed, blocked, or altered an execution.

---

## 🛡️ What JEV Fuse Does

JEV Fuse sits as a transparent, high-performance reverse proxy between your callers (coding agents, microservices, CLI tools, and SDKs) and decision engines. It translates raw, uncalibrated model probabilities into durable, deterministic policy actions:

$$\text{Raw Model Probability } P \xrightarrow{\quad\mathbf{JEV\ Fuse}\quad} \mathbf{Action} \in \{\text{ALLOW}, \text{ASK}, \text{DENY}, \text{KEEP}, \text{TRUNCATE}, \text{DROP}\}$$

```
                      ┌────────────────────────────────────────────────────────┐
                      │              Callers & Integrations                    │
                      │  Claude Code  │  Cursor / MCP  │  Official Python SDK   │
                      └───────────────────────────┬────────────────────────────┘
                                                  │
                                                  ▼ (POST /v1/systemone)
                      ┌────────────────────────────────────────────────────────┐
                      │                       JEV Fuse                         │
                      │  ┌─────────────────┐ ┌───────────────┐ ┌─────────────┐ │
                      │  │ AST Guard Gate  │ │ Singleflight  │ │ WAL Audit   │ │
                      │  │  (4-Tier Safe)  │ │ (Deduplicate) │ │  (DuckDB)   │ │
                      │  └─────────────────┘ └───────────────┘ └─────────────┘ │
                      └───────────────────────────┬────────────────────────────┘
                                                  │
                                                  ▼
                      ┌────────────────────────────────────────────────────────┐
                      │              TypeSafe Jev / Local Laya                 │
                      └────────────────────────────────────────────────────────┘
```

### Core Capabilities

1. **Zero-Refactor Universal Wire (`POST /v1/systemone`)**: Existing applications, scripts, and official SDKs point directly to JEV Fuse simply by setting `base_url="http://127.0.0.1:8000"` or `export TYPESAFE_BASE_URL="http://127.0.0.1:8000/v1"`.
2. **Deterministic 4-Tier Guard**: Combines shell AST lexical parsing, 0ms denylists, 0ms verified allowlists, and governed model evaluation. Harmless reads execute in 0ms; catastrophic commands are blocked before touching any model.
3. **Deadband Margin Abstention**: When confidence is ambiguous ($0.40 \le P \le 0.60$), JEV Fuse safely abstains (`action: ask`), requesting human confirmation instead of guessing on razor-thin margins.
4. **Singleflight Concurrency Coalescing**: Concurrent identical evaluations merge into a single upstream request, preventing quota exhaustion and saving up to 98% of upstream API cost during traffic bursts.
5. **Two-Tier Micro-Caching ($<5\text{ms}$)**: In-memory LRU + persistent SQLite WAL caching ensures duplicate evaluations return instantaneously with zero network egress.
6. **Tamper-Evident SQLite WAL Audit Trail + DuckDB Analytics**: Records trace IDs, input hashes, calibrated confidences, execution latencies, and human feedback for offline evaluation and Expected Calibration Error (ECE) tracking.

---

## ⏱️ Quickstart: Get Running in 30 Seconds

### 1. Install JEV Fuse
```bash
pip install jev-fuse
# or using uv:
uv pip install jev-fuse
```

### 2. Start the Gateway
```bash
export TYPESAFE_API_KEY="your-typesafe-api-key"
jevfuse serve --port 8000
```

### 3. Point Your Existing Code or Agent
In your existing TypeSafe SDK application, just point `base_url` to JEV Fuse:
```python
from typesafe_sdk import AsyncTypeSafeClient

client = AsyncTypeSafeClient(
    base_url="http://127.0.0.1:8000",
    api_key="your-typesafe-api-key",
)
```
Or protect Claude Code terminal commands:
```bash
claude plugin marketplace add 0xshikhar/jev-fuse
claude plugin install jev-fuse@0xshikhar
```

---

## 🛡️ The 4-Tier Guard Pipeline

When evaluating commands or agent actions, JEV Fuse runs a zero-trust, four-tier evaluation pipeline before executing or abstaining:

```
Incoming Shell Command / Agent Action
  │
  ├─ Tier 1: Shell Tokenizer & AST Structure Check
  │     └─ Catches unparseable syntax, command substitutions $(...), subshells `...`, redirection tricks -> ASK
  │
  ├─ Tier 2: Deterministic Denylist (0ms Network / 0 Tokens)
  │     └─ Immediately blocks `rm -rf /`, `find -exec`, `git push -f`, `dd of=/dev/`, `curl | sh`, `sudo` -> DENY
  │
  ├─ Tier 3: Deterministic Allowlist (0ms Network / 0 Tokens)
  │     └─ Instantly approves `git status`, `git diff`, `git log`, `pytest`, `cargo test`, `npm test`, `ls` -> ALLOW
  │
  └─ Tier 4: Governed Decision Model Fallback (TypeSafe Jev or Local Laya)
        ├─ High probability harm (P > 0.70) ────────► DENY
        ├─ Confidence deadband margin (0.40 <= P <= 0.60) ──► ASK (fails closed toward human confirmation)
        └─ High probability safe (P < 0.30) ────────► ALLOW
```

---

## 🚀 6 Ways to Integrate JEV Fuse

JEV Fuse is designed to fit into any stack-whether you are using the official Python SDK, Claude Code, Cursor, a terminal CLI, raw REST APIs, or embedding directly into your Python codebase.

---

### Integration 1: Official TypeSafe Python SDK (`typesafe-sdk`)

If your project already uses TypeSafe's official Python SDK (`typesafe-sdk`), **you do not even need to import JEV Fuse in your application code**. JEV Fuse runs as a local sidecar or gateway (`jevfuse serve`). Your application continues using the official SDK, simply pointing `base_url` to your JEV Fuse gateway:

```python
import asyncio
from typesafe_sdk import AsyncTypeSafeClient, Noul, Choice, Score

async def main():
    # 1. Point official client to JEV Fuse
    client = AsyncTypeSafeClient(
        base_url="http://127.0.0.1:8000",
        api_key="your-typesafe-api-key",
    )

    # 2. Binary Question (Noul: P between 0.0 and 1.0)
    res_noul = await client.system_one(
        state="git push --force origin main",
        questions={
            "is_destructive": Noul(instructions="Does this command rewrite remote repository history?")
        },
    )
    print("Noul P:", res_noul.answers["is_destructive"].noul)

    # 3. Categorical Routing (Choice: calibrated multi-class distribution)
    res_choice = await client.system_one(
        state="Customer requested full refund due to broken glass on arrival.",
        questions={
            "intent": Choice(
                instructions="Determine customer support intent",
                criteria={
                    "refund": "Customer demands money back",
                    "replacement": "Customer requests replacement shipment",
                    "inquiry": "General question",
                },
            )
        },
    )
    print("Detected Intent:", res_choice.answers["intent"].choice)
    print("Confidence:", res_choice.answers["intent"].confidence)

    # 4. Ordinal Rating (Score: continuous calibrated level)
    res_score = await client.system_one(
        state="SELECT * FROM orders WHERE status = 'pending';",
        questions={
            "query_risk": Score(
                instructions="Assess database performance risk",
                criteria=["safe", "low_cost", "table_scan_risk", "catastrophic"],
            )
        },
    )
    print("Risk Score:", res_score.answers["query_risk"].score)

if __name__ == "__main__":
    asyncio.run(main())
```

> **Note:** For tools using the `TYPESAFE_BASE_URL` environment variable, export:
> ```bash
> export TYPESAFE_BASE_URL="http://127.0.0.1:8000/v1"
> ```

---

### Integration 2: Claude Code (`PreToolUse` Hook & Plugin)

Protect your terminal from runaway agents while eliminating confirmation prompt fatigue on safe developer commands (`git status`, `pytest`, `ls`).

#### A. Install via Claude Code Plugin Marketplace
```bash
claude plugin marketplace add 0xshikhar/jev-fuse
claude plugin install jev-fuse@0xshikhar
```

#### B. Configure Local PreToolUse Hook
Add this entry to your project's `.claude/settings.json`:
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

#### How Claude Code Interacts with JEV Fuse:
```
Claude Code wants to run a command
  │
  ├─ `git status` ─────────────► JEV Fuse evaluates: ALLOW ────► Executes immediately (no popup)
  ├─ `rm -rf /` ───────────────► JEV Fuse evaluates: DENY ─────► Execution blocked
  └─ `cat ~/.ssh/id_rsa` ──────► JEV Fuse evaluates: ASK ──────► Prompts user with explanation
```

---

### Integration 3: Cursor, Windsurf & Claude Desktop via MCP Server

JEV Fuse includes a native [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server exposing safety, pruning, and verification tools over standard I/O (`stdio`).

#### Add to your MCP Settings (`claude_desktop_config.json` or `.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "jev-fuse": {
      "command": "uv",
      "args": ["run", "jevfuse", "mcp"]
    }
  }
}
```

#### Tools Provided by the MCP Server:
| MCP Tool | Purpose | Arguments |
|---|---|---|
| `fuse_guard` | Real-time shell command safety verification | `{"command": "pytest -v"}` |
| `fuse_prune` | Fast token context compaction without narrative loss | `{"turns": [...], "goal": "..."}` |
| `fuse_verify` | Fast binary verification of conditions | `{"statement": "...", "context": "..."}` |
| `fuse_route` | Calibrated routing among bounded choices | `{"task": "...", "options": [...]}` |

---

### Integration 4: Standalone Terminal CLI (`jevfuse`)

Run safety checks, conversation compactions, or start the server straight from your terminal:

```bash
# 1. Start the HTTP/2 REST Gateway (default port 8000)
jevfuse serve --port 8000

# 2. Evaluate shell command safety
jevfuse guard "git diff"
# -> [JEV Fuse Guard Verdict] ALLOW (Confidence: 1.00)

jevfuse guard "rm -rf /"
# -> [JEV Fuse Guard Verdict] DENY (Exit code: 1)

# 3. Compact conversation history against an agent goal
jevfuse prune history.json --goal "Fix authentication timeout in auth.py"

# 4. Run the Claude Code hook via stdin/stdout pipe
echo '{"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git log"}}' | jevfuse hook pre-tool-use
```

---

### Integration 5: REST API (cURL, Python `httpx`, Node.js `fetch`)

The JEV Fuse gateway exposes standard HTTP endpoints:

#### A. TypeSafe Universal Wire Format (`POST /v1/systemone` or `POST /v1/predict`)
```bash
curl -X POST http://127.0.0.1:8000/v1/systemone \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-typesafe-api-key" \
  -d '{
    "state": "Customer asking for full refund for damaged shipment.",
    "questions": {
      "is_urgent": {
        "type": "noul",
        "instructions": "Is this inquiry urgent?"
      },
      "intent": {
        "type": "choice",
        "instructions": "Determine intent",
        "criteria": {
          "refund": "Wants money back",
          "support": "Needs technical assistance"
        }
      }
    }
  }'
```

#### B. Governed Policy Decision Endpoint (`POST /v1/decide`)
Maps probabilities into governed actions according to your YAML policy:
```bash
curl -X POST http://127.0.0.1:8000/v1/decide \
  -H "Content-Type: application/json" \
  -d '{
    "task": "shell-guard",
    "kind": "bool",
    "input": "git reset --hard HEAD~1"
  }'
```

#### C. Health & System Models
```bash
# Gateway health check
curl http://127.0.0.1:8000/v1/health

# Available models
curl http://127.0.0.1:8000/v1/models
```

#### D. Zero-Build Telemetry Dashboard
Open **`http://127.0.0.1:8000/dashboard`** in your browser to view live SQLite WAL metrics, latency distributions (`X-Fuse-Latency-Ms`), and decision audit breakdown.

---

### Integration 6: Direct Embedded Python Library (`import jevfuse`)

If you prefer **not** to run a standalone server and want to evaluate decisions directly in-process within your Python application or agent pipeline, you can import JEV Fuse as a native Python library:

```python
import asyncio
from jevfuse import Action, DecisionKind, DecisionRequest, JevFuseEngine

async def main():
    # Instantiate in-process engine
    engine = JevFuseEngine(
        db_path="jevfuse_decisions.db",
        cache_db_path="jevfuse_cache.db",
    )
    await engine.start()

    # Dispatch governed decision
    req = DecisionRequest(
        task="shell-guard",
        kind=DecisionKind.BOOL,
        input="npm run build",
    )
    decision = await engine.decide(req)

    print("Governed Action:", decision.action)      # Action.ALLOW, Action.ASK, Action.DENY
    print("Model Confidence:", decision.confidence)
    print("Execution Reason:", decision.reason)

    await engine.close()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🖥️ Live Control Plane Dashboard & Trace Inspector

JEV Fuse includes an out-of-the-box, zero-dependency real-time web dashboard served directly by the gateway. When you run `jevfuse serve`, open your browser to:

👉 **`http://127.0.0.1:8000/dashboard`**

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  ⚡ JEV Fuse Control Plane                                             ● OPERATIONAL   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  [ Total Decisions: 1,420 ]  [ Action Split: 82% ALLOW | 14% ASK | 4% DENY ]          │
│  [ P95 Latency: 4.2ms ]      [ Cache Hit Ratio: 68.4% ]  [ Singleflight Saves: 310 ]   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  RECENT DECISION AUDIT LOG                                                             │
│  Trace ID    Task         Action   Confidence  Latency   Preview           Inspect     │
│  ──────────  ───────────  ───────  ──────────  ────────  ────────────────  ──────────  │
│  a1b2c3d4    shell-guard  ALLOW    0.98        0.12ms    git status        [Inspect]   │
│  e5f6g7h8    shell-guard  DENY     0.99        0.08ms    rm -rf /          [Inspect]   │
│  i9j0k1l2    prune        KEEP     0.84        18.4ms    tool_call_102     [Inspect]   │
│  m3n4o5p6    shell-guard  ASK      0.52        22.1ms    curl -fsSL ...    [Inspect]   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Live Activity & Action Breakdown**: Track real-time distribution of `ALLOW`, `ASK`, and `DENY` verdicts across all connected agent sessions.
- **Deep Trace Inspector**: Click any decision row to view the full tamper-evident JSON trace—including exact input hashes, model version, rubric probability distributions, and policy execution reasons.
- **Human-in-the-Loop Feedback**: Label ambiguous decisions directly from the dashboard to train offline calibration curves and monitor Expected Calibration Error (ECE).
- **Interactive OpenAPI Explorer**: Test and experiment with endpoints directly in your browser using Swagger UI at **`http://127.0.0.1:8000/docs`**.

---

## 💻 Terminal CLI Commands Reference

JEV Fuse provides a first-class CLI (`jevfuse`) for operators, terminal developers, and CI/CD pipelines:

### 1. Start Gateway & Web Dashboard
```bash
# Start on default port 8000 with auto-reload for local development
jevfuse serve --port 8000 --reload

# Bind to custom interface and port
jevfuse serve --host 0.0.0.0 --port 8080
```

### 2. Standalone Shell Guard (CI/CD & Terminal Gate)
Evaluate any command's safety directly in your shell or bash pipeline. Returns exit codes matching standard CI conventions:
* `Exit 0` = **ALLOW**
* `Exit 1` = **DENY**
* `Exit 2` = **ASK** (uncertain/deadband)

```bash
# Test a harmless command (Exit 0: ALLOW in 0ms)
jevfuse guard "git status"

# Test a dangerous command (Exit 1: DENY in 0ms)
jevfuse guard "rm -rf /"

# Test an ambiguous command (Exit 2: ASK)
jevfuse guard "curl -s http://example.com/install.sh | bash"
```

### 3. Run MCP Stdio Server
Spawn an MCP-compliant JSON-RPC stdio daemon for Cursor, Claude Desktop, or Windsurf:
```bash
jevfuse mcp
```

### 4. Claude Code PreToolUse Hook
Wire directly into Claude Code's tool execution harness:
```bash
jevfuse hook pre-tool-use
```

### 5. Offline Context Compaction
Prune and compact recorded conversation turn JSON files using goal-directed relevance scoring:
```bash
jevfuse prune transcript.json --goal "Refactor user authentication service"
```

---

## 🧠 Deep Dive: Understanding System 1 vs. System 2 Models

To understand why Jev and JEV Fuse exist, it helps to look at the architectural divide in modern AI systems:

```
┌────────────────────────────────────────────────────────┐   ┌────────────────────────────────────────────────────────┐
│            System 2: Generative LLMs                   │   │         System 1: Typed-Decision Models                │
│       (Claude 3.5 Sonnet, GPT-4o, Llama 3)             │   │             (TypeSafe Jev, Laya)                       │
├────────────────────────────────────────────────────────┤   ├────────────────────────────────────────────────────────┤
│ • Autoregressive token generation                      │   │ • Single forward pass (zero token sampling loop)       │
│ • Latency: 800ms – 5,000ms+                            │   │ • Latency: 15ms – 35ms P50                             │
│ • Output: Open-ended prose, narrative summaries        │   │ • Output: Calibrated probabilities over typed schemas   │
│ • Cost: Billed per input + output token                │   │ • Cost: Output tokens are free                         │
│ • Role: Planning, complex coding, deep reasoning       │   │ • Role: Instant reflex judgments, safety, routing      │
└────────────────────────────────────────────────────────┘   └────────────────────────────────────────────────────────┘
```

A **System One model** answers questions about state in **one single forward pass**. There are no tokens generated, no sampling loops, and no markdown prose to parse with regex. You supply an input state and a dictionary of questions; the neural network returns calibrated probability distributions for all questions simultaneously.

### The Missing Layer: Reflexes Need Guardrails

Just as the human brain relies on reflexive System 1 reactions checked by executive control, AI agent architectures need:
1. **Fast, low-latency reflex evaluation** (provided by Jev).
2. **Deterministic, tamper-evident governance and guardrails** (provided by JEV Fuse).

JEV Fuse bridges the gap between raw neural probabilities and deterministic operational policy.

---

## ⚙️ Configuration & Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TYPESAFE_API_KEY` | *(None)* | Your TypeSafe Jev API key (obtained from [console.typesafe.ai](https://console.typesafe.ai)) |
| `TYPESAFE_BASE_URL` | `https://api.typesafe.ai` | Upstream TypeSafe Jev service base URL |
| `JEVFUSE_PORT` | `8000` | Port to bind the FastAPI gateway |
| `JEVFUSE_HOST` | `127.0.0.1` | Network interface to bind (`127.0.0.1` for loopback, `0.0.0.0` for containers) |
| `JEVFUSE_DB_PATH` | `jevfuse_decisions.db` | Path to persistent SQLite WAL decision log |
| `JEVFUSE_CACHE_DB_PATH` | `jevfuse_cache.db` | Path to persistent SQLite WAL cache database |

---

## 📦 Installation Options

### Using `uv` (Recommended)
```bash
# Run CLI directly
uv run jevfuse --help

# Install into active virtual environment
uv pip install jev-fuse
```

### Using `pip`
```bash
pip install jev-fuse
```

### From Source
```bash
git clone https://github.com/0xshikhar/jev-fuse.git
cd jev-fuse
uv sync
uv run pytest
```

---

## 🧪 Testing

JEV Fuse includes an end-to-end test suite verifying the official `typesafe-sdk`, singleflight coalescing, SQLite WAL persistence, CLI guards, and Claude Code hooks:

```bash
uv run pytest
```

---

## 📄 License

Apache-2.0. See [LICENSE](LICENSE) for details.
