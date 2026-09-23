"""Deterministic canonical request hashing for idempotency and cache keys."""

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from typing import Any

from jevfuse.schema.internal import NormalizedRequest


def canonicalize_text(text: str) -> str:
    """Normalize Unicode (NFC), convert Windows line endings, and strip outer whitespace."""
    normalized = unicodedata.normalize("NFC", text)
    return normalized.replace("\r\n", "\n").strip()


def compute_input_hash(
    task: str,
    input_text: str,
    choices: Sequence[str] | None = None,
    context: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> str:
    """
    Compute a deterministic SHA-256 fingerprint for a decision input.
    
    Hash structure:
        sha256(canonical_task || \x00 || canonical_input || \x00 || canonical_choices || \x00 || idempotency_key)
    """
    if idempotency_key:
        # If client explicitly specifies an idempotency key, combine task with idempotency key
        hasher = hashlib.sha256()
        hasher.update(task.strip().lower().encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(idempotency_key.strip().encode("utf-8"))
        return hasher.hexdigest()

    hasher = hashlib.sha256()
    hasher.update(task.strip().lower().encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(canonicalize_text(input_text).encode("utf-8"))
    hasher.update(b"\x00")

    if choices is not None:
        canonical_choices = [canonicalize_text(c) for c in choices]
        choices_bytes = json.dumps(canonical_choices, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        hasher.update(choices_bytes)
    else:
        hasher.update(b"__NONE__")

    return hasher.hexdigest()


def hash_normalized_request(req: NormalizedRequest) -> str:
    """Compute the canonical input hash directly from a NormalizedRequest."""
    return compute_input_hash(
        task=req.task,
        input_text=req.input,
        choices=req.choices,
    )


def compute_systemone_hash(model: str, state: Any, questions: dict[str, Any]) -> str:
    """
    Compute a deterministic SHA-256 fingerprint for a SystemOne request.
    
    Hash structure:
        sha256(model || \x00 || canonical_state || \x00 || canonical_questions)
    """
    hasher = hashlib.sha256()
    hasher.update(model.strip().lower().encode("utf-8"))
    hasher.update(b"\x00")

    # Canonicalize state
    if isinstance(state, str):
        state_bytes = canonicalize_text(state).encode("utf-8")
    else:
        state_bytes = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hasher.update(state_bytes)
    hasher.update(b"\x00")

    # Canonicalize questions
    questions_bytes = json.dumps(questions, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hasher.update(questions_bytes)

    return hasher.hexdigest()
