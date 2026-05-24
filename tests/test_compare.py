"""Cross-model comparison — pure summary stats per run."""

from context_clock.driver import TurnRow
from context_clock.compare import summarize


def _rows():
    return [
        TurnRow(turn=1, context_tokens=164, cumulative_tokens=167, recall=None, compaction_event=False),
        TurnRow(turn=3, context_tokens=406, cumulative_tokens=1677, recall=1.0, compaction_event=False),
        TurnRow(turn=8, context_tokens=976, cumulative_tokens=7500, recall=None, compaction_event=True),
        TurnRow(turn=9, context_tokens=708, cumulative_tokens=9633, recall=1.0, compaction_event=False),
        TurnRow(turn=15, context_tokens=746, cumulative_tokens=18699, recall=0.33, compaction_event=False),
    ]


class TestSummarize:
    def test_counts_compactions(self):
        assert summarize(_rows())["compactions"] == 1

    def test_first_compaction_turn(self):
        assert summarize(_rows())["first_compaction_turn"] == 8

    def test_recall_stats_use_probe_turns_only(self):
        s = summarize(_rows())
        assert s["final_recall"] == 0.33
        assert s["min_recall"] == 0.33

    def test_total_tokens_and_peak_context(self):
        s = summarize(_rows())
        assert s["total_tokens"] == 18699
        assert s["peak_context"] == 976

    def test_handles_no_compactions(self):
        rows = [TurnRow(turn=1, context_tokens=100, cumulative_tokens=120, recall=1.0, compaction_event=False)]
        s = summarize(rows)
        assert s["compactions"] == 0
        assert s["first_compaction_turn"] is None
        assert s["tokens_before_compaction"] is None

    def test_tokens_consumed_before_first_compaction(self):
        # cumulative spend at the first compaction turn — the budget burned
        # before the window forces you to compact
        assert summarize(_rows())["tokens_before_compaction"] == 7500
