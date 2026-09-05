from __future__ import annotations

import json
import re

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
