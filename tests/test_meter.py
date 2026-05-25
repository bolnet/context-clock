"""Token meter — pure accounting over a session.

Tracks the *live context size* (the prompt sent this turn) and the
*cumulative tokens spent* (every prompt + completion, ever). The live
context drives "is the window full"; the cumulative drives "tokens exhausted".
"""

from context_clock.meter import TokenMeter


class TestTokenMeter:
    def test_starts_empty(self):
        m = TokenMeter()
        assert m.cumulative_total == 0
        assert m.current_context == 0
        assert m.cumulative_cost == 0.0

    def test_records_billed_cost(self):
        m = TokenMeter()
        m.record(prompt_tokens=120, completion_tokens=4, cost=0.00013)
        assert m.cumulative_cost == 0.00013

    def test_accumulates_cost_across_turns(self):
        m = TokenMeter()
        m.record(prompt_tokens=120, completion_tokens=4, cost=0.00013)
        m.record(prompt_tokens=130, completion_tokens=5, cost=0.00021)
        assert m.cumulative_cost == 0.00013 + 0.00021

    def test_none_cost_contributes_zero(self):
        # Local (Ollama) calls have no billed cost — they must not crash the
        # meter nor inflate cumulative_cost.
        m = TokenMeter()
        m.record(prompt_tokens=100, completion_tokens=20, cost=None)
        m.record(prompt_tokens=100, completion_tokens=20)  # cost omitted
        assert m.cumulative_cost == 0.0

    def test_records_one_turn(self):
        m = TokenMeter()
        m.record(prompt_tokens=100, completion_tokens=20)
        assert m.current_context == 100      # live context = this turn's prompt
        assert m.cumulative_total == 120     # all tokens spent

    def test_accumulates_across_turns(self):
        m = TokenMeter()
        m.record(prompt_tokens=100, completion_tokens=20)
        m.record(prompt_tokens=150, completion_tokens=30)
        assert m.current_context == 150
        assert m.cumulative_total == 300

    def test_utilization_against_limit(self):
        m = TokenMeter()
        m.record(prompt_tokens=150, completion_tokens=30)
        assert m.utilization(limit=1000) == 0.15
