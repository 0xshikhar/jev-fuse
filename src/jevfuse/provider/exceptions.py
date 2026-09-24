"""Provider-specific exceptions and error conversions."""

from jevfuse.schema.decision import Action
from jevfuse.schema.errors import ErrorCode, JevFuseError


class ProviderError(Exception):
    """Base exception for provider inference and communication failures."""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode = "internal_error",
        trace_id: str | None = None,
        suggested_action: Action = Action.ASK,
        status_code: int | None = None,
        raw_body: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.trace_id = trace_id
        self.suggested_action = suggested_action
        self.status_code = status_code
        self.raw_body = raw_body
        self.headers = headers or {}

    def to_jevfuse_error(self) -> JevFuseError:
        return JevFuseError(
            error_code=self.error_code,
            message=self.message,
            trace_id=self.trace_id,
            suggested_action=self.suggested_action,
        )


class ProviderAuthenticationError(ProviderError):
    """Raised when provider credentials (API keys) are missing or rejected."""

    def __init__(
        self,
        message: str,
        trace_id: str | None = None,
        status_code: int = 401,
        raw_body: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message=message,
            error_code="provider_unavailable",
            trace_id=trace_id,
            suggested_action=Action.ASK,
            status_code=status_code,
            raw_body=raw_body,
            headers=headers,
        )


class ProviderTimeoutError(ProviderError):
    """Raised when a provider fails to respond within the configured deadline."""

    def __init__(self, message: str, trace_id: str | None = None):
        super().__init__(
            message=message,
            error_code="provider_timeout",
            trace_id=trace_id,
            suggested_action=Action.ASK,
            status_code=504,
        )


class RateLimitExceededError(ProviderError):
    """Raised when a provider rejects requests due to rate limits (HTTP 429)."""

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        trace_id: str | None = None,
        status_code: int = 429,
        raw_body: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message=message,
            error_code="rate_limited",
            trace_id=trace_id,
            suggested_action=Action.ASK,
            status_code=status_code,
            raw_body=raw_body,
            headers=headers,
        )
        self.retry_after = retry_after


class ProviderUnavailableError(ProviderError):
    """Raised when a provider endpoint or backend cannot be reached."""

    def __init__(
        self,
        message: str,
        trace_id: str | None = None,
        status_code: int = 503,
        raw_body: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message=message,
            error_code="provider_unavailable",
            trace_id=trace_id,
            suggested_action=Action.ASK,
            status_code=status_code,
            raw_body=raw_body,
            headers=headers,
        )

