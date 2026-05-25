"""Token meter — accounting over a long session.

Two distinct quantities the benchmark cares about:

* ``current_context`` — the prompt size sent *this* turn. This is what
  fills the window and triggers compaction.
* ``cumulative_total`` — every token ever spent (prompt + completion).
  This is the "tokens exhausted / cost climbing" curve.
* ``cumulative_cost`` — real billed dollars, summed from the provider's
  ``usage.cost`` (OpenRouter). Local/unbilled calls report no cost and
  contribute $0, so this stays exact across mixed local + API runs.
"""

from __future__ import annotations


class TokenMeter:
    def __init__(self) -> None:
        self.current_context: int = 0
        self.cumulative_total: int = 0
        self.cumulative_prompt: int = 0
        self.cumulative_completion: int = 0
        self.cumulative_cost: float = 0.0

    def record(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float | None = None,
    ) -> None:
        """Log one turn's real token usage (from the provider's response).

        ``cost`` is the provider's billed dollars for this call; ``None``
        (unbilled/unknown, e.g. a local Ollama call) contributes $0.
        """
        self.current_context = prompt_tokens
        self.cumulative_total += prompt_tokens + completion_tokens
        self.cumulative_prompt += prompt_tokens
        self.cumulative_completion += completion_tokens
        self.cumulative_cost += cost or 0.0

    def utilization(self, limit: int) -> float:
        """Fraction of the context window currently filled."""
        return self.current_context / limit
