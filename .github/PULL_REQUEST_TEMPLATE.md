## 📌 Description

<!-- Provide a clear, concise description of the changes made and the motivation behind them. -->

Fixes #(issue)

---

## 🎯 Type of Change

Please select the options that are relevant:

- [ ] 🐛 **Bug fix** (non-breaking change fixing an issue)
- [ ] ✨ **New feature** (non-breaking change adding functionality)
- [ ] ⚡ **Performance improvement** (latency reduction, memory optimization, or caching enhancement)
- [ ] 🛡️ **Security / Safety enhancement** (guard pipeline improvements, bypass prevention)
- [ ] ♻️ **Refactor** (code cleanup or structural reorganization with no behavioral change)
- [ ] 📝 **Documentation** (updates to docs, README, or examples)
- [ ] 🧪 **Tests** (adding missing tests or updating fixtures)

---

## 🏛️ Safety & Architectural Verification

Because JEV Fuse functions as an operational safety gate and decision runtime, please verify the following:

- [ ] **Fail-Closed Verification**: Ambiguous inputs or unhandled states default to `Action.ASK` (never fail open).
- [ ] **Wire Contract Fidelity**: If modifying `POST /v1/systemone` or provider drivers, the payload strictly conforms to TypeSafe's public spec (no synthetic or fabricated data).
- [ ] **Primitive Integrity**: `Noul`, `Choice`, and `Score` outputs maintain their distinct schemas (probabilities, margins, and rubric levels are preserved).
- [ ] **Calibration Invariant**: Any model-sourced auto-approval requires an empirical calibration profile (`is_calibrated` remains `False` by default).
- [ ] **Pure Policy Evaluation**: Policy evaluation contains zero network or filesystem I/O.
- [ ] **Audit Durability**: Any new decision path durably records traces to the SQLite WAL decision log.

---

## 🧪 Testing Checklist

- [ ] Added unit or integration tests for new functionality under `tests/`.
- [ ] All existing and new tests pass locally:
  ```bash
  pytest
  ```
- [ ] Code has been formatted and linted with `ruff`:
  ```bash
  ruff check .
  ruff format --check .
  ```
- [ ] If changing `guard`: Added test cases to the golden command dataset testing both positive (safe) and adversarial/bypass vectors.

---

## 📸 Screenshots or Trace Examples (Optional)

<!-- If applicable, paste relevant dashboard screenshots, trace inspect JSON, or CLI terminal output demonstrating the change. -->
