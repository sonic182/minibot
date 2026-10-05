from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_HTTP_ERROR_PATTERN = re.compile(r"^HTTP (\d{3}): (.*)$", re.DOTALL)
_QUOTA_STATUSES = {402, 429}
_QUOTA_CODES = {"permission-denied", "insufficient_quota"}


class ProviderHTTPError(Exception):
    """Raised when the LLM provider's HTTP layer returns an error response.

    ``llm_async`` raises a bare ``Exception(f"HTTP {status}: {body}")`` with no structured
    attributes; ``wrap_provider_exception`` turns that into one of these at the LLM-client
    boundary so callers can branch on typed fields instead of re-parsing the message.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")

    @property
    def quota_detail(self) -> str | None:
        """Return the provider's billing/quota detail text, or None if this isn't one."""
        try:
            parsed = json.loads(self.body)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(parsed, dict):
            return None
        error = parsed.get("error")
        if isinstance(error, dict):
            detail = error.get("message")
            code = error.get("code") or parsed.get("code")
        else:
            detail = error
            code = parsed.get("code")
        if self.status_code not in _QUOTA_STATUSES and code not in _QUOTA_CODES:
            return None
        text = str(detail or code or "").strip().replace("\n", " ")
        if len(text) > 200:
            text = f"{text[:200]}..."
        return text or f"HTTP {self.status_code}"


def wrap_provider_exception(exc: Exception) -> Exception:
    """Return a `ProviderHTTPError` when `exc` matches llm_async's bare HTTP-error format.

    Returns `exc` unchanged (same object) otherwise, so callers can tell whether wrapping
    happened via identity and re-raise the original exception/traceback when it didn't.
    """
    match = _HTTP_ERROR_PATTERN.match(str(exc).strip())
    if not match:
        return exc
    return ProviderHTTPError(int(match.group(1)), match.group(2))


_NON_RETRYABLE_RESPONSE_CODES = {"context_length_exceeded", "invalid_prompt"}
_NON_RETRYABLE_RESPONSE_TYPES = {"invalid_request_error"}


class ProviderResponseError(Exception):
    """Raised when the provider accepted the request but its response reports a failure.

    Covers Responses API streams that end in ``response.failed`` or an ``error`` event. Fields come
    from the provider's structured ``status`` and ``error`` payload.
    """

    def __init__(
        self,
        *,
        status: str | None,
        code: str | None = None,
        error_type: str | None = None,
        message: str | None = None,
        response_id: str | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.error_type = error_type
        self.message = message
        self.response_id = response_id
        super().__init__(f"provider response {status or 'unknown'}: {code or error_type or message or 'no detail'}")

    @property
    def retryable(self) -> bool:
        return self.code not in _NON_RETRYABLE_RESPONSE_CODES and self.error_type not in _NON_RETRYABLE_RESPONSE_TYPES

    @classmethod
    def from_payload(cls, original: Mapping[str, Any]) -> ProviderResponseError:
        error = original.get("error")
        error = error if isinstance(error, Mapping) else {}
        return cls(
            status=_opt_str(original.get("status")),
            code=_opt_str(error.get("code")),
            error_type=_opt_str(error.get("type")),
            message=_opt_str(error.get("message")),
            response_id=_opt_str(original.get("id")),
        )


class EmptyProviderResponseError(ProviderResponseError):
    """Raised when the provider finished without any text or tool calls."""

    response: Any = None

    @property
    def retryable(self) -> bool:
        return True


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
