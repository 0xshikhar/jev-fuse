"""Subprocess tests verifying Claude Code PreToolUse hook stdin/stdout wire contract."""

import json
import subprocess
import sys


def run_hook_subproc(payload: str) -> subprocess.CompletedProcess[str]:
    """Execute arbiter hook pre-tool-use via subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "arbiter.hook.pre_tool_use"],
        input=payload,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_hook_allows_read_only_git_status() -> None:
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short", "description": "Check tree"},
        "cwd": "/workspace",
    }
    proc = run_hook_subproc(json.dumps(event))
    assert proc.returncode == 0
    assert proc.stdout.strip()
    data = json.loads(proc.stdout)
    assert data == {"allow": True}


def test_hook_denies_destructive_rm_rf() -> None:
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /", "description": "Delete root"},
        "cwd": "/workspace",
    }
    proc = run_hook_subproc(json.dumps(event))
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "deny" in data
    assert data["deny"].startswith("JEV-Fuse:")
    assert "allow" not in data
    assert "ask" not in data


def test_hook_asks_on_secret_path() -> None:
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat ~/.ssh/id_rsa", "description": "Read private key"},
        "cwd": "/workspace",
    }
    proc = run_hook_subproc(json.dumps(event))
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "ask" in data
    assert data["ask"].startswith("JEV-Fuse:")
    assert "allow" not in data
    assert "deny" not in data


def test_hook_ignores_non_bash_tool() -> None:
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "FileEdit",
        "tool_input": {"path": "src/app.py"},
    }
    proc = run_hook_subproc(json.dumps(event))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_hook_handles_corrupt_stdin_gracefully() -> None:
    proc = run_hook_subproc("not-valid-json")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
