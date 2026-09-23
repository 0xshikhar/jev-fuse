"""YAML policy file loader and validator."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jevfuse.policy.schema import PolicyDefinition


class PolicyLoadError(Exception):
    """Raised when a policy document fails YAML parsing or schema validation."""


def load_policy_from_dict(data: dict[str, Any]) -> PolicyDefinition:
    """Validate a dictionary against the PolicyDefinition schema."""
    try:
        return PolicyDefinition.model_validate(data)
    except ValidationError as exc:
        raise PolicyLoadError(f"Invalid policy schema: {exc}") from exc


def load_policy_from_str(yaml_content: str) -> PolicyDefinition:
    """Parse YAML string and validate into PolicyDefinition."""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise PolicyLoadError(f"YAML syntax error: {exc}") from exc

    if not isinstance(data, dict):
        raise PolicyLoadError("Policy file must contain a top-level YAML mapping/dictionary")

    return load_policy_from_dict(data)


def load_policy_from_file(file_path: str | Path) -> PolicyDefinition:
    """Read a policy YAML file from disk and return validated PolicyDefinition."""
    path = Path(file_path)
    if not path.is_file():
        raise PolicyLoadError(f"Policy file not found: {path}")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise PolicyLoadError(f"Failed to read policy file {path}: {exc}") from exc

    return load_policy_from_str(content)


def load_policies_from_dir(dir_path: str | Path) -> dict[str, PolicyDefinition]:
    """Scan directory for .yaml/.yml files and return mapping of task_name -> PolicyDefinition."""
    path = Path(dir_path)
    if not path.is_dir():
        return {}

    policies: dict[str, PolicyDefinition] = {}
    for entry in sorted(path.iterdir()):
        if entry.is_file() and entry.suffix.lower() in (".yaml", ".yml"):
            policy = load_policy_from_file(entry)
            policies[policy.task] = policy

    return policies
