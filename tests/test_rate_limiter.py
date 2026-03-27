"""Tests for TokenBucketRateLimiter."""

import time

from src.utils.rate_limiter import TokenBucketRateLimiter


class TestTokenBucketRateLimiter:
    def test_allows_within_capacity(self):
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=0)
        for _ in range(5):
            assert limiter.check("1.2.3.4") is True

    def test_rejects_over_capacity(self):
        limiter = TokenBucketRateLimiter(capacity=3, refill_rate=0)
        for _ in range(3):
            limiter.check("1.2.3.4")
        assert limiter.check("1.2.3.4") is False

    def test_refills_over_time(self):
        limiter = TokenBucketRateLimiter(capacity=2, refill_rate=100)
        # Exhaust tokens
        limiter.check("1.2.3.4")
        limiter.check("1.2.3.4")
        assert limiter.check("1.2.3.4") is False
        # Sleep briefly — high refill rate means quick recovery
        time.sleep(0.05)
        assert limiter.check("1.2.3.4") is True

    def test_auth_cost_exhausts_faster(self):
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=0)
        # AUTH_TOKEN_COST = 5, so 2 auth checks exhaust 10 tokens
        assert limiter.check("1.2.3.4", cost=TokenBucketRateLimiter.AUTH_TOKEN_COST) is True
        assert limiter.check("1.2.3.4", cost=TokenBucketRateLimiter.AUTH_TOKEN_COST) is True
        assert limiter.check("1.2.3.4", cost=1) is False

    def test_prune_removes_stale(self):
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=1)
        limiter.check("stale-ip")
        # Prune with a very short threshold
        limiter.prune(max_idle_seconds=0)
        # Internal store should be empty — next check gets fresh bucket
        assert limiter.check("stale-ip") is True
        # Verify a second full capacity is available (bucket was recreated)
        for _ in range(9):
            assert limiter.check("stale-ip") is True

    def test_clear_empties_store(self):
        limiter = TokenBucketRateLimiter(capacity=2, refill_rate=0)
        limiter.check("a")
        limiter.check("b")
        limiter.clear()
        # After clear, both IPs get fresh buckets
        assert limiter.check("a") is True
        assert limiter.check("b") is True

    def test_separate_buckets_per_ip(self):
        limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0)
        assert limiter.check("ip-a") is True
        assert limiter.check("ip-a") is False
        # Different IP should still have tokens
        assert limiter.check("ip-b") is True
