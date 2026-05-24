"""Self-compaction policy — pure logic, no LLM.

When a running session's context approaches the window limit, an agent
must compact: lossily summarize the oldest turns to reclaim room. This
module decides *when* to compact and *which* turns to fold — leaving the
actual summarization (an LLM call) to ``compactor.py``.
"""

from __future__ import annotations


def should_compact(context_tokens: int, limit: int, threshold: float = 0.9) -> bool:
    """True when the live context has filled to ``threshold`` of ``limit``."""
    return context_tokens >= threshold * limit


def select_turns_to_compact(turns: list[int], target_reclaim: int) -> list[int]:
    """Pick the oldest turns to fold until ``target_reclaim`` tokens are freed.

    ``turns`` is the per-turn token cost, oldest first. We accumulate from
    the oldest end and stop once enough is reclaimed. The most recent turn is
    never compacted — there must always be live, un-summarized context.
    """
    selected: list[int] = []
    reclaimed = 0
    for index in range(len(turns) - 1):  # never the most recent turn
        if reclaimed >= target_reclaim:
            break
        selected.append(index)
        reclaimed += turns[index]
    return selected
