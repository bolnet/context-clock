"""Cache-lifecycle simulator — the documented rules, and only those.

The rules under test: the TTL clock starts at request start (generation time
counts against it), a read refreshes it for free, a miss rewrites the whole
prefix, and a turn appending more than 20 blocks misses regardless of time.
"""

import pytest

from context_clock.cachecost.scenarios import (
    Turn,
    continuous,
    heartbeat_bridge,
    sawtooth,
    simulate,
)


class TestFirstRequest:
    def test_writes_the_preamble_and_never_counts_as_a_miss(self):
        s = simulate([Turn(new_tokens=1_000)], "claude-sonnet-5", system_tokens=38_000)
        first = s.requests[0]
        assert first.cache_write == 39_000
        assert first.cache_read == 0
        assert first.was_cache_miss is False  # cost of entry, not a miss

    def test_preamble_is_written_once_not_per_turn(self):
        s = simulate([Turn(new_tokens=1_000)] * 3, "claude-sonnet-5", system_tokens=38_000)
        assert s.requests[1].cache_write == 1_000
        assert s.requests[2].cache_write == 1_000


class TestCacheHit:
    def test_hit_reads_the_prefix_and_writes_only_the_tail(self):
        s = simulate(
            [Turn(new_tokens=1_000), Turn(new_tokens=500, generation=30)],
            "claude-sonnet-5",
        )
        second = s.requests[1]
        assert second.cache_read == 1_000
        assert second.cache_write == 500
        assert second.was_cache_miss is False

    def test_a_read_refreshes_the_timer_so_continuous_traffic_never_misses(self):
        # Four minutes between every request start, indefinitely: all hits.
        s = simulate([Turn(new_tokens=100, idle_before=240)] * 20, "claude-sonnet-5")
        assert s.n_cache_misses == 0


class TestCacheMiss:
    def test_idling_past_the_ttl_rewrites_the_whole_prefix(self):
        s = simulate(
            [Turn(new_tokens=10_000), Turn(new_tokens=500, idle_before=418)],
            "claude-sonnet-5",
        )
        second = s.requests[1]
        assert second.was_cache_miss is True
        assert second.cache_read == 0
        assert second.cache_write == 10_500  # prefix + tail, all rewritten

    def test_generation_time_counts_against_the_ttl(self):
        # Three minutes generating + three minutes thinking = 360s > 300s TTL,
        # even though the human only "waited" three minutes.
        s = simulate(
            [Turn(new_tokens=1_000), Turn(new_tokens=100, generation=180, idle_before=180)],
            "claude-sonnet-5",
        )
        assert s.requests[1].was_cache_miss is True

    def test_a_four_minute_gap_after_a_four_minute_generation_still_misses(self):
        s = simulate(
            [Turn(new_tokens=1_000), Turn(new_tokens=100, generation=240, idle_before=240)],
            "claude-sonnet-5",
        )
        assert s.requests[1].was_cache_miss is True

    def test_exactly_at_the_ttl_boundary_still_hits(self):
        s = simulate(
            [Turn(new_tokens=1_000), Turn(new_tokens=100, idle_before=300)],
            "claude-sonnet-5",
        )
        assert s.requests[1].was_cache_miss is False

    def test_one_hour_ttl_survives_a_gap_that_kills_the_five_minute_cache(self):
        turns = [Turn(new_tokens=1_000), Turn(new_tokens=100, idle_before=1_800)]
        assert simulate(turns, "claude-sonnet-5", ttl="5m").n_cache_misses == 1
        assert simulate(turns, "claude-sonnet-5", ttl="1h").n_cache_misses == 0


class TestLookbackWindow:
    def test_a_turn_over_twenty_blocks_misses_with_zero_elapsed_time(self):
        # The mechanism the talk never mentions: same 12.5x penalty, no clock.
        s = simulate(
            [Turn(new_tokens=1_000), Turn(new_tokens=100, blocks_added=21)],
            "claude-sonnet-5",
        )
        assert s.requests[1].was_cache_miss is True

    def test_exactly_twenty_blocks_still_hits(self):
        s = simulate(
            [Turn(new_tokens=1_000), Turn(new_tokens=100, blocks_added=20)],
            "claude-sonnet-5",
        )
        assert s.requests[1].was_cache_miss is False


class TestNamedScenarios:
    def test_continuous_session_has_no_misses(self):
        assert continuous(31, "claude-sonnet-5").n_cache_misses == 0

    def test_sawtooth_misses_every_turn_after_the_first(self):
        s = sawtooth(10, "claude-sonnet-5")
        assert s.n_cache_misses == 9  # the first request has nothing to lose

    def test_sawtooth_costs_far_more_than_the_same_work_done_continuously(self):
        busy = continuous(31, "claude-sonnet-5")
        saw = sawtooth(31, "claude-sonnet-5")
        assert saw.prompt_tokens == busy.prompt_tokens  # identical work
        assert saw.cost > 5 * busy.cost                 # purely from idling

    def test_caching_beats_no_caching_on_a_busy_session(self):
        busy = continuous(31, "claude-sonnet-5")
        assert busy.cache_savings_multiple > 4

    def test_rejects_an_unknown_ttl(self):
        with pytest.raises(ValueError, match="unknown cache TTL"):
            simulate([Turn(new_tokens=1)], "claude-sonnet-5", ttl="30m")


class TestHeartbeatBridge:
    def test_beats_fit_inside_the_gap(self):
        s = heartbeat_bridge(275_000, gap_seconds=1_200, model="claude-sonnet-5")
        assert s.n_requests == 5  # 1200s / 240s
        assert s.cache_write_tokens == 0  # refresh reads only

    def test_short_gap_is_cheaper_to_bridge_than_to_miss(self):
        from context_clock.cachecost.usage import miss_penalty

        bridge = heartbeat_bridge(275_000, 10 * 60, "claude-sonnet-5")
        assert bridge.cost < miss_penalty(275_000, "claude-sonnet-5")

    def test_long_gap_is_cheaper_to_miss_than_to_bridge(self):
        from context_clock.cachecost.usage import miss_penalty

        bridge = heartbeat_bridge(275_000, 70 * 60, "claude-sonnet-5")
        assert bridge.cost > miss_penalty(275_000, "claude-sonnet-5")

    def test_break_even_lands_near_fifty_minutes(self):
        from context_clock.cachecost.usage import miss_penalty

        miss = miss_penalty(275_000, "claude-sonnet-5")
        assert heartbeat_bridge(275_000, 48 * 60, "claude-sonnet-5").cost < miss
        assert heartbeat_bridge(275_000, 52 * 60, "claude-sonnet-5").cost > miss

    def test_interval_must_fit_inside_the_ttl(self):
        with pytest.raises(ValueError, match="does not fit inside"):
            heartbeat_bridge(1_000, 3_600, "claude-sonnet-5", interval=400)
