"""Memory backends — the alternative to stuffing the full transcript.

A backend stores facts and returns only what's relevant for a query, keeping
the live context flat. v1 ships ``RetrievalMemory`` (exact-key lookup — the
ideal-retrieval reference). Real backends (Attestor, Zep, Mem0) implement the
same ``add`` / ``recall`` shape and plug in here.
"""

from __future__ import annotations

from .driver import Fact


class RetrievalMemory:
    def __init__(self) -> None:
        self._by_index: dict[int, Fact] = {}

    def add(self, fact: Fact) -> None:
        self._by_index[fact.index] = fact

    def recall(self, index: int) -> Fact | None:
        return self._by_index.get(index)
