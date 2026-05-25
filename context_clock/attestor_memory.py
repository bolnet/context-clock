"""Attestor-backed memory adapter for the context-clock benchmark.

``AttestorMemory`` implements the same ``add`` / ``recall`` shape as
``RetrievalMemory`` but is backed by a real semantic memory system
(Attestor v4) instead of an exact-key dict. Where ``RetrievalMemory`` is
the *ideal-retrieval reference* (it can never miss), this adapter measures
whether honest semantic retrieval keeps the planted needle recallable as
the session grows.

Design:
- **Dependency injection.** The Attestor client is passed in, so unit
  tests mock it and the adapter never hard-imports ``attestor`` (which
  lives in a separate venv). ``connect`` is a convenience factory that
  builds a real ``attestor.client.MemoryClient`` over HTTP.
- **Honesty.** ``recall`` issues a semantic query and returns whatever
  Attestor surfaces. It does NOT fall back to a local exact-key lookup.
  A backend miss returns ``None`` — a real finding, not a bug to hide.
- **Namespace isolation.** Every write/read is scoped to a per-run
  namespace so benchmark memos don't collide with other tenants' data.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .driver import Fact


class _AttestorClient(Protocol):
    """The slice of ``attestor.client.MemoryClient`` this adapter uses."""

    def add(self, content: str, **kwargs: Any) -> Any: ...

    def recall(self, query: str, **kwargs: Any) -> list[Any]: ...


@dataclass(frozen=True)
class _Memory:
    """Minimal view of an Attestor memory row (only the field we read)."""

    content: str


@dataclass(frozen=True)
class _Result:
    """Minimal view of an Attestor ``RetrievalResult``."""

    memory: _Memory
    score: float
    match_source: str


class _HttpClient:
    """Self-contained stdlib client for the ``attestor api`` REST surface.

    Mirrors ``attestor.client.MemoryClient``'s ``/add`` and ``/recall`` wire
    protocol using only ``urllib`` — so the adapter runs in context-clock's
    own interpreter without importing the (separately-installed) ``attestor``
    package. Errors are surfaced explicitly; nothing is swallowed.
    """

    def __init__(
        self,
        base_url: str,
        agent_id: str,
        timeout: float,
        *,
        retries: int = 4,
        backoff: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retries = retries
        self._backoff = backoff
        self._headers = {
            "Content-Type": "application/json",
            "X-Agent-ID": agent_id,
        }

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        """POST with bounded retry on transient backend failures.

        The local Attestor stack is periodically recreated by an external
        harness (Postgres + API containers bounce together), which drops
        in-flight connections and briefly 500s. A 5xx or connection error
        is therefore treated as transient and retried with linear backoff;
        a 4xx (client error) is raised immediately. After the last attempt
        the error propagates — failures are surfaced, never swallowed.
        """
        data = json.dumps(body).encode()
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            req = urllib.request.Request(
                f"{self._base_url}{path}",
                data=data,
                headers=self._headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - trusted local URL
                    result = json.loads(resp.read())
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "attestor API error"))
                return result["data"]
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    raise  # client error — not transient
                last_exc = exc
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                last_exc = exc
            if attempt < self._retries - 1:
                time.sleep(self._backoff * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def add(self, content: str, **kwargs: Any) -> _Memory:
        body: dict[str, Any] = {"content": content}
        for key in ("tags", "category", "entity", "namespace", "metadata"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        data = self._post("/add", body)
        return _Memory(content=data.get("content", content))

    def recall(self, query: str, **kwargs: Any) -> list[_Result]:
        body: dict[str, Any] = {"query": query}
        for key in ("budget", "namespace"):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        data = self._post("/recall", body)
        return [
            _Result(
                memory=_Memory(content=item["content"]),
                score=item.get("score", 0.0),
                match_source=item.get("source", "?"),
            )
            for item in data
        ]


def _recall_query(index: int) -> str:
    """Semantic probe text for a memo. Mirrors the driver's probe wording."""
    return f"vault code in Memo {index}"


class AttestorMemory:
    """Memory backend that delegates storage + retrieval to Attestor.

    Args:
        client: Anything implementing ``add`` / ``recall`` (the real
            ``MemoryClient`` in production, a fake in unit tests).
        namespace: Tenant scope for every write and read.
        category: Attestor category tag for ingested memos.
        recall_budget: Token budget passed to Attestor's recall pack.
    """

    def __init__(
        self,
        client: _AttestorClient,
        *,
        namespace: str,
        category: str = "memo",
        recall_budget: int = 512,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._category = category
        self._recall_budget = recall_budget

    def add(self, fact: Fact) -> None:
        """Ingest one memo into Attestor, tagged with its index.

        The memo index is stored both as a tag (``memo:N``) and in
        ``metadata`` so the write is identifiable for audit, even though
        ``recall`` deliberately retrieves *semantically*, not by key.
        """
        self._client.add(
            fact.statement,
            tags=["memo", f"memo:{fact.index}"],
            category=self._category,
            entity=f"Memo {fact.index}",
            namespace=self._namespace,
            metadata={"memo_index": fact.index},
        )

    def recall(self, index: int) -> Fact | None:
        """Semantically retrieve context for ``index`` from Attestor.

        Attestor packs the top matches up to ``recall_budget`` tokens; we feed
        the model **all** of them (highest-scored first), so a larger budget
        means a larger retrieved context — not just a single memo. The
        ``statement`` is exactly what Attestor surfaced (the driver feeds it to
        the model). Returns ``None`` when Attestor surfaces nothing — an honest
        miss, never a local exact-key fallback.
        """
        results = self._client.recall(
            _recall_query(index),
            budget=self._recall_budget,
            namespace=self._namespace,
        )
        if not results:
            return None
        statement = "\n".join(r.memory.content for r in results)
        return Fact(index=index, statement=statement, answer="")


def connect(
    base_url: str,
    *,
    namespace: str,
    agent_id: str = "context-clock",
    category: str = "memo",
    recall_budget: int = 512,
    timeout: float = 30.0,
) -> AttestorMemory:
    """Build an ``AttestorMemory`` backed by a live ``attestor api`` server.

    Uses a self-contained stdlib HTTP client, so context-clock talks to
    Attestor over REST without importing the (separately-installed)
    ``attestor`` package — the benchmark and Attestor can live in different
    virtualenvs.
    """
    client = _HttpClient(base_url, agent_id=agent_id, timeout=timeout)
    return AttestorMemory(
        client,
        namespace=namespace,
        category=category,
        recall_budget=recall_budget,
    )
