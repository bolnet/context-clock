"""Document generator — large, varied haystack text with a planted needle.

Each turn dumps a sizeable document (varied words, not a repeated sentence)
with one checkable needle fact buried inside, so grading stays deterministic
(NIAH-style) while filling native windows in a few turns.
"""

from context_clock.documents import make_document


class TestMakeDocument:
    def test_answer_is_deterministic(self):
        assert make_document(7).answer == "k007"
        assert make_document(7).statement == make_document(7).statement

    def test_needle_is_planted_in_the_body(self):
        doc = make_document(7)
        assert "k007" in doc.statement
        assert doc.index == 7

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
