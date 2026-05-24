"""Recall grader — deterministic whole-word phrase match.

v1 avoids an LLM judge entirely: injected facts have short, unambiguous
answers, so a probe is correct iff the expected answer appears as a
whole-word phrase in the model's output. Free, exact, reproducible.
"""

from __future__ import annotations

import re


def _normalize(text: str) -> str:
    """Lowercase, reduce to alphanumeric tokens, pad with spaces.

    Padding lets us test ``" expected " in " actual "`` so matches respect
    word boundaries — ``red`` will not match inside ``predator``.
    """
    collapsed = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return f" {collapsed} "


def grade(expected: str, actual: str) -> bool:
    """True iff ``expected`` appears as a whole-word phrase in ``actual``."""
    if not actual.strip():
        return False
    return _normalize(expected) in _normalize(actual)
