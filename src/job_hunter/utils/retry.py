"""Retry decorator with exponential back-off."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger("job_hunter.retry")

F = TypeVar("F", bound=Callable[..., Any])


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Retry a sync or async function with exponential back-off."""

    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                delay = base_delay
                for attempt in range(1, max_attempts + 1):
                    try:
                        return await fn(*args, **kwargs)
                    except exceptions as exc:
                        if attempt == max_attempts:
                            raise
                        logger.warning(
                            "Attempt %d/%d for %s failed: %s — retrying in %.1fs",
                            attempt, max_attempts, fn.__name__, exc, delay,
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff_factor

            return async_wrapper  # type: ignore[return-value]
        else:
            import time

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                delay = base_delay
                for attempt in range(1, max_attempts + 1):
                    try:
                        return fn(*args, **kwargs)
                    except exceptions as exc:
                        if attempt == max_attempts:
                            raise
                        logger.warning(
                            "Attempt %d/%d for %s failed: %s — retrying in %.1fs",
                            attempt, max_attempts, fn.__name__, exc, delay,
                        )
                        time.sleep(delay)
                        delay *= backoff_factor

            return sync_wrapper  # type: ignore[return-value]

    return decorator  # type: ignore[return-value]

