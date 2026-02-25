from __future__ import annotations

import asyncio
from dataclasses import dataclass
from random import uniform
from typing import Awaitable, Callable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    backoff_factor: float = 2.0
    jitter: bool = False
    retry_exceptions: tuple[type[Exception], ...] = (Exception,)


class AsyncRetriesService:
    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        policy: RetryPolicy,
        should_retry: Callable[[Exception], bool] | None = None,
        on_retry: Callable[[Exception, int, float], None] | None = None,
    ) -> T:
        if policy.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if policy.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be > 0")
        if policy.max_delay_seconds <= 0:
            raise ValueError("max_delay_seconds must be > 0")
        if policy.backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0")

        last_error: Exception | None = None
        for attempt in range(1, policy.max_attempts + 1):
            try:
                return await operation()
            except Exception as exc:
                if not isinstance(exc, policy.retry_exceptions):
                    raise
                if should_retry is not None and not should_retry(exc):
                    raise
                if attempt >= policy.max_attempts:
                    raise
                last_error = exc
                delay = self._delay_for_attempt(policy=policy, attempt=attempt)
                if on_retry is not None:
                    on_retry(exc, attempt, delay)
                await asyncio.sleep(delay)

        assert last_error is not None
        raise last_error

    @staticmethod
    def _delay_for_attempt(*, policy: RetryPolicy, attempt: int) -> float:
        delay = min(policy.max_delay_seconds, policy.base_delay_seconds * (policy.backoff_factor ** (attempt - 1)))
        if policy.jitter:
            return uniform(delay * 0.5, delay)
        return delay
