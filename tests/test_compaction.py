"""Compaction policy — pure logic, no LLM.

Decides WHEN the running context is full enough to trigger a self-compaction,
and WHICH oldest turns to fold into a summary.
"""

from context_clock.compaction import should_compact, select_turns_to_compact


class TestShouldCompact:
    def test_fires_when_at_threshold(self):
        # 90% of a 1000-token window with a 0.9 threshold → compact
        assert should_compact(context_tokens=900, limit=1000, threshold=0.9) is True

    def test_holds_below_threshold(self):
        assert should_compact(context_tokens=899, limit=1000, threshold=0.9) is False

    def test_fires_when_over_threshold(self):
        assert should_compact(context_tokens=980, limit=1000, threshold=0.9) is True


class TestSelectTurnsToCompact:
    def test_selects_oldest_turns_until_target_freed(self):
        # turns carry token costs (oldest first); free ~half the context
        turns = [100, 100, 100, 100, 100]  # 500 total
        # want to reclaim at least 250 tokens → fold the 3 oldest (300 >= 250)
        assert select_turns_to_compact(turns, target_reclaim=250) == [0, 1, 2]

    def test_keeps_recent_turns_untouched(self):
        turns = [50, 50, 400]
        # reclaim 80 → only the 2 oldest small turns (100 >= 80), never the recent big one
        assert select_turns_to_compact(turns, target_reclaim=80) == [0, 1]

    def test_never_compacts_everything_when_target_unreachable(self):
        # asking to reclaim more than exists → fold all but the most recent turn
        turns = [100, 100, 100]
        assert select_turns_to_compact(turns, target_reclaim=10_000) == [0, 1]
