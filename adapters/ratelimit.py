"""Async token-bucket rate limiter with exponential-backoff retry (§39)."""
from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int | None = None):
        self.rate = max(rate_per_sec, 0.1)
        self.capacity = burst or max(1, int(rate_per_sec))
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait)


class RetryPolicy:
    def __init__(self, max_attempts: int = 4, base_delay: float = 0.5,
                 max_delay: float = 10.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def run(self, fn: Callable[[], Awaitable[T]],
                  retry_on: tuple[type[BaseException], ...]) -> T:
        attempt = 0
        while True:
            try:
                return await fn()
            except retry_on:
                attempt += 1
                if attempt >= self.max_attempts:
                    raise
                delay = min(self.max_delay,
                            self.base_delay * (2 ** (attempt - 1)))
                await asyncio.sleep(delay * (0.5 + random.random()))
