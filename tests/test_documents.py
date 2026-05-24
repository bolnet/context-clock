"""Document generator — large, varied haystack text with a planted needle.

Each turn dumps a sizeable document (varied words, not a repeated sentence)
with one checkable needle fact buried inside, so grading stays deterministic
(NIAH-style) while filling native windows in a few turns.

The needle code is deterministic *per index* (reproducible, gradeable) but
**not derivable from the index** — so a capable model can't infer it by spotting
a pattern; it must actually have the fact in context. That keeps the benchmark
measuring retention, not pattern-inference.
"""

from context_clock.documents import make_document


class TestMakeDocument:
    def test_answer_is_deterministic(self):
        # same index → same code and same haystack, every time
        assert make_document(7).answer == make_document(7).answer
        assert make_document(7).statement == make_document(7).statement

    def test_answer_is_not_derivable_from_index(self):
        # the code must NOT be the naive readable mapping (k007), or a capable
        # model could reconstruct it without recall
        assert make_document(7).answer != "k007"
        assert make_document(1).answer != "k001"
        # still a short code token (starts with 'k')
        assert make_document(7).answer.startswith("k")

    def test_needle_is_planted_in_the_body(self):
        doc = make_document(7)
        assert doc.answer in doc.statement
        assert doc.index == 7

    def test_distinct_indices_have_distinct_codes(self):
        assert make_document(1).answer != make_document(2).answer

    def test_size_scales_with_words(self):
        small = make_document(1, words=100)
        large = make_document(1, words=1500)
        assert len(large.statement) > 5 * len(small.statement)

    def test_documents_are_varied_not_one_repeated_sentence(self):
        # distinct indices produce distinct haystacks (not identical filler)
        assert make_document(1).statement != make_document(2).statement
        # and the body uses many distinct words (real entropy, not 1 sentence ×N)
        body_words = set(make_document(1, words=300).statement.split())
        assert len(body_words) > 20
