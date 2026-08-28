"""Cache-lifecycle simulator — turns a session *shape* into billable tokens.

Implements the documented cache semantics, and only those:

1. An entry's lifetime is measured from the **start** of the request that
   writes or reads it. Generation time counts against the TTL, so a 4-minute
   response leaves ~1 minute for the next request to start.
2. A cache **read refreshes the timer for free**. Continuous traffic keeps a
   5-minute entry alive indefinitely.
3. A breakpoint walks back at most 20 content blocks. A turn appending more
   than that misses even with zero elapsed time — a second, time-independent
   cause of the same cliff.
4. On a hit, the prefix bills at the read rate and only the new tail is
   written. On a miss, the whole prefix is rewritten at the write rate.

Output is a ``SessionUsage``, so simulated and measured sessions are priced by
exactly the same code. Simulated runs are **derived**, never reported as
measured — see CACHE_CLAIMS.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from .pricing import LOOKBACK_BLOCKS, TTL_1H_SECONDS, TTL_5M_SECONDS
from .usage import RequestUsage, SessionUsage

_TTL_SECONDS = {"5m": TTL_5M_SECONDS, "1h": TTL_1H_SECONDS}


@dataclass(frozen=True)
class Turn:
    """One request in a session shape.

    ``idle_before`` is wall-clock the caller spent *not* generating — thinking,
    a meeting, a background task the agent is blocked on. ``generation``
    is how long the previous response took to stream; both count against
    the TTL because the clock runs from request start.
    """

    new_tokens: int
    output: int = 0
    idle_before: float = 0.0
    generation: float = 0.0
    blocks_added: int = 1


def simulate(
    turns: list[Turn],
    model: str,
    *,
    system_tokens: int = 0,
    ttl: str = "5m",
) -> SessionUsage:
    """Replay a session shape through the cache rules and bill every request.

    ``system_tokens`` is the fixed preamble (system prompt, CLAUDE.md, skill
    definitions, tool schemas) written to cache on the first request.
    """
    if ttl not in _TTL_SECONDS:
        raise ValueError(f"unknown cache TTL {ttl!r}; expected '5m' or '1h'")
    ttl_seconds = _TTL_SECONDS[ttl]

    session = SessionUsage(model=model)
    cached_prefix = 0        # tokens currently live in cache
    since_touch = 0.0        # seconds since the cache entry was last written or read

    for index, turn in enumerate(turns):
        # The clock runs from request start, so the previous response's
        # generation time is spent before this request can begin.
        since_touch += turn.generation + turn.idle_before

        expired = cached_prefix > 0 and since_touch > ttl_seconds
        overflowed = cached_prefix > 0 and turn.blocks_added > LOOKBACK_BLOCKS
        missed = expired or overflowed

        # The first request has nothing cached yet: it writes, but that is the
        # unavoidable cost of entry, not a miss.
        preamble = system_tokens if index == 0 else 0

        if cached_prefix == 0:
            read, write = 0, preamble + turn.new_tokens
        elif missed:
            read, write = 0, cached_prefix + turn.new_tokens
        else:
            read, write = cached_prefix, turn.new_tokens

        session = session.with_request(
            RequestUsage(
                cache_read=read,
                cache_write=write,
                output=turn.output,
                ttl=ttl,
                was_cache_miss=missed,
            )
        )

        # Everything read or written this turn is now the live cached prefix,
        # and the request start reset the timer.
        cached_prefix = read + write
        since_touch = 0.0

    return session


def continuous(
    n_turns: int,
    model: str,
    *,
    system_tokens: int = 38_000,
    new_tokens: int = 2_400,
    output: int = 600,
    generation: float = 30.0,
) -> SessionUsage:
    """The busy session: every turn starts well inside the TTL. No misses."""
    return simulate(
        [Turn(new_tokens=new_tokens, output=output, generation=generation)] * n_turns,
        model,
        system_tokens=system_tokens,
    )


def sawtooth(
    n_turns: int,
    model: str,
    *,
    idle: float = 420.0,
    system_tokens: int = 38_000,
    new_tokens: int = 2_400,
    output: int = 600,
    generation: float = 30.0,
) -> SessionUsage:
    """The worst case: every turn idles past the TTL, so every turn rewrites.

    This is the agent blocked on a seven-minute background task, or the human
    who steps away between prompts.
    """
    return simulate(
        [
            Turn(
                new_tokens=new_tokens,
                output=output,
                idle_before=idle,
                generation=generation,
            )
        ]
        * n_turns,
        model,
        system_tokens=system_tokens,
    )


def heartbeat_bridge(
    context_tokens: int,
    gap_seconds: float,
    model: str,
    *,
    interval: float = 240.0,
    ttl: str = "5m",
) -> SessionUsage:
    """Keep a cache warm across an idle gap with periodic no-op refresh reads.

    Each heartbeat re-reads the whole prefix at 0.1x. Cheaper than eating the
    miss only while the reads cost less than one rewrite — see
    ``pricing.reads_per_write``.
    """
    if interval >= _TTL_SECONDS[ttl]:
        raise ValueError(
            f"heartbeat interval {interval}s does not fit inside the {ttl} TTL"
        )
    n_beats = int(gap_seconds // interval)
    session = SessionUsage(model=model)
    for _ in range(n_beats):
        session = session.with_request(
            RequestUsage(cache_read=context_tokens, cache_write=0, output=0, ttl=ttl)
        )
    return session
