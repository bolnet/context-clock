"""Fill-rate knob — make_fact accepts a pad_repeat to control tokens/turn,
so a native (32K-128K) window can be filled in a reasonable number of turns.
"""

from context_clock.driver import make_fact


class TestPadRepeat:
    def test_default_unchanged(self):
        # default still embeds its (deterministic) answer in the statement
        assert make_fact(7).answer in make_fact(7).statement
        assert make_fact(7).answer == make_fact(7).answer

    def test_more_padding_makes_longer_statement(self):
        small = make_fact(1, pad_repeat=2)
        large = make_fact(1, pad_repeat=40)
        assert len(large.statement) > len(small.statement)

    def test_answer_still_present_with_padding(self):
        fact = make_fact(1, pad_repeat=40)
        assert fact.answer in fact.statement
