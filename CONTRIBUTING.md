# Contributing to JEV Fuse

Thank you for your interest in contributing to **JEV Fuse**!

JEV Fuse is the open governance runtime, safety gate, and audit plane for TypeSafe Jev and typed-decision models. Because JEV Fuse is trusted to sit as an enforcement gate in front of autonomous coding agents and terminal shells, we hold our codebase to rigorous standards of correctness, deterministic evaluation, and security.

This guide outlines our development workflow, architectural invariants, code standards, and PR process.

---

## 🏛️ Core Architectural Invariants

Before writing code, please read [doc/ARCHITECTURE.md](file:///Users/shikharsingh/Downloads/code/ai-work/arbiter/doc/ARCHITECTURE.md) and [doc/DECISIONS.md](file:///Users/shikharsingh/Downloads/code/ai-work/arbiter/doc/DECISIONS.md). Every PR must uphold these core invariants:

1. **Correctness Before Speed**: A wrong `ALLOW` is far worse than a slow `ASK`. When in doubt or when evaluating ambiguous signals, the system must fail closed toward `action: ask`.
2. **The Wire Contract is `systemone`**: The canonical wire interface is `POST /v1/systemone` matching TypeSafe's public specification. Never fabricate proxy data or synthesize fake distributions.
3. **Decoupled Primitives**: `Noul` (binary probability), `Choice` (categorical distribution + runner-up margin), and `Score` (rubric position + distribution) are distinct mathematical objects. Never collapse them into a generic scalar confidence.
4. **`is_calibrated` Defaults to `False`**: Model-sourced actions cannot auto-approve (`allow`) until a task has an empirically fitted calibration curve or threshold. Deterministic allowlist hits are logged as `deterministic_allow`.
5. **No Network or Disk I/O Inside Policy Functions**: The policy evaluation layer must remain a pure function `(task, value, confidence, context) -> Action`.
6. **Single Source of Truth**: All evaluation metrics, audit traces, and calibration routines read directly from the durable SQLite WAL Decision Log.

---

## 🛠️ Development Setup

JEV Fuse requires **Python 3.12+**. We recommend using [`uv`](https://github.com/astral-sh/uv) for fast, reproducible dependency management.

### 1. Clone the Repository
```bash
git clone https://github.com/0xshikhar/jev-fuse.git
cd jev-fuse
```

### 2. Set Up Virtual Environment & Dependencies
```bash
# Using uv (recommended)
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Or using standard pip
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Verify Local Installation
```bash
# Run test suite
pytest

# Test the CLI tool
jevfuse --help
```

---

## 🧪 Testing Guidelines

We require comprehensive test coverage for all new features, bug fixes, and recipes.

```bash
# Run all tests
pytest

# Run tests with coverage report
pytest --cov=jevfuse --cov-report=term-missing

# Run a specific test module
pytest tests/test_guard.py -v
```

### Writing Tests
- **Contract Tests**: Place wire compatibility fixtures in `tests/fixtures/`. Ensure any mock responses reflect real TypeSafe JSON responses.
- **Guard Tests**: If you modify `src/jevfuse/guard/`, you must add test cases to the golden set covering both bypass vectors (`find -exec`, backticks, compound `&&`, subshells) and read-only commands (`git status`, `pwd`).
- **Deterministic Replay**: Ensure test cases do not depend on external live networks. Set `TYPESAFE_API_KEY="test-mock-key"` in tests and mock upstream HTTP calls.

---

## 🎨 Code Style & Quality Standards

We use [`ruff`](https://github.com/astral-sh/ruff) for rapid linting and formatting.

```bash
# Check formatting and lint rules
ruff check .

# Automatically fix lint issues
ruff check --fix .

# Format code
ruff format .
```

- **Type Annotations**: All public functions, methods, and classes must include strict type annotations.
- **Pydantic Schemas**: All data exchanged across module boundaries or network endpoints must use Pydantic v2 models.
- **Docstrings**: Public APIs and modules require Google-style or PEP 257 docstrings explaining parameters, return values, and failure modes.

---

## 🔀 Pull Request Process

### 1. Create a Topic Branch
Branch names should follow standard naming conventions:
- `feat/feature-name` (e.g. `feat/speculative-fanout-prune`)
- `fix/bug-description` (e.g. `fix/shell-lexer-subshell-edgecase`)
- `docs/doc-update` (e.g. `docs/update-quickstart-guide`)
- `refactor/component-name`

### 2. Follow Conventional Commits
Write clear, descriptive commit messages following the [Conventional Commits](https://www.conventionalcommits.org/) specification:
```
feat(guard): add token detection for command substitution in shell lexer
fix(cache): include template version and model string in cache hash
docs(readme): add cli commands reference and dashboard walkthrough
test(systemone): add contract test fixtures for choice runner-up margins
```

### 3. Open a Pull Request
When submitting your PR, use the default GitHub Pull Request Template:
- Provide a concise summary of the problem and the solution.
- Link any related GitHub issues (`Fixes #123`).
- Confirm all tests pass locally (`pytest`).
- Check that no performance or security regressions are introduced to the 4-tier guard or latency budget.

---

## 🔒 Security Vulnerability Reporting

If you discover a security vulnerability or a safety bypass in JEV Fuse's shell guard, **do not open a public GitHub issue**. Please review [SECURITY.md](file:///Users/shikharsingh/Downloads/code/ai-work/arbiter/SECURITY.md) and report it privately to our security team.

---

## 💬 Community & Questions

- **GitHub Discussions**: For feature proposals, architectural ideas, and general Q&A.
- **GitHub Issues**: For verified bug reports and concrete roadmap tracking.
