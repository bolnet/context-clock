"""Unit tests for the Attestor-backed memory adapter.

These mock the Attestor client so the suite stays green offline. A live
round-trip against a real ``attestor api`` server is gated behind
``CONTEXT_CLOCK_LIVE_ATTESTOR`` (see ``test_attestor_memory_live.py``).

The adapter must be *honest*: ``recall`` reflects whatever Attestor's
semantic retrieval returns, never a local exact-key shortcut. A miss
returns ``None`` (a real finding, not a bug to paper over).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import urllib.error

from context_clock.attestor_memory import AttestorMemory, _HttpClient
from context_clock.driver import Fact, make_fact


@dataclass(frozen=True)
class _FakeMemory:
    content: str


@dataclass(frozen=True)
class _FakeResult:
    memory: _FakeMemory
    score: float = 1.0
    match_source: str = "vector"


class _RecordingClient:
    """A minimal stand-in for ``attestor.client.MemoryClient``.

    Records ``add`` calls and serves ``recall`` from a scripted map so
    tests can assert ingest metadata and simulate hits/misses without a
    live backend.
    """

    def __init__(self, recall_map: dict[str, list[_FakeResult]] | None = None) -> None:
        self.added: list[dict] = []
        self.recall_calls: list[dict] = []
        self._recall_map = recall_map or {}

    def add(self, content, **kwargs):  # noqa: ANN001, ANN003 - duck-typed
        self.added.append({"content": content, **kwargs})
        return _FakeMemory(content=content)

    def recall(self, query, **kwargs):  # noqa: ANN001, ANN003 - duck-typed
        self.recall_calls.append({"query": query, **kwargs})
        return self._recall_map.get(query, [])


class TestAttestorMemoryAdd:
    def test_add_ingests_statement_content(self):
        client = _RecordingClient()
        memory = AttestorMemory(client=client, namespace="ns-1")
        fact = make_fact(7)

        memory.add(fact)

        assert len(client.added) == 1
        assert client.added[0]["content"] == fact.statement

    def test_add_tags_with_memo_index_so_it_is_identifiable(self):
        client = _RecordingClient()
        memory = AttestorMemory(client=client, namespace="ns-1")

        memory.add(make_fact(14))

        tags = client.added[0]["tags"]
        assert "memo:14" in tags
        assert client.added[0]["metadata"]["memo_index"] == 14

    def test_add_scopes_to_the_configured_namespace(self):
        client = _RecordingClient()
        memory = AttestorMemory(client=client, namespace="run-abc")

        memory.add(make_fact(3))

        assert client.added[0]["namespace"] == "run-abc"


class TestAttestorMemoryRecall:
    def test_recall_queries_semantically_for_the_memo(self):
        client = _RecordingClient()
        memory = AttestorMemory(client=client, namespace="ns-1")

        memory.recall(9)

        assert client.recall_calls[0]["query"] == "vault code in Memo 9"
        assert client.recall_calls[0]["namespace"] == "ns-1"

    def test_recall_returns_fact_with_retrieved_statement(self):
        retrieved = "Memo 5: the vault code is zp9q1t; routine note."
        query = "vault code in Memo 5"
        client = _RecordingClient(
            recall_map={query: [_FakeResult(_FakeMemory(retrieved))]}
        )
        memory = AttestorMemory(client=client, namespace="ns-1")

        fact = memory.recall(5)

        assert isinstance(fact, Fact)
        assert fact.index == 5
        assert fact.statement == retrieved

    def test_recall_packs_all_retrieved_memos_highest_first(self):
        # Attestor packs up to recall_budget tokens; the adapter feeds the
        # model ALL of them (highest-scored first), so a larger budget means
        # a larger retrieved context — not just the single top memo.
        query = "vault code in Memo 2"
        top = "Memo 2: the vault code is aa11bb."
        other = "Memo 99: the vault code is zz00yy."
        client = _RecordingClient(
            recall_map={
                query: [
                    _FakeResult(_FakeMemory(top), score=0.9),
                    _FakeResult(_FakeMemory(other), score=0.4),
                ]
            }
        )
        memory = AttestorMemory(client=client, namespace="ns-1")

        fact = memory.recall(2)

        assert fact is not None
        assert top in fact.statement and other in fact.statement
        assert fact.statement.index(top) < fact.statement.index(other)  # highest score first

    def test_recall_miss_returns_none(self):
        client = _RecordingClient(recall_map={})  # backend returns nothing
        memory = AttestorMemory(client=client, namespace="ns-1")

        assert memory.recall(42) is None


class TestHttpClientRetry:
    """The local Attestor stack is periodically recreated; transient 5xx /
    connection drops must be retried, while client errors fail fast.
    """

    def _client(self, monkeypatch, responses):
        client = _HttpClient("http://x", agent_id="cc", timeout=1.0, backoff=0.0)
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):  # noqa: ANN001
            i = calls["n"]
            calls["n"] += 1
            outcome = responses[min(i, len(responses) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(
            "context_clock.attestor_memory.urllib.request.urlopen", fake_urlopen
        )
        return client, calls

    def test_retries_then_succeeds_on_transient_500(self, monkeypatch):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                import json

                return json.dumps({"ok": True, "data": [{"content": "ok"}]}).encode()

        err500 = urllib.error.HTTPError("http://x", 500, "boom", {}, None)
        client, calls = self._client(monkeypatch, [err500, err500, _Resp()])

        data = client._post("/recall", {"query": "q"})

        assert data == [{"content": "ok"}]
        assert calls["n"] == 3  # two failures, then success

    def test_client_error_4xx_raises_immediately(self, monkeypatch):
        err400 = urllib.error.HTTPError("http://x", 400, "bad", {}, None)
        client, calls = self._client(monkeypatch, [err400])

        with pytest.raises(urllib.error.HTTPError):
            client._post("/recall", {"query": "q"})
        assert calls["n"] == 1  # no retry on client error


class TestAttestorMemoryRoundTrip:
    def test_add_then_recall_surfaces_the_planted_needle(self):
        client = _RecordingClient()
        memory = AttestorMemory(client=client, namespace="ns-1")
        fact = make_fact(11)
        memory.add(fact)
        # Wire the fake backend to return what was just added.
        client._recall_map["vault code in Memo 11"] = [
            _FakeResult(_FakeMemory(fact.statement))
        ]

        hit = memory.recall(11)

        assert hit is not None
        assert fact.answer in hit.statement
