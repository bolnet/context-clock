"""``run_memory_session`` must accept an injected memory backend + probe
budget so any backend (RetrievalMemory, AttestorMemory, ...) plugs in
without editing the driver, while defaulting to RetrievalMemory.
"""

from __future__ import annotations

from dataclasses import dataclass

from context_clock.driver import Fact, make_fact, run_memory_session


@dataclass(frozen=True)
class _Completion:
    text: str
    prompt_tokens: int = 5
    completion_tokens: int = 1
    cost: float | None = None


class _EchoProvider:
    """Provider that answers each probe with the code from the context.

    The memo statement is injected as a system message; this provider
    extracts the planted code so recall is graded as correct when the
    backend surfaced the right memo. Records the ``max_tokens`` it saw.
    ``cost_per_call`` simulates a billed (OpenRouter) provider.
    """

    def __init__(self, cost_per_call: float | None = None) -> None:
        self.max_tokens_seen: list[int] = []
        self._cost_per_call = cost_per_call

    def complete(self, messages: list[dict], max_tokens: int = 256) -> _Completion:
        self.max_tokens_seen.append(max_tokens)
        ctx = " ".join(m["content"] for m in messages if m["role"] == "system")
        marker = "the vault code is "
        code = ""
        if marker in ctx:
            code = ctx.split(marker, 1)[1].split(".")[0].split()[0].strip(".,;")
        return _Completion(text=code, cost=self._cost_per_call)


class _SpyMemory:
    """Records add/recall and serves the exact stored fact (ideal backend)."""

    def __init__(self) -> None:
        self._store: dict[int, Fact] = {}
        self.recall_indices: list[int] = []

    def add(self, fact: Fact) -> None:
        self._store[fact.index] = fact

    def recall(self, index: int) -> Fact | None:
        self.recall_indices.append(index)
        return self._store.get(index)


class TestRunMemorySessionInjection:
    def test_uses_injected_memory_backend(self):
        provider = _EchoProvider()
        spy = _SpyMemory()

        run_memory_session(provider, turns=3, cadence=3, probe_k=3, memory=spy)

        # The spy backend was queried on the probe turn (turn 3).
        assert spy.recall_indices, "injected backend was never recalled"

    def test_passes_probe_max_tokens_to_provider(self):
        provider = _EchoProvider()
        spy = _SpyMemory()

        run_memory_session(
            provider, turns=3, cadence=3, probe_k=1, memory=spy, probe_max_tokens=2048
        )

        assert 2048 in provider.max_tokens_seen

    def test_defaults_to_retrieval_memory(self):
        # No backend injected → still runs and grades recall (RetrievalMemory).
        provider = _EchoProvider()
        rows = run_memory_session(provider, turns=3, cadence=3, probe_k=3)
        probed = [r for r in rows if r.recall is not None]
        assert probed, "expected at least one probe row"
        # Ideal echo + exact-key backend → perfect recall.
        assert probed[-1].recall == 1.0

    def test_billed_cost_threads_into_rows(self):
        # On a billed provider, each probe call's $ accrues into the row's
        # turn_cost and the running cumulative_cost — the real-money curve.
        provider = _EchoProvider(cost_per_call=0.00004)
        spy = _SpyMemory()

        rows = run_memory_session(
            provider, turns=3, cadence=3, probe_k=1, memory=spy
        )

        # One probe (turn 3, probe_k=1) → exactly one billed call.
        assert rows[-1].turn_cost == 0.00004
        assert rows[-1].cumulative_cost == 0.00004
        # Non-probe turns make no model call → no cost.
        assert rows[0].cumulative_cost == 0.0

    def test_missing_recall_grades_as_incorrect_not_crash(self):
        provider = _EchoProvider()

        class _AlwaysMiss:
            def add(self, fact: Fact) -> None: ...
            def recall(self, index: int) -> Fact | None:
                return None

        rows = run_memory_session(
            provider, turns=3, cadence=3, probe_k=3, memory=_AlwaysMiss()
        )
        probed = [r for r in rows if r.recall is not None]
        assert probed[-1].recall == 0.0
