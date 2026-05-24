"""Token meter — accounting over a long session.

Two distinct quantities the benchmark cares about:

* ``current_context`` — the prompt size sent *this* turn. This is what
  fills the window and triggers compaction.
* ``cumulative_total`` — every token ever spent (prompt + completion).
  This is the "tokens exhausted / cost climbing" curve.
"""

from __future__ import annotations


class TokenMeter:
    def __init__(self) -> None:
        self.current_context: int = 0
        self.cumulative_total: int = 0

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Log one turn's real token usage (from the provider's response)."""
        self.current_context = prompt_tokens
        self.cumulative_total += prompt_tokens + completion_tokens

    def utilization(self, limit: int) -> float:
        """Fraction of the context window currently filled."""
        return self.current_context / limit
