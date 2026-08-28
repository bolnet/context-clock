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
                "index", "turn", "elapsed_s", "gap_s", "latency_s",
                # context, per datapoint
                "context_tokens", "cumulative_tokens", "n_messages", "n_blocks",
                "blocks_added", "history_chars",
                # billing split
                "cache_write", "cache_read", "uncached_input", "output",
                "cost_usd", "cumulative_cost_usd",
                "stop_reason", "tool_calls",
            ]
        )
        for r in run.records:
            writer.writerow(
                [
                    r.index, r.turn, f"{r.elapsed:.2f}", f"{r.gap:.2f}", f"{r.latency:.2f}",
                    r.context_tokens, r.cumulative_tokens, r.n_messages, r.n_blocks,
                    r.blocks_added, r.history_chars,
                    r.cache_creation, r.cache_read, r.input_tokens, r.output_tokens,
                    "" if r.cost is None else f"{r.cost:.8f}", f"{r.cumulative_cost:.8f}",
                    r.stop_reason, "|".join(r.tool_calls),
                ]
            )
    return target


def summarize(run: AgentRun, model: str) -> str:
    """A human-readable account of where this session's money went.

    Cost leads, because that is what this benchmark exists to measure. The
    token buckets are shown as dollars first and tokens second, since the
    buckets differ 12.5x in price and a token count alone hides that. Context
    is reported last as supporting detail — it explains the shape of the bill,
    it is not the headline.
    """
    session = to_session_usage(run, model)
    card = session.card
    billed = run.measured_cost

    def money(cost: float, tokens: int, label: str, rate: float) -> str:
        share = cost / session.cost if session.cost else 0.0
        return (
            f"    {label:<16} ${cost:>9.4f}  {share:>5.1%}   "
            f"{tokens:>12,} tok @ ${rate:>5.2f}/Mtok"
        )

    write_cost = session.cache_write_tokens * card.cache_write_5m_per_mtok / 1e6
    read_cost = session.cache_read_tokens * card.cache_read_per_mtok / 1e6
    uncached_cost = session.uncached_input_tokens * card.input_per_mtok / 1e6
    output_cost = session.output_tokens * card.output_per_mtok / 1e6

    lines = [
        f"task={run.task}  model={model}  policy={run.policy}",
        f"  {run.n_turns} user turns -> {run.n_requests} API requests"
        f"   ({run.wall_clock / 60:.1f} min wall clock)   tests pass: {run.tests_passed}"
        + ("   [WINDOW EXHAUSTED — session outgrew the model's context limit]"
           if run.window_exhausted else ""),
        "",
        "  COST",
        (f"    billed              ${billed:>9.4f}   measured, from the provider"
         if billed is not None else
         "    billed                    n/a   (provider reported no cost)"),
        f"    priced              ${session.cost:>9.4f}   our price card",
    ]
    if billed is not None and session.cost:
        drift = abs(billed - session.cost) / session.cost
        lines.append(f"    agreement           {1 - drift:>9.2%}   measured vs priced")
    lines += [
        "",
        f"    without caching     ${session.uncached_cost:>9.4f}   "
        f"caching saved {session.cache_savings_multiple:.1f}x "
        f"({1 - session.cost / session.uncached_cost:.0%})",
        f"    naive estimate      ${session.naive_cost:>9.4f}   "
        f"understates the bill {session.naive_underestimate_multiple:.1f}x",
        "",
        "  WHERE IT WENT",
        money(write_cost, session.cache_write_tokens, "cache writes", card.cache_write_5m_per_mtok),
        money(read_cost, session.cache_read_tokens, "cache reads", card.cache_read_per_mtok),
        money(uncached_cost, session.uncached_input_tokens, "uncached input", card.input_per_mtok),
        money(output_cost, session.output_tokens, "output", card.output_per_mtok),
        "",
        f"    cache hit rate      {session.cache_hit_rate:>9.1%}   of input tokens",
        f"    cache misses        {session.n_cache_misses:>9} / {session.n_requests} requests",
        f"    cost per request    ${session.cost / session.n_requests:>9.4f}   average"
        if session.n_requests else "",
        "",
        "  CONTEXT (supporting detail)",
        f"    peak context        {run.peak_context:>9,} tok   the context-meter number",
        f"    cumulative          {run.cumulative_tokens:>9,} tok   every token ever read or written",
        f"    re-read factor      {run.cumulative_tokens / run.peak_context:>9.1f}x   "
        "how many times the context was paid for"
        if run.peak_context else "",
    ]
    return "\n".join(line for line in lines if line != "")


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
