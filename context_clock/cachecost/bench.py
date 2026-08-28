"""Timing policies and the session runner.

The experiment: run the **identical** coding task under different timing, and
watch the bill move while the work stays the same. Same task, same tools, same
model, same token counts — only *when* the requests happen changes.

* ``busy``     — no artificial delay. The agent works straight through.
* ``sawtooth`` — idle past the TTL between user turns, the way a human who
  steps away between prompts does, or an agent blocked on a slow task.

Measured records are converted to the same ``SessionUsage`` the claim ledger
uses, so measured and derived sessions are priced by identical code.
"""

from __future__ import annotations

import csv
import time
from collections.abc import Callable
from pathlib import Path

from .agent import AgentRun
from .anthropic_provider import RequestRecord
from .pricing import TTL_5M_SECONDS
from .usage import RequestUsage, SessionUsage


def busy_policy() -> Callable[[int, int], None]:
    """Work straight through — every request starts inside the TTL."""

    def policy(turn: int, round_index: int) -> None:
        return None

    return policy


def sawtooth_policy(
    idle: float = TTL_5M_SECONDS + 120,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[int, int], None]:
    """Idle past the TTL before each new user turn.

    Only between turns, not between tool-call rounds: an agent mid-turn is
    generating continuously, and inserting waits there would model a workload
    nobody actually runs — and would multiply the wall clock by the request
    count instead of the turn count.
    """

    def policy(turn: int, round_index: int) -> None:
        if turn > 0 and round_index == 0:
            sleep(idle)

    return policy


POLICIES: dict[str, Callable[..., Callable[[int, int], None]]] = {
    "busy": busy_policy,
    "sawtooth": sawtooth_policy,
}


def to_session_usage(run: AgentRun, model: str, ttl: str = "5m") -> SessionUsage:
    """Price a measured run with the same arithmetic the ledger uses.

    ``was_cache_miss`` is only claimed where a request rewrote a prefix it
    should have been able to read — a warm entry that lapsed. The very first
    request writes because nothing is cached yet, which is the cost of entry,
    not a miss, and counting it as one would inflate every miss statistic.
    """
    session = SessionUsage(model=model)
    for record in run.records:
        session = session.with_request(
            RequestUsage(
                cache_read=record.cache_read,
                cache_write=record.cache_creation,
                uncached_input=record.input_tokens,
                output=record.output_tokens,
                ttl=ttl,
                was_cache_miss=(
                    record.index > 0 and record.cache_read == 0 and record.cache_creation > 0
                ),
            )
        )
    return session


def write_records_csv(run: AgentRun, path: str | Path) -> Path:
    """Persist the per-request rows — the raw evidence behind every figure."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "index", "turn", "gap_s", "latency_s", "blocks_added",
                "cache_write", "cache_read", "uncached_input", "output",
                "prompt_tokens", "stop_reason", "tool_calls",
            ]
        )
        for r in run.records:
            writer.writerow(
                [
                    r.index, r.turn, f"{r.gap:.2f}", f"{r.latency:.2f}", r.blocks_added,
                    r.cache_creation, r.cache_read, r.input_tokens, r.output_tokens,
                    r.prompt_tokens, r.stop_reason, "|".join(r.tool_calls),
                ]
            )
    return target


def summarize(run: AgentRun, model: str) -> str:
    """A human-readable account of where this session's money went."""
    session = to_session_usage(run, model)
    lines = [
        f"task={run.task}  model={model}  policy={run.policy}",
        f"  {run.n_turns} user turns -> {run.n_requests} API requests"
        f"   ({run.wall_clock / 60:.1f} min wall clock)",
        f"  tests pass: {run.tests_passed}",
        "",
        f"  cache writes    {session.cache_write_tokens:>12,} tok",
        f"  cache reads     {session.cache_read_tokens:>12,} tok",
        f"  uncached input  {session.uncached_input_tokens:>12,} tok",
        f"  output          {session.output_tokens:>12,} tok",
        f"  final context   {session.final_context_tokens:>12,} tok",
        "",
        f"  cache hit rate  {session.cache_hit_rate:>11.1%}",
        f"  cache misses    {session.n_cache_misses:>11} / {session.n_requests}",
        "",
        f"  billed          ${session.cost:>11.4f}",
        f"  without caching ${session.uncached_cost:>11.4f}"
        f"   ({session.cache_savings_multiple:.1f}x)",
        f"  naive estimate  ${session.naive_cost:>11.4f}"
        f"   (understates {session.naive_underestimate_multiple:.1f}x)",
    ]
    return "\n".join(lines)


def find_lookback_misses(run: AgentRun, ttl_seconds: float = TTL_5M_SECONDS) -> list[RequestRecord]:
    """Requests that missed the cache with time to spare — the M1 signature.

    A miss whose preceding turn appended more than 20 content blocks, arriving
    well inside the TTL, cannot be explained by the clock. That is the
    mechanism the talk never mentions, observed in a real session rather than
    provoked by a synthetic probe.
    """
    misses = []
    for previous, current in zip(run.records, run.records[1:]):
        clock_safe = current.gap < ttl_seconds * 0.8
        missed = current.cache_read == 0 and current.cache_creation > 0
        if missed and clock_safe and previous.blocks_added > 20:
            misses.append(current)
    return misses
