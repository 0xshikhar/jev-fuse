"""Pure-function Policy Engine evaluating decision contexts against declarative rules."""

from collections.abc import Callable

from jevfuse.policy.schema import PolicyContext, PolicyDefinition
from jevfuse.schema.decision import Action

PolicyHook = Callable[[PolicyContext], Action]


class PolicyEngine:
    """Evaluates (task, client, value, confidence, context) -> Action."""

    def __init__(self, default_unrouted_action: Action = Action.ASK):
        self._policies: dict[str, PolicyDefinition] = {}
        self._hooks: dict[str, PolicyHook] = {}
        self._default_unrouted_action = default_unrouted_action

    def register_policy(self, policy: PolicyDefinition) -> None:
        """Register or update a task's declarative YAML policy."""
        self._policies[policy.task] = policy

    def register_hook(self, task: str, hook: PolicyHook) -> None:
        """Register a pure Python policy callable as an escape hatch for complex logic."""
        self._hooks[task] = hook

    def get_policy(self, task: str) -> PolicyDefinition | None:
        """Lookup registered policy for a task."""
        return self._policies.get(task)

    def evaluate(self, ctx: PolicyContext) -> tuple[Action, str | None]:
        """
        Pure function evaluating the given context against policy rules.
        
        Returns:
            tuple[Action, str | None]: The action verdict and an explanatory reason tag.
        """
        policy = self._policies.get(ctx.task)

        # 1. Unrouted task check
        if policy is None:
            # Check if there is a custom Python hook for unrouted task
            if ctx.task in self._hooks:
                return self._hooks[ctx.task](ctx), "python_hook"
            return self._default_unrouted_action, "unrouted_task"

        # 2. Provider error safety check
        if ctx.provider_error:
            return policy.on_provider_error, f"provider_error: {ctx.provider_error}"

        # 3. Calibration health check (stale or missing labels force abstention)
        if not ctx.is_calibrated:
            return policy.on_stale_calibration, "stale_calibration"

        # 4. Python hook escape hatch (if registered, takes precedence)
        if ctx.task in self._hooks:
            return self._hooks[ctx.task](ctx), "python_hook"

        # 5. Client overrides evaluation
        if ctx.client_id in policy.client_overrides:
            override = policy.client_overrides[ctx.client_id]
            for idx, rule in enumerate(override.rules):
                if rule.when.matches(ctx.value, ctx.confidence, ctx.context):
                    return rule.then, f"client_override:{ctx.client_id}:rule_{idx}"
            if override.default_action is not None:
                return override.default_action, f"client_default:{ctx.client_id}"

        # 6. Task-level declarative rules (first match wins)
        for idx, rule in enumerate(policy.rules):
            if rule.when.matches(ctx.value, ctx.confidence, ctx.context):
                return rule.then, f"rule_match:{idx}"

        # 7. Fallback to policy default_action
        return policy.default_action, "default_action"
