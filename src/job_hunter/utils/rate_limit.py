"""Configurable rate limiter for browser automation."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Simple token-bucket style rate limiter."""

    def __init__(self, min_delay_ms: int = 500, max_delay_ms: int = 2000) -> None:
        self.min_delay = min_delay_ms / 1000.0
        self.max_delay = max_delay_ms / 1000.0
        self._last_call = 0.0

    async def wait(self) -> None:
        """Wait until enough time has passed since the last call."""
        import random

        delay = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.monotonic() - self._last_call
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_call = time.monotonic()

