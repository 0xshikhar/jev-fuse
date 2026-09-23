---
name: jev-fuse
license: Apache-2.0
description: >
  Governed decision runtime, policy control plane, and MCP supervisor for TypeSafe Jev
  and local typed-decision models. Use when an agent needs to evaluate safety of shell
  commands (jevfuse guard), compact conversation history without losing critical code
  (jevfuse prune), route workflows based on semantic classification, or execute fast
  System One typed decisions with declarative policy actions (allow, ask, deny, continue,
  escalate, keep, truncate, drop).
---

# JEV Fuse: Governed Decision Runtime for Coding Agents

**JEV Fuse** is an open decision runtime and policy control plane. It enables coding agents (Claude Code, Cursor, Codex, Pi, browser agents) to execute fast, bounded semantic judgments and map probabilities into deterministic, governed actions using TypeSafe Jev or local Laya models.

## 1. Installation

### In Claude Code
Run in your terminal:
```bash
claude plugin marketplace add 0xshikhar/jev-fuse
claude plugin install jev-fuse@0xshikhar
```

### Via skills.sh / Other Agents
Run in your project root:
```bash
npx skills add 0xshikhar/jev-fuse --skill jev-fuse
```

### As an MCP Server
Add to your agent's MCP configuration (`claude_desktop_config.json` or `.cursor/mcp.json`):
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

---

## 2. When to Use JEV Fuse in Agent Workflows

Use JEV Fuse instead of full LLM generation when you need **fast (<30ms), deterministic, typed verdicts**:

1. **Before Executing Shell Commands (`jevfuse guard`)**:
   - Don't ask the human user every time you run read-only or standard dev commands (`git status`, `ls`, `pytest`, `cargo test`, `cat`).
   - Call JEV Fuse's guard gate to verify command safety. Auto-execute if `action: "allow"`. Only interrupt the user if `action: "ask"` or `action: "deny"`.

2. **Before Context Compaction (`jevfuse prune`)**:
   - When conversation history approaches token limits, don't generate a vague narrative summary.
   - Use JEV Fuse to score each turn against the active user goal. Keep vital code diffs and instructions verbatim, truncate large tool outputs, and drop stale queries.

3. **Routing and Tool Dispatch**:
   - Classify user intents into typed enum options with calibrated confidence distributions.

---

## 3. TypeSafe Question Design

JEV-Fuse models questions in three canonical types, fully compatible with TypeSafe's System One specification:

### A. Binary Probability (`noul`)
Use when evaluating whether a specific statement holds true:
```json
{
  "state": "git push --force origin main",
  "questions": {
    "is_destructive": {
      "type": "noul",
      "instructions": "This command permanently alters or deletes remote repository history."
    }
  }
}
```
Returns a calibrated probability between 0.0 and 1.0.

### B. Categorical Routing (`choice`)
Use when selecting among bounded mutually exclusive options:
```json
{
  "state": "The test failed with connection refused on 127.0.0.1:5432",
  "questions": {
    "root_cause": {
      "type": "choice",
      "instructions": "What is the category of this test failure?",
      "criteria": {
        "database": "PostgreSQL service is not running or unreachable",
        "assertion": "Expected value does not match actual test output",
        "syntax": "Compilation or import syntax failure",
        "network": "External third-party API timeout"
      }
    }
  }
}
```

### C. Ordinal Risk or Urgency (`score`)
Use for continuous or ranked assessment across ordered levels (2 to 10):
```json
{
  "state": "rm -rf build/ dist/",
  "questions": {
    "risk_level": {
      "type": "score",
      "instructions": "Evaluate the irreversible risk of this command.",
      "criteria": ["safe read-only", "minor local modification", "destructive directory wipe", "critical system risk"]
    }
  }
}
```

---

## 4. Policy Engine Actions

Unlike raw model APIs that leave probability thresholds to the caller, JEV-Fuse evaluates declarative YAML rules and returns unambiguous actions:

| Action | Meaning for Agent |
|---|---|
| `allow` | Safe to proceed immediately without human intervention. |
| `ask` | Ambiguous or medium risk. Pause and request human user approval. |
| `deny` | Dangerous or policy violation. Block execution immediately. |
| `keep` | Maintain this conversation turn verbatim in context. |
| `truncate` | Shorten this tool output or log to its first/last 10 lines. |
| `drop` | Completely purge this turn from active prompt context. |

---

## 5. Local vs Hosted Execution

JEV-Fuse operates transparently across two providers:
- **Hosted Jev (`--provider jev`)**: Fastest setup (<30s), uses `TYPESAFE_API_KEY`.
- **Local Laya (`--provider laya`)**: Zero egress, runs entirely locally on Apple Silicon (MLX/MPS) or NVIDIA GPUs (CUDA).
