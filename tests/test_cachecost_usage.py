"""Request/session cost arithmetic — the four billing buckets, kept disjoint."""

import pytest

from context_clock.cachecost.usage import (
    RequestUsage,
    SessionUsage,
    hit_cost,
    miss_penalty,
)


class TestRequestUsage:
    def test_prompt_tokens_sums_every_input_bucket(self):
        r = RequestUsage(cache_read=100, cache_write=50, uncached_input=25, output=10)
        assert r.prompt_tokens == 175  # output is not a prompt token

    def test_each_bucket_bills_at_its_own_rate(self):
        r = RequestUsage(
            cache_read=1_000_000,
            cache_write=1_000_000,
            uncached_input=1_000_000,
            output=1_000_000,
        )
        # 0.20 + 2.50 + 2.00 + 10.00 on Sonnet
        assert r.cost("claude-sonnet-5") == pytest.approx(14.70)

    def test_one_hour_ttl_costs_more_to_write(self):
        tokens = dict(cache_write=1_000_000)
        assert RequestUsage(**tokens, ttl="5m").cost("claude-sonnet-5") == pytest.approx(2.50)
        assert RequestUsage(**tokens, ttl="1h").cost("claude-sonnet-5") == pytest.approx(4.00)

    def test_uncached_counterfactual_bills_everything_at_full_input(self):
        r = RequestUsage(cache_read=900_000, cache_write=100_000, output=0)
        assert r.uncached_cost("claude-sonnet-5") == pytest.approx(2.00)
        assert r.cost("claude-sonnet-5") == pytest.approx(0.18 + 0.25)

    def test_negative_counts_rejected(self):
        with pytest.raises(ValueError, match="must be >= 0"):
            RequestUsage(cache_read=-1)

    def test_non_integer_counts_rejected(self):
        with pytest.raises(TypeError, match="must be an int"):
            RequestUsage(cache_read=1.5)

    def test_unknown_ttl_rejected(self):
        with pytest.raises(ValueError, match="unknown cache TTL"):
            RequestUsage(cache_write=10, ttl="30m")

    def test_is_immutable(self):
        with pytest.raises(Exception):
            RequestUsage(cache_read=1).cache_read = 2


class TestSessionUsage:
    def test_starts_empty(self):
        s = SessionUsage(model="claude-sonnet-5")
        assert s.n_requests == 0
        assert s.cost == 0.0
        assert s.final_context_tokens == 0
        assert s.cache_hit_rate == 0.0

    def test_with_request_returns_a_new_session(self):
        s = SessionUsage(model="claude-sonnet-5")
        s2 = s.with_request(RequestUsage(cache_write=100))
        assert s.n_requests == 0      # original untouched
        assert s2.n_requests == 1
        assert s2 is not s

    def test_final_context_is_the_last_prompt_not_the_sum(self):
        # This is exactly the number the context meter shows — and why the
        # naive estimate is wrong.
        s = (
            SessionUsage(model="claude-sonnet-5")
            .with_request(RequestUsage(cache_write=1000))
            .with_request(RequestUsage(cache_read=1000, cache_write=500))
        )
        assert s.final_context_tokens == 1500
        assert s.prompt_tokens == 2500

    def test_counts_cache_misses(self):
        s = (
            SessionUsage(model="claude-sonnet-5")
            .with_request(RequestUsage(cache_write=100))
            .with_request(RequestUsage(cache_write=100, was_cache_miss=True))
        )
        assert s.n_cache_misses == 1

    def test_hit_rate_is_reads_over_all_input(self):
        s = SessionUsage(model="claude-sonnet-5").with_request(
            RequestUsage(cache_read=900, cache_write=100)
        )
        assert s.cache_hit_rate == pytest.approx(0.90)

    def test_reproduces_the_reported_session_bill(self):
        # 6.0M read + 308k written on Sonnet -> the "$2" from the talk.
        s = SessionUsage(model="claude-sonnet-5").with_request(
            RequestUsage(cache_read=6_000_000, cache_write=308_000)
        )
        assert s.cost == pytest.approx(1.97, abs=0.01)

    def test_reproduces_the_no_caching_counterfactual(self):
        s = SessionUsage(model="claude-sonnet-5").with_request(
            RequestUsage(cache_read=6_000_000, cache_write=308_000)
        )
        assert s.uncached_cost == pytest.approx(12.62, abs=0.01)
        assert s.cache_savings_multiple == pytest.approx(6.4, abs=0.1)

    def test_naive_estimate_understates_the_bill(self):
        s = (
            SessionUsage(model="claude-sonnet-5")
            .with_request(RequestUsage(cache_read=6_000_000, cache_write=308_000))
            .with_request(RequestUsage(cache_read=277_000))
        )
        assert s.naive_cost == pytest.approx(0.554, abs=0.001)
        assert s.naive_underestimate_multiple > 3


class TestMissPenalty:
    def test_sonnet_at_275k(self):
        assert miss_penalty(275_000, "claude-sonnet-5") == pytest.approx(0.6875)

    def test_opus_at_275k(self):
        assert miss_penalty(275_000, "claude-opus-5") == pytest.approx(1.71875)

    def test_first_request_preamble(self):
        assert miss_penalty(38_000, "claude-sonnet-5") == pytest.approx(0.095)

    def test_hit_is_twelve_and_a_half_times_cheaper_than_a_miss(self):
        ctx = 275_000
        ratio = miss_penalty(ctx, "claude-opus-5") / hit_cost(ctx, "claude-opus-5")
        assert ratio == pytest.approx(12.5)
