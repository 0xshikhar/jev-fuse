---
name: 🐛 Bug report
about: Create a report to help us improve JEV Fuse
title: '[BUG] '
labels: ['bug']
assignees: ''
---

**Describe the Bug**
A clear and concise description of what the bug is.

**Component Affected**
- [ ] 🛡️ Shell Guard / AST Classifier (`jevfuse guard` / `PreToolUse` hook)
- [ ] 🔄 Reverse Proxy / Drop-in API (`POST /v1/systemone`)
- [ ] ⚡ Caching & Singleflight Concurrency
- [ ] 📜 SQLite WAL Audit Log / DuckDB Analytics
- [ ] 🖥️ Web Control Plane Dashboard (`/dashboard`)
- [ ] 🔌 MCP Server (`jevfuse mcp`)
- [ ] 📦 Python SDK / Engine (`import jevfuse`)

**To Reproduce**
Steps to reproduce the behavior:
1. Run command '...' or send payload '...'
2. Expected output: '...'
3. Actual output / error trace: '...'

**Environment Information**
- OS: [e.g. macOS 14.5, Ubuntu 22.04]
- Python Version: [e.g. 3.12.3]
- JEV Fuse Version: [e.g. 0.1.0]
- Target Provider: [e.g. Hosted TypeSafe Jev, Local Laya, mock]

**Trace ID / Logs**
If applicable, paste the trace ID or relevant lines from `jevfuse_decisions.db` or server console output.
