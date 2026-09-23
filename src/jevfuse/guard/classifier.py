"""Deterministic AST & grammar-based shell command classifier with Jev SystemOne fallback.

Implements the unified Phase 8 safety evaluation order:
1. Shell tokenizer and structure check (unparseable, command substitutions, redirects -> ask/deny)
2. Deterministic denylist on parsed argv (recursive rm, force push, find -exec, pipe-to-sh, sudo)
3. Deterministic read-only allowlist (git status, diff, log, show, ls, pwd)
4. Split-noul SystemOne fallback (destroys_data, destroys_history, exposes_secrets, compromises_security)
   with 2s timeout and fail-closed uncalibrated abstention.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Any

from jevfuse.schema.decision import Action


@dataclass(frozen=True)
class GuardResult:
    action: Action
    confidence: float
    reason: str
    command: str
    is_destructive: bool = False

    def to_claude_hook_result(self) -> dict[str, Any]:
        """Convert to official Claude Code PreToolUseResult shape (2.1.274)."""
        if self.action == Action.ALLOW:
            return {"allow": True}
        elif self.action == Action.DENY:
            return {"deny": f"JEV-Fuse: {self.reason}"}
        else:
            return {"ask": f"JEV-Fuse: {self.reason}"}


# ---------------------------------------------------------------------------
# Patterns & Lexical Rules
# ---------------------------------------------------------------------------

SECRET_PATTERNS = re.compile(
    r"(~|\$HOME)?/?\.(ssh|aws|gnupg|azure|kube|docker|netrc|npmrc)\b"
    r"|\bid_(rsa|ed25519|ecdsa)\b|(^|[\s/=\"'])\.env\b|\bcredentials\b|\bkeychain\b"
    r"|\b[A-Z_]*(TOKEN|SECRET|PASSWORD|API_KEY)\b|\.(pem|p12|pfx)\b",
    re.IGNORECASE,
)

SYSTEM_PATH_PATTERNS = re.compile(
    r"(^|[\s/])(etc|usr|var|dev|boot|opt|System|Library|private)(/|$)",
    re.IGNORECASE,
)

PIPE_TO_SHELL_PATTERNS = re.compile(
    r"\|\s*(sudo\s+)?(sh|bash|zsh|dash|ksh|python\d?|perl|ruby|node)(\s+.*)?$",
    re.IGNORECASE,
)

FORK_BOMB_PATTERN = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")

COMMAND_SUBSTITUTION_PATTERN = re.compile(r"\$\(.*?\)|\`.*?\`")

REDIRECT_PATTERN = re.compile(r"(>|>>|>\||>&)")


# Day-one strict read-only allowlist
READ_ONLY_COMMANDS = {
    "pwd",
}

READ_ONLY_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "rev-parse",
    "describe",
    "blame",
    "shortlog",
    "ls-files",
    "remote",
}

GUARD_QUESTIONS = {
    "destroys_data": {
        "type": "noul",
        "instructions": "This command deletes or overwrites something that cannot be brought back.",
    },
    "destroys_history": {
        "type": "noul",
        "instructions": "This command rewrites, deletes, or truncates repository commit history.",
    },
    "exposes_secrets": {
        "type": "noul",
        "instructions": "This command reads, prints, or transmits private keys, secrets, or credentials.",
    },
    "compromises_security": {
        "type": "noul",
        "instructions": "This command modifies system security settings, permissions, or system binaries.",
    },
}


# ---------------------------------------------------------------------------
# Lexer & Pipeline Splitter
# ---------------------------------------------------------------------------

def split_into_pipelines(command: str) -> list[list[list[str]]]:
    """
    Split command by compound control operators (&&, ||, ;, &),
    then by pipe (|), returning a list of pipelines.
    Structure: list of compound commands -> list of pipe stages -> list of argv tokens.
    Raises ValueError on unparseable / unterminated quotes.
    """
    lexer = shlex.shlex(command, punctuation_chars="|&;()")
    lexer.wordchars += ":/.-_~=+@%^*[]{},"
    lexer.whitespace_split = True
    raw_tokens = list(lexer)

    # Group by compound control operators: &&, ||, ;, &
    compound_commands: list[list[str]] = []
    current_cmd: list[str] = []

    for tok in raw_tokens:
        if tok in ("&&", "||", ";", "&"):
            if current_cmd:
                compound_commands.append(current_cmd)
                current_cmd = []
        else:
            current_cmd.append(tok)
    if current_cmd:
        compound_commands.append(current_cmd)

    # For each compound command, split by pipe |
    result: list[list[list[str]]] = []
    for cmd_tokens in compound_commands:
        pipeline: list[list[str]] = []
        current_stage: list[str] = []
        for tok in cmd_tokens:
            if tok == "|":
                if current_stage:
                    pipeline.append(current_stage)
                    current_stage = []
            else:
                # Strip wrapping quotes if preserved
                unquoted = tok.strip("\"'") if (tok.startswith(("\"", "'")) and tok.endswith(("\"", "'")) and len(tok) >= 2) else tok
                current_stage.append(unquoted)
        if current_stage:
            pipeline.append(current_stage)
        if pipeline:
            result.append(pipeline)

    return result


# ---------------------------------------------------------------------------
# 4-Layer Evaluator
# ---------------------------------------------------------------------------

async def evaluate_shell_command(
    command: str,
    cwd: str = "",
    engine: Any | None = None,
    allow_tests: bool = False,
) -> GuardResult:
    """Evaluate whether a shell command is safe to execute automatically.
    
    Unified 4-layer evaluation:
    1. Shell Tokenizer & Structure Check (unparseable, command substitutions, redirects -> ask/deny)
    2. Deterministic Denylist on parsed argv (recursive rm, force push, find -exec, pipe-to-sh, sudo)
    3. Deterministic Read-Only Allowlist (git status, diff, log, show, ls, pwd)
    4. Split-Noul SystemOne Fallback with 2s timeout and uncalibrated abstention.
    """
    cleaned = command.strip()
    if not cleaned:
        return GuardResult(
            action=Action.ALLOW,
            confidence=1.0,
            reason="Empty command",
            command=command,
        )

    # -----------------------------------------------------------------------
    # Layer 1: Shell Tokenizer & Structural Inspection
    # -----------------------------------------------------------------------

    # Check for fork bomb immediately
    if FORK_BOMB_PATTERN.search(cleaned):
        return GuardResult(
            action=Action.DENY,
            confidence=1.0,
            reason="Fork bomb detected",
            command=cleaned,
            is_destructive=True,
        )

    # Check for command substitutions $(...) or `...`
    if COMMAND_SUBSTITUTION_PATTERN.search(cleaned):
        # Inspect what is inside the substitution
        sub_match = COMMAND_SUBSTITUTION_PATTERN.search(cleaned)
        sub_inner = sub_match.group(0).strip("$()` ") if sub_match else ""
        if any(bad in sub_inner for bad in ("rm", "mkfs", "dd", "sudo", "git push")):
            return GuardResult(
                action=Action.DENY,
                confidence=1.0,
                reason=f"Dangerous command substitution detected: {sub_match.group(0)}",
                command=cleaned,
                is_destructive=True,
            )
        return GuardResult(
            action=Action.ASK,
            confidence=0.5,
            reason=f"Command substitution detected ({sub_match.group(0)}); user confirmation required.",
            command=cleaned,
        )

    # Check for pipe to shell in raw command
    if PIPE_TO_SHELL_PATTERNS.search(cleaned):
        return GuardResult(
            action=Action.DENY,
            confidence=1.0,
            reason="Pipe-to-shell detected (curl/wget/cat piped directly into shell interpreter)",
            command=cleaned,
            is_destructive=True,
        )

    # Attempt shell parsing
    try:
        pipelines = split_into_pipelines(cleaned)
    except Exception as exc:
        return GuardResult(
            action=Action.ASK,
            confidence=0.5,
            reason=f"Unparseable shell command syntax ({exc}); asking confirmation.",
            command=cleaned,
        )

    if not pipelines:
        return GuardResult(
            action=Action.ALLOW,
            confidence=1.0,
            reason="Empty parsed command",
            command=cleaned,
        )

    # -----------------------------------------------------------------------
    # Layer 2: Deterministic Denylist on Parsed Argv & Secret Redirection
    # -----------------------------------------------------------------------

    for pipeline in pipelines:
        for stage in pipeline:
            if not stage:
                continue

            verb = os.path.basename(stage[0]).lower()
            args = stage[1:]

            # 1. sudo / doas / su
            if verb in ("sudo", "doas", "su"):
                return GuardResult(
                    action=Action.DENY,
                    confidence=1.0,
                    reason=f"Privilege escalation verb '{verb}' denied without explicit authorization",
                    command=cleaned,
                    is_destructive=True,
                )

            # 2. find -exec / find -delete
            if verb == "find":
                for arg in args:
                    if arg in ("-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprint", "-fls"):
                        return GuardResult(
                            action=Action.DENY,
                            confidence=1.0,
                            reason=f"find command with '{arg}' executes arbitrary subcommands",
                            command=cleaned,
                            is_destructive=True,
                        )

            # 3. Recursive delete: rm with -r / -R / --recursive targeting dangerous locations
            if verb in ("rm", "unlink"):
                is_recursive = any(
                    a in ("-r", "-R", "--recursive") or (a.startswith("-") and ("r" in a or "R" in a))
                    for a in args
                )
                targets = [a for a in args if not a.startswith("-")]
                is_no_preserve = "--no-preserve-root" in args
                if is_no_preserve:
                    return GuardResult(
                        action=Action.DENY,
                        confidence=1.0,
                        reason="rm with --no-preserve-root denied",
                        command=cleaned,
                        is_destructive=True,
                    )
                if is_recursive:
                    for tgt in targets:
                        normalized = tgt.strip().rstrip("/")
                        if normalized in ("", "/", "~", "$HOME", ".", "..", "*", "/*", "~/*"):
                            return GuardResult(
                                action=Action.DENY,
                                confidence=1.0,
                                reason=f"Recursive delete targeting dangerous root or home directory '{tgt}'",
                                command=cleaned,
                                is_destructive=True,
                            )
                    # Any recursive rm without specific safe local targets is denied
                    if not targets or any(t.startswith(("/", "~", "$HOME")) for t in targets):
                        return GuardResult(
                            action=Action.DENY,
                            confidence=1.0,
                            reason="Recursive delete outside project directory",
                            command=cleaned,
                            is_destructive=True,
                        )

            # 4. git push -f / --force / --force-with-lease
            if verb == "git" and args:
                git_sub = args[0].lower()
                if git_sub == "push":
                    has_force = any(a in ("-f", "--force", "--force-with-lease") for a in args[1:])
                    if has_force:
                        return GuardResult(
                            action=Action.DENY,
                            confidence=1.0,
                            reason="git push with force flag destroys repository history",
                            command=cleaned,
                            is_destructive=True,
                        )

            # 5. Disk and device writes
            if verb in ("dd", "mkfs", "fdisk", "parted", "gdisk"):
                if verb == "dd" and any(a.startswith("of=/dev/") for a in args):
                    return GuardResult(
                        action=Action.DENY,
                        confidence=1.0,
                        reason="dd writing directly to raw block device",
                        command=cleaned,
                        is_destructive=True,
                    )
                if verb.startswith("mkfs"):
                    return GuardResult(
                        action=Action.DENY,
                        confidence=1.0,
                        reason="Filesystem creation tool mkfs denied",
                        command=cleaned,
                        is_destructive=True,
                    )

            # 6. Redirection to secrets or system paths
            for i, token in enumerate(stage):
                if token in (">", ">>", ">|", ">&"):
                    if i + 1 < len(stage):
                        target_file = stage[i + 1]
                        if SECRET_PATTERNS.search(target_file):
                            return GuardResult(
                                action=Action.DENY,
                                confidence=1.0,
                                reason=f"Redirection writing into secret path '{target_file}'",
                                command=cleaned,
                                is_destructive=True,
                            )
                        if SYSTEM_PATH_PATTERNS.search(target_file):
                            return GuardResult(
                                action=Action.DENY,
                                confidence=1.0,
                                reason=f"Redirection writing into system path '{target_file}'",
                                command=cleaned,
                                is_destructive=True,
                            )

    # -----------------------------------------------------------------------
    # Secret Path Check: Reading secrets always asks or denies (model does NOT vote)
    # -----------------------------------------------------------------------
    if SECRET_PATTERNS.search(cleaned):
        return GuardResult(
            action=Action.ASK,
            confidence=0.5,
            reason="Command touches or reads secret path; user confirmation required.",
            command=cleaned,
        )

    # Check for any redirection that wasn't already denied:
    # Any redirection (e.g. > out.txt) is not read-only -> falls through to ASK
    if REDIRECT_PATTERN.search(cleaned):
        return GuardResult(
            action=Action.ASK,
            confidence=0.5,
            reason="Shell redirection detected; user confirmation required.",
            command=cleaned,
        )

    # -----------------------------------------------------------------------
    # Layer 3: Deterministic Read-Only Allowlist (Zero-Prompt on Day One)
    # -----------------------------------------------------------------------

    def is_stage_allowlisted(stage: list[str]) -> bool:
        if not stage:
            return False
        verb = os.path.basename(stage[0]).lower()
        args = stage[1:]

        # Simple read-only utilities
        if verb in READ_ONLY_COMMANDS:
            return True

        # ls command: must not have dangerous flags
        if verb == "ls":
            return True

        # git read-only subcommands
        if verb == "git" and args:
            git_sub = args[0].lower()
            if git_sub in READ_ONLY_GIT_SUBCOMMANDS:
                # Disallow git commands with external execution flags
                disallowed_git_flags = ("--exec", "-c", "--upload-pack")
                if not any(a.startswith(disallowed_git_flags) for a in args[1:]):
                    return True

        # Opt-in test runner execution
        if allow_tests:
            if verb in ("pytest", "bun", "npm", "cargo", "go"):
                if verb == "pytest":
                    return True
                if len(args) >= 1 and args[0] == "test":
                    return True

        return False

    all_stages_allowlisted = True
    for pipeline in pipelines:
        for stage in pipeline:
            if not is_stage_allowlisted(stage):
                all_stages_allowlisted = False
                break
        if not all_stages_allowlisted:
            break

    if all_stages_allowlisted:
        return GuardResult(
            action=Action.ALLOW,
            confidence=1.0,
            reason="Standard verified read-only command",
            command=cleaned,
        )

    # -----------------------------------------------------------------------
    # Layer 4: Split-Noul SystemOne Model Fallback
    # -----------------------------------------------------------------------

    # Fast 2-second timeout client
    arbiter_url = os.environ.get("ARBITER_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("ARBITER_API_KEY")

    try:
        import httpx
        payload = {
            "model": "jev-latest",
            "state": f"Command to evaluate: {cleaned}\nWorking Directory: {cwd or '.'}",
            "questions": GUARD_QUESTIONS,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.post(f"{arbiter_url}/v1/systemone", json=payload, headers=headers)
            if resp.status_code != 200:
                return GuardResult(
                    action=Action.ASK,
                    confidence=0.5,
                    reason=f"Arbiter upstream returned HTTP {resp.status_code}; asking confirmation.",
                    command=cleaned,
                )
            data = resp.json()

        answers = data.get("answers", {})
        # Evaluate 4 split nouls
        max_harm = 0.0
        max_harm_qid = ""
        in_deadband = False

        for qid in ("destroys_data", "destroys_history", "exposes_secrets", "compromises_security"):
            ans = answers.get(qid, {})
            val = float(ans.get("noul", 0.5)) if isinstance(ans, dict) else 0.5
            if val > max_harm:
                max_harm = val
                max_harm_qid = qid
            if 0.40 <= val <= 0.60:
                in_deadband = True

        if max_harm >= 0.70:
            return GuardResult(
                action=Action.DENY,
                confidence=round(max_harm, 4),
                reason=f"High risk score ({max_harm:.2f}) on question '{max_harm_qid}'",
                command=cleaned,
                is_destructive=True,
            )

        if in_deadband:
            return GuardResult(
                action=Action.ASK,
                confidence=round(max_harm, 4),
                reason=f"Model confidence in deadband [0.40, 0.60] on question '{max_harm_qid}' ({max_harm:.2f})",
                command=cleaned,
            )

        # High confidence safe (max_harm < 0.40)
        # Because is_calibrated defaults to False, uncalibrated model verdict still asks!
        return GuardResult(
            action=Action.ASK,
            confidence=round(max_harm, 4),
            reason=f"Uncalibrated safety verdict ({max_harm:.2f}); asking confirmation until task calibration is fitted.",
            command=cleaned,
        )

    except Exception as exc:
        # Fail-closed on timeout or connection error: ask user, never crash, never emit empty stdout
        return GuardResult(
            action=Action.ASK,
            confidence=0.5,
            reason=f"Arbiter guard unreachable or timed out ({type(exc).__name__}); asking user confirmation.",
            command=cleaned,
        )
