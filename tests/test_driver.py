"""Driver pure helpers — deterministic fact injection + probe scheduling.

The session loop itself is exercised by a real short run (the demo); here we
lock the pure pieces: each turn injects a uniquely-answerable fact, and probes
fire on a fixed cadence against the *oldest* facts (the ones context rot kills).
"""

from context_clock.driver import make_fact, due_probe, probe_targets


class TestMakeFact:
    def test_answer_is_unique_and_deterministic(self):
        assert make_fact(7).answer == "k007"
        assert make_fact(7).answer == make_fact(7).answer

    def test_statement_embeds_the_answer(self):
        fact = make_fact(7)
        assert "k007" in fact.statement
        assert fact.index == 7

    def test_distinct_facts_have_distinct_answers(self):
        assert make_fact(1).answer != make_fact(2).answer


class TestDueProbe:
    def test_no_probe_on_turn_zero(self):
        assert due_probe(turn=0, cadence=5) is False

    def test_probe_on_cadence_boundary(self):
        assert due_probe(turn=5, cadence=5) is True

    def test_no_probe_off_boundary(self):
        assert due_probe(turn=3, cadence=5) is False


class TestProbeTargets:
    def test_returns_oldest_k(self):
        assert probe_targets(num_injected=10, k=3) == [0, 1, 2]

    def test_clamps_to_available(self):
        assert probe_targets(num_injected=2, k=3) == [0, 1]

    def test_empty_when_nothing_injected(self):
        assert probe_targets(num_injected=0, k=3) == []
