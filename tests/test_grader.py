"""Recall grader — deterministic, no LLM judge in v1.

Facts are injected with short, checkable answers (a color, a number, a
codeword). A probe is correct if the expected answer appears as a
whole-word phrase in the model's output. Deterministic → free + reproducible.
"""

from context_clock.grader import grade


class TestGrade:
    def test_exact_match_case_insensitive(self):
        assert grade(expected="blue", actual="Blue") is True

    def test_phrase_appears_in_sentence(self):
        assert grade(expected="blue", actual="The item was blue.") is True

    def test_wrong_answer(self):
        assert grade(expected="blue", actual="It was red") is False

    def test_no_substring_false_positive(self):
        # "red" must not match inside "predator"
        assert grade(expected="red", actual="a predator appeared") is False

    def test_multiword_answer(self):
        assert grade(expected="code-7", actual="the secret was code 7 apparently") is True

    def test_numeric_answer(self):
        assert grade(expected="42", actual="I believe it was 42 apples") is True

    def test_empty_output_is_wrong(self):
        assert grade(expected="blue", actual="") is False
