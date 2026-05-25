"""Live round-trip against a real ``attestor api`` server.

Skipped unless ``CONTEXT_CLOCK_LIVE_ATTESTOR=1`` so the suite stays green
offline. Point it at a running server with ``ATTESTOR_URL`` (default
``http://127.0.0.1:8090``). The adapter talks REST via stdlib only, so this
runs in context-clock's own venv — no ``attestor`` import needed.

Run:
    CONTEXT_CLOCK_LIVE_ATTESTOR=1 ATTESTOR_URL=http://127.0.0.1:8090 \
    .venv/bin/python -m pytest tests/test_attestor_memory_live.py -q
"""

from __future__ import annotations

import os
import uuid

import pytest

from context_clock.driver import make_fact

pytestmark = pytest.mark.skipif(
    os.environ.get("CONTEXT_CLOCK_LIVE_ATTESTOR") != "1",
    reason="set CONTEXT_CLOCK_LIVE_ATTESTOR=1 to run the live Attestor round-trip",
)


def _live_memory():
    from context_clock.attestor_memory import connect

    url = os.environ.get("ATTESTOR_URL", "http://127.0.0.1:8090")
    namespace = f"cc-livetest-{uuid.uuid4().hex[:8]}"
    return connect(url, namespace=namespace, timeout=30.0)


def test_live_add_then_recall_surfaces_the_needle():
    memory = _live_memory()
    fact = make_fact(7)
    memory.add(fact)

    hit = memory.recall(7)

    assert hit is not None, "Attestor surfaced nothing for an ingested memo"
    assert fact.answer in hit.statement


def test_live_discriminates_between_memos():
    memory = _live_memory()
    indices = [1, 5, 14, 22]
    facts = {i: make_fact(i) for i in indices}
    for f in facts.values():
        memory.add(f)

    hits = 0
    for i, f in facts.items():
        hit = memory.recall(i)
        if hit is not None and f.answer in hit.statement:
            hits += 1
    # Real semantic retrieval should resolve most scattered memos.
    assert hits >= len(indices) - 1, f"only {hits}/{len(indices)} memos recalled"
