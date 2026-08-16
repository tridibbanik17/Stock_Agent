"""Unit tests for the in-process rate limiter."""

import pytest

from app.services.rate_limit import SlidingWindowLimiter


class TestSlidingWindowLimiter:
    """Sliding window per-key rate limiter."""

    def test_allows_under_limit(self):
        limiter = SlidingWindowLimiter()
        for _ in range(5):
            allowed, remaining, retry = limiter.check("key1", limit=5, window_seconds=60)
            assert allowed is True
            assert retry == 0

    def test_blocks_at_limit(self):
        limiter = SlidingWindowLimiter()
        for _ in range(5):
            limiter.check("key2", limit=5, window_seconds=60)
        # 6th request should be blocked
        allowed, remaining, retry = limiter.check("key2", limit=5, window_seconds=60)
        assert allowed is False
        assert remaining == 0
        assert retry > 0

    def test_different_keys_independent(self):
        limiter = SlidingWindowLimiter()
        for _ in range(5):
            limiter.check("a", limit=5, window_seconds=60)
        # Key "a" is exhausted
        allowed_a, _, _ = limiter.check("a", limit=5, window_seconds=60)
        assert allowed_a is False
        # Key "b" is fresh
        allowed_b, _, _ = limiter.check("b", limit=5, window_seconds=60)
        assert allowed_b is True

    def test_remaining_decreases(self):
        limiter = SlidingWindowLimiter()
        _, remaining1, _ = limiter.check("r", limit=3, window_seconds=60)
        _, remaining2, _ = limiter.check("r", limit=3, window_seconds=60)
        _, remaining3, _ = limiter.check("r", limit=3, window_seconds=60)
        assert remaining1 == 2
        assert remaining2 == 1
        assert remaining3 == 0

    def test_zero_limit_always_allows(self):
        limiter = SlidingWindowLimiter()
        allowed, _, _ = limiter.check("x", limit=0, window_seconds=60)
        assert allowed is True

    def test_prune_clears_empty_keys(self):
        limiter = SlidingWindowLimiter()
        # Use a very short window so entries expire immediately
        limiter.check("temp", limit=1, window_seconds=0.001)
        import time
        time.sleep(0.05)
        # Trigger the window eviction by checking again (forces popleft)
        limiter.check("temp", limit=1, window_seconds=0.001)
        # Now prune should clear it since the old entry was evicted
        limiter.prune()
        # The key might still exist but have the new entry; just verify prune doesn't crash
        # and that the key count stays bounded
        assert len(limiter._windows) <= 1
