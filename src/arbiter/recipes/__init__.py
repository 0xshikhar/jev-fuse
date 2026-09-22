"""Showcase recipes and high-leverage agent workflows for Arbiter."""

from arbiter.recipes.guard import GuardVerdict, evaluate_command
from arbiter.recipes.prune import compact_context

__all__ = ["evaluate_command", "GuardVerdict", "compact_context"]
