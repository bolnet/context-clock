"""Driver pure helpers — deterministic fact injection + probe scheduling.

The session loop itself is exercised by a real short run (the demo); here we
lock the pure pieces: each turn injects a uniquely-answerable fact, and probes
fire on a fixed cadence against the *oldest* facts (the ones context rot kills).
"""

from context_clock.driver import make_fact, due_probe, probe_targets


class TestMakeFact:
    def test_answer_is_unique_and_deterministic(self):
        # deterministic per index, but not the naive derivable mapping
        assert make_fact(7).answer == make_fact(7).answer
        assert make_fact(7).answer != "k007"

    def test_statement_embeds_the_answer(self):
        fact = make_fact(7)
        assert fact.answer in fact.statement
        assert fact.index == 7

    def test_distinct_facts_have_distinct_answers(self):
        assert make_fact(1).answer != make_fact(2).answer

    def test_filler_is_a_varied_haystack_not_one_repeated_sentence(self):
        # a large memo is a varied NIAH haystack, not one sentence repeated:
        # repeated filler caps distinct words ~20 regardless of size.
        big = make_fact(1, pad_repeat=40)
        assert len(set(big.statement.split())) > 40

    def test_keeps_memo_framing_so_probes_align(self):
        # the driver probes by "Memo N"; the injected fact must carry that label
        assert make_fact(7).statement.startswith("Memo 7:")


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
