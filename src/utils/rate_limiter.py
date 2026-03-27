"""
Token bucket rate limiter for PageKeeper.

Thread-safe per-IP rate limiting. Each IP gets a bucket with configurable
capacity that refills at a steady rate. Requests consume tokens; excess
requests are rejected.
"""

import threading
import time


class TokenBucketRateLimiter:
    DEFAULT_CAPACITY = 30
    DEFAULT_REFILL_RATE = 2.0
    AUTH_TOKEN_COST = 5
    STALE_SECONDS = 300

    def __init__(self, capacity: int = None, refill_rate: float = None):
        self._capacity = capacity or self.DEFAULT_CAPACITY
        self._refill_rate = refill_rate or self.DEFAULT_REFILL_RATE
        self._store: dict[str, dict] = {}
        self._lock = threading.Lock()

    def check(self, ip: str, cost: int = 1) -> bool:
        """Consume `cost` tokens for `ip`. Returns True if allowed."""
        now = time.time()
        with self._lock:
            bucket = self._store.get(ip)
            if bucket is None:
                bucket = {"tokens": self._capacity, "last": now}
                self._store[ip] = bucket

            elapsed = now - bucket["last"]
            bucket["tokens"] = min(self._capacity, bucket["tokens"] + elapsed * self._refill_rate)
            bucket["last"] = now

            if bucket["tokens"] >= cost:
                bucket["tokens"] -= cost
                return True
            return False

    def prune(self, max_idle_seconds: int = None) -> None:
        """Remove entries idle for more than max_idle_seconds."""
        threshold = max_idle_seconds if max_idle_seconds is not None else self.STALE_SECONDS
        now = time.time()
        with self._lock:
            stale = [ip for ip, b in self._store.items() if now - b["last"] > threshold]
            for ip in stale:
                del self._store[ip]

    def clear(self) -> None:
        """Clear all buckets (useful for testing)."""
        with self._lock:
            self._store.clear()
