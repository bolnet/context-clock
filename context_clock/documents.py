"""Document generator — NIAH-style haystack with a planted needle.

Each call builds a sizeable, *varied* document (many distinct words drawn from
a themed vocabulary, not one sentence repeated N times) with a single checkable
needle fact buried in the middle. This fills a native context window in a few
turns while keeping grading deterministic: the needle is always ``k{n:03d}``.

Deterministic by construction — the body is sampled from a PRNG seeded by the
document index, so the same index always yields the same haystack, and distinct
indices yield distinct ones.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Themed shift-log vocabulary — enough distinct words for real entropy so a
# few-hundred-word body never collapses to a handful of repeated tokens.
_VOCAB: tuple[str, ...] = (
    "routine shift note subsystems nominal logs rotated anomalies observed "
    "telemetry within bounds duty roster unchanged reactor coolant pressure "
    "stable valve sensor array calibrated drift negligible relay handshake "
    "acknowledged buffer flushed queue drained latency steady throughput "
    "checksum verified manifest sealed beacon pulse cadence interval window "
    "diagnostic sweep clean residual noise filtered baseline restored operator "
    "console quiet manifold purge cycle complete inventory reconciled gauge "
    "reading threshold margin ample uplink downlink sync ledger entry appended"
).split()

_NEEDLE = "Critical: the vault code is {answer}."


@dataclass(frozen=True)
class Document:
    index: int
    statement: str
    answer: str


def make_document(n: int, words: int = 200) -> Document:
    """Build a deterministic ~``words``-word haystack with needle ``k{n:03d}``.

    The needle is planted at the midpoint of the body (the classic, hardest
    needle-in-a-haystack position). ``words`` controls the haystack size so
    callers can dial tokens-per-turn up to fill large native windows.
    """
    answer = f"k{n:03d}"
    rng = random.Random(n)  # seed by index → varied across n, stable per n
    body = [rng.choice(_VOCAB) for _ in range(max(1, words))]
    pos = len(body) // 2
    needle = _NEEDLE.format(answer=answer)
    statement = " ".join([*body[:pos], needle, *body[pos:]])
    return Document(index=n, statement=statement, answer=answer)
