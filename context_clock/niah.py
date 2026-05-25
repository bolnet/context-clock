"""Needle-in-a-haystack by depth — intra-window long-context degradation.

For big-window API models (fixed large window, no `num_ctx` truncation knob):
build a haystack of ~`target_tokens` filler with one **unpredictable** needle at a
fractional depth, probe recall, and sweep (size × depth). This measures where a
model loses the needle *within* its window ("lost in the middle"), which is the
distinct question for a frontier model — the local benchmark instead measures
truncation rot once the window overflows.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .documents import _VOCAB  # reuse the themed haystack vocabulary
from .grader import grade

_NEEDLE_TMPL = "Critical operational note: the vault code is {answer}."
_TOKENS_PER_WORD = 1.3  # rough; actual prompt size is read back from the API


@dataclass(frozen=True)
class Haystack:
    statement: str
    answer: str
    target_tokens: int
    depth: float


@dataclass(frozen=True)
class NiahRow:
    size: int            # target token size of the haystack
    depth: float         # fractional needle position (0=start, 1=end)
    hit: bool            # did the model recall the needle?
    prompt_tokens: int   # actual prompt tokens (from the provider)
    completion_tokens: int


def build_haystack(target_tokens: int, index: int = 1, depth: float = 0.5) -> Haystack:
    """Deterministic ~`target_tokens` haystack with the needle at fractional `depth`."""
    rng = random.Random(index)
    answer = f"k{rng.randrange(16**5):05x}"  # deterministic per index, not derivable
    n_words = max(1, int(target_tokens / _TOKENS_PER_WORD))
    body = [rng.choice(_VOCAB) for _ in range(n_words)]
    pos = int(len(body) * min(max(depth, 0.0), 1.0))
    needle = _NEEDLE_TMPL.format(answer=answer)
    statement = " ".join([*body[:pos], needle, *body[pos:]])
    return Haystack(statement=statement, answer=answer, target_tokens=target_tokens, depth=depth)


def run_niah(
    provider,
    *,
    sizes: list[int],
    depths: list[float],
    probe_max_tokens: int = 2048,
    index_base: int = 1,
) -> list[NiahRow]:
    """Sweep (size × depth): one probe per cell, recording recall + token usage.

    A fresh needle index per cell keeps codes distinct so the model can't carry an
    answer between probes. Works with any provider exposing ``complete(messages, max_tokens)``.
    """
    rows: list[NiahRow] = []
    idx = index_base
    for size in sizes:
        for depth in depths:
            hay = build_haystack(size, index=idx, depth=depth)
            idx += 1
            messages = [
                {"role": "system", "content": "You are a memo keeper. Answer tersely."},
                {"role": "user", "content": hay.statement + "\nWhat is the vault code? Reply with only the code."},
            ]
            resp = provider.complete(messages, max_tokens=probe_max_tokens)
            rows.append(
                NiahRow(
                    size=size,
                    depth=depth,
                    hit=grade(hay.answer, resp.text),
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                )
            )
    return rows
