"""Fill-rate knob — make_fact accepts a pad_repeat to control tokens/turn,
so a native (32K-128K) window can be filled in a reasonable number of turns.
"""

from context_clock.driver import make_fact


class TestPadRepeat:
    def test_default_unchanged(self):
        # default still embeds the answer (backward compatible)
        assert make_fact(7).answer == "k007"
        assert "k007" in make_fact(7).statement

    def test_more_padding_makes_longer_statement(self):
        small = make_fact(1, pad_repeat=2)
        large = make_fact(1, pad_repeat=40)
        assert len(large.statement) > len(small.statement)

    def test_answer_still_present_with_padding(self):
        assert "k001" in make_fact(1, pad_repeat=40).statement
