"""Memory backend — the contrast to full-context.

Instead of stuffing the whole growing transcript, store facts and retrieve
only the relevant one per probe. v1 backend is an exact-key retriever (the
"ideal retrieval" reference / upper bound); Attestor / Zep slot in behind the
same interface later.
"""

from context_clock.driver import make_fact
from context_clock.memory import RetrievalMemory


class TestRetrievalMemory:
    def test_recall_returns_the_stored_fact(self):
        memory = RetrievalMemory()
        for n in (1, 2, 3):
            memory.add(make_fact(n))
        assert memory.recall(2).answer == "k002"

    def test_recall_missing_returns_none(self):
        memory = RetrievalMemory()
        assert memory.recall(5) is None

    def test_add_is_idempotent_on_index(self):
        memory = RetrievalMemory()
        memory.add(make_fact(1))
        memory.add(make_fact(1))
        assert memory.recall(1).index == 1
