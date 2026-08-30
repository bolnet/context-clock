"""The claim ledger — every assertion from the talk, with an executable verdict.

Source: an internal engineering talk on where the money goes in a Claude Code
session (transcript, 2026-08-28). Each claim carries the quote, a verdict, the
evidence that settles it, and — wherever the claim is numeric — a ``check``
that re-derives the number from ``pricing``/``usage`` so the verdict cannot
drift away from the price card.

Verdicts
--------
``CONFIRMED``    Matches published rates or documented semantics.
``CONSISTENT``   Not independently checkable, but the speaker's own figures
                 reproduce to the stated result under the published rates.
``REFINED``      True as far as it goes; the ledger records the correction.
``ORG_SPECIFIC`` A fact about their deployment, not about the API.
``UNVERIFIABLE`` Needs data we do not have. Recorded, never asserted.
``MISSING``      A mechanism the talk did not cover that changes the advice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .pricing import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_1H_MULTIPLIER,
    CACHE_WRITE_5M_MULTIPLIER,
    MAX_BREAKPOINTS,
    price_card,
    reads_per_write,
)
from .usage import RequestUsage, SessionUsage, hit_cost, miss_penalty

CONFIRMED = "CONFIRMED"
CONSISTENT = "CONSISTENT"
REFINED = "REFINED"
ORG_SPECIFIC = "ORG_SPECIFIC"
UNVERIFIABLE = "UNVERIFIABLE"
MISSING = "MISSING"


@dataclass(frozen=True)
class Claim:
    """One assertion under test."""

    id: str
    topic: str
    quote: str
    verdict: str
    evidence: str
    #: Returns (derived_value, stated_value) for numeric claims; None otherwise.
    check: Callable[[], tuple[float, float]] | None = None
    tolerance: float = 0.05  # relative, for the derived-vs-stated comparison

    def evaluate(self) -> tuple[float, float] | None:
        return self.check() if self.check else None

    def holds(self) -> bool:
        """True when the derived number reproduces the stated one in tolerance."""
        result = self.evaluate()
        if result is None:
            return True
        derived, stated = result
        if stated == 0:
            return derived == 0
        return abs(derived - stated) / abs(stated) <= self.tolerance


# --- The session the speaker measured, as they reported it ------------------
# 6 prompts -> 31 API requests, ~277k final context, 6M cache-read tokens,
# ~308k cache-write tokens, ~$2 billed, on Sonnet.
REPORTED_SESSION = SessionUsage(
    model="claude-sonnet-5",
    requests=(
        RequestUsage(cache_read=6_000_000, cache_write=308_000, output=0),
        # The final prompt is what the context meter showed the user.
        RequestUsage(cache_read=277_000, cache_write=0, output=0),
    ),
)


def _session_billed() -> tuple[float, float]:
    card = price_card("claude-sonnet-5")
    derived = (6_000_000 * card.cache_read_per_mtok + 308_000 * card.cache_write_5m_per_mtok) / 1e6
    return derived, 2.00


def _session_uncached() -> tuple[float, float]:
    card = price_card("claude-sonnet-5")
    return (6_308_000 * card.input_per_mtok) / 1e6, 13.00


def _naive_estimate() -> tuple[float, float]:
    card = price_card("claude-sonnet-5")
    return (277_000 * card.input_per_mtok) / 1e6, 0.50


CLAIMS: tuple[Claim, ...] = (
    Claim(
        id="C1",
        topic="List prices",
        quote="Sonnet costs about $2 for a million tokens, Opus costs 5, Haiku costs 1.",
        verdict=CONFIRMED,
        evidence="Published input rates: Sonnet 5 $2.00/MTok, Opus 5 $5.00/MTok, "
        "Haiku 4.5 $1.00/MTok. Exact, not approximate.",
        check=lambda: (price_card("claude-opus-5").input_per_mtok, 5.00),
    ),
    Claim(
        id="C2",
        topic="Output pricing",
        quote="The output tokens cost usually about 5x in price.",
        verdict=CONFIRMED,
        evidence="Output is exactly 5x input on all three: $10/$2, $25/$5, $5/$1.",
        check=lambda: (
            price_card("claude-sonnet-5").output_per_mtok
            / price_card("claude-sonnet-5").input_per_mtok,
            5.0,
        ),
    ),
    Claim(
        id="C3",
        topic="Cache read rate",
        quote="For the cache reads you're paying $0.20 per million. It's usually a tenth "
        "of the cost.",
        verdict=CONFIRMED,
        evidence="Cache read = 0.10x base input. On Sonnet 5 that is $0.20/MTok.",
        check=lambda: (price_card("claude-sonnet-5").cache_read_per_mtok, 0.20),
    ),
    Claim(
        id="C4",
        topic="Cache write rate",
        quote="You're paying 1.25 if it's 5 minute and 2 for one hour.",
        verdict=CONFIRMED,
        evidence="Cache write multipliers are 1.25x (5-minute TTL) and 2.00x (1-hour TTL) "
        "of the base input rate. The figures are multipliers, not $/MTok.",
        check=lambda: (CACHE_WRITE_5M_MULTIPLIER + CACHE_WRITE_1H_MULTIPLIER, 3.25),
    ),
    Claim(
        id="C5",
        topic="Opus cache write",
        quote="You're paying 6.25 per million tokens, not $5 per million tokens.",
        verdict=CONFIRMED,
        evidence="Opus 5 input $5.00 x 1.25 (5-minute write) = $6.25/MTok.",
        check=lambda: (price_card("claude-opus-5").cache_write_5m_per_mtok, 6.25),
    ),
    Claim(
        id="C6",
        topic="Statelessness",
        quote="LLM models are completely stateless, they don't really remember anything "
        "unless there's caching.",
        verdict=CONFIRMED,
        evidence="The Messages API is stateless; the full conversation is resent on every "
        "request. This is the same O(n^2) input growth context-clock measures.",
    ),
    Claim(
        id="C7",
        topic="Billing unit",
        quote="You're not paying per prompt, per hour, per minute — you're paying for each "
        "API request. Six prompts turned into 31 API requests.",
        verdict=CONFIRMED,
        evidence="Billing is per request. One user prompt fans out to as many requests as "
        "the agent loop makes tool calls, so prompt count is not a cost proxy.",
    ),
    Claim(
        id="C8",
        topic="Cache invalidation",
        quote="Changing your CLAUDE.md or changing the model during the conversation will "
        "blow away your cache.",
        verdict=CONFIRMED,
        evidence="Caching is a prefix match on exact bytes: any change invalidates every "
        "breakpoint at or after it, and CLAUDE.md sits in the prefix. Cache entries are "
        "also model-scoped. Not mentioned but equally invalidating: changing effort.",
    ),
    Claim(
        id="C9",
        topic="Cache layers",
        quote="There's three or four different layers of caching.",
        verdict=CONFIRMED,
        evidence=f"The API accepts at most {MAX_BREAKPOINTS} cache_control breakpoints per "
        "request, so 'three or four layers' is the documented ceiling.",
        check=lambda: (float(MAX_BREAKPOINTS), 4.0),
    ),
    Claim(
        id="C10",
        topic="TTL availability",
        quote="Right now we only support 5 minute caches for Anthropic models.",
        verdict=ORG_SPECIFIC,
        evidence="A limit of their LLM proxy, not of the API: cache_control accepts "
        "ttl='1h'. See C22 for when the 1-hour TTL actually wins.",
    ),
    Claim(
        id="C11",
        topic="TTL clock start",
        quote="That five minute timer starts from the time you send your message. If it "
        "streams for three minutes, you only have two minutes to type the next question.",
        verdict=CONFIRMED,
        evidence="The entry's lifetime is measured from the start of the request that "
        "writes or reads it, so generation time is spent inside the window. The sharpest "
        "and least obvious claim in the talk, and it is exactly right.",
    ),
    Claim(
        id="C12",
        topic="Read refreshes TTL",
        quote="Just run a timer every four minutes, wake up and say hi — it keeps the "
        "session warm.",
        verdict=CONFIRMED,
        evidence="A cache read refreshes the entry's timer at no extra charge, so requests "
        "starting less than 5 minutes apart keep a 5-minute entry alive indefinitely. "
        "Whether it is worth doing is a separate question — see C22.",
    ),
    Claim(
        id="C13",
        topic="Session total",
        quote="Our conversation went to 277,000 tokens, but we used 6 million tokens from "
        "cache and wrote 308,000 — and we paid about $2.",
        verdict=CONSISTENT,
        evidence="6.0M x $0.20/MTok + 308k x $2.50/MTok = $1.97. Reproduces $2 from their "
        "own figures at published Sonnet rates.",
        check=_session_billed,
    ),
    Claim(
        id="C14",
        topic="Naive estimate",
        quote="The naive math tells us 277k tokens, a million is $2, I should be spending "
        "50 cents on this conversation.",
        verdict=CONSISTENT,
        evidence="277k x $2.00/MTok = $0.55, which he rounded to 'about 50 cents'. The "
        "context meter reports one request's prompt; the bill sums every request, so the "
        "estimate understates the real $1.97 by ~3.6x.",
        check=_naive_estimate,
        tolerance=0.15,  # he said "50 cents" for $0.55 — a spoken round number
    ),
    Claim(
        id="C15",
        topic="Value of caching",
        quote="If we did not have any caching and just kept paying the $2 rate, we'd have "
        "paid about $13.",
        verdict=CONSISTENT,
        evidence="(6.0M read + 308k written) x $2.00/MTok = $12.62. Caching took the same "
        "session from $12.62 to $1.97 — 6.4x cheaper, 84% saved.",
        check=_session_uncached,
    ),
    Claim(
        id="C16",
        topic="First request",
        quote="That's where the 38,000 tokens went, and that's what makes the first "
        "request cost almost 10 cents.",
        verdict=CONSISTENT,
        evidence="38k x $2.50/MTok (5-minute write) = $0.095 — 'almost 10 cents' exactly.",
        check=lambda: (miss_penalty(38_000, "claude-sonnet-5"), 0.10),
        tolerance=0.10,  # the claim is stated as "almost", not as equality
    ),
    Claim(
        id="C17",
        topic="Second request",
        quote="On the second request we're paying pretty much 10 times less, because we're "
        "only writing 178 tokens and reading the 3,000 we already cached.",
        verdict=REFINED,
        evidence="The 10x drop is right and structural — it is the write-to-read ratio "
        "(1.25x / 0.1x = 12.5x). The '3,000 tokens' does not fit: re-reading the 38k "
        "prefix gives $0.0076 + $0.0004 = $0.008, i.e. 12x less than 9.5 cents, which is "
        "the 10x he describes. Reading only 3,000 would be ~95x less.",
        check=lambda: (
            miss_penalty(38_000, "claude-sonnet-5")
            / (hit_cost(38_000, "claude-sonnet-5") + miss_penalty(178, "claude-sonnet-5")),
            12.0,
        ),
        tolerance=0.15,
    ),
    Claim(
        id="C18",
        topic="Expiry cliff",
        quote="Because we waited 418 seconds we missed the cache — instead of paying 5 "
        "cents we suddenly pay 80 cents.",
        verdict=CONSISTENT,
        evidence="418s > the 300s TTL, so the whole prefix is rewritten. The 16x jump "
        "brackets the structural 12.5x plus the turn's new tokens. Self-consistent at a "
        "~320k prefix: read $0.064, rewrite $0.80.",
        check=lambda: (
            miss_penalty(320_000, "claude-sonnet-5"), 0.80
        ),
    ),
    Claim(
        id="C19",
        topic="Steady state",
        quote="The price per request stabilizes around 6, 7 cents.",
        verdict=CONSISTENT,
        evidence="Reading a 277k prefix costs 277k x $0.20/MTok = $0.055, plus the turn's "
        "write and output — 6-7 cents.",
        check=lambda: (hit_cost(277_000, "claude-sonnet-5"), 0.055),
        tolerance=0.10,
    ),
    Claim(
        id="C20",
        topic="Miss cost, Sonnet",
        quote="If we had this five minute break, then on Sonnet for the next message we "
        "would have paid about 70 cents.",
        verdict=CONFIRMED,
        evidence="275k x $2.50/MTok = $0.69.",
        check=lambda: (miss_penalty(275_000, "claude-sonnet-5"), 0.70),
    ),
    Claim(
        id="C21",
        topic="Miss cost, Opus",
        quote="If we used Opus and were also 275k tokens in, we would pay about $1.75.",
        verdict=CONFIRMED,
        evidence="275k x $6.25/MTok = $1.72.",
        check=lambda: (miss_penalty(275_000, "claude-opus-5"), 1.75),
    ),
    Claim(
        id="C22",
        topic="Heartbeat break-even",
        quote="It's basically 10 reads for every cache write. So if you spun it 12 times "
        "you're actually paying more than just letting the cache expire.",
        verdict=REFINED,
        evidence="The exact break-even is 1.25x / 0.1x = 12.5 reads, so '10ish' and "
        "'12 times' bracket it correctly. At a 4-minute heartbeat that is 50 minutes of "
        "keep-warm — past ~50 minutes idle, take the miss. The talk missed the third "
        "option: a 1-hour TTL costs 0.75x extra on the write, which equals only 7.5 "
        "reads, so for gaps between ~30 and ~60 minutes the 1-hour TTL beats both "
        "heartbeating and eating the miss.",
        check=lambda: (reads_per_write("5m"), 12.5),
    ),
    Claim(
        id="C23",
        topic="1-hour TTL globally",
        quote="We did look into turning on the 1 hour TTL cache globally, but it actually "
        "would lose us money.",
        verdict=REFINED,
        evidence="Correct for continuous traffic: a 5-minute entry refreshes free on every "
        "read, so the 1-hour TTL buys nothing and doubles the write (2x vs 1.25x). It is "
        "wrong as a universal rule — see C22. 'Globally' is the load-bearing word; per-"
        "prefix TTL selection is the right policy, not a single org-wide switch.",
        check=lambda: (
            CACHE_WRITE_1H_MULTIPLIER / CACHE_WRITE_5M_MULTIPLIER, 1.6
        ),
    ),
    Claim(
        id="C24",
        topic="Org projection",
        quote="If we scale this up to our entire engineering org, we're going to pay about "
        "three and a half extra million dollars over the next year.",
        verdict=UNVERIFIABLE,
        evidence="Rests on one month of their internal telemetry, which we do not have. "
        "The projection *model* is reproducible — see ``annual_miss_overpay`` — so anyone "
        "can substitute their own miss count and context size and check the shape.",
    ),
    Claim(
        id="C25",
        topic="Sub-agent overhead",
        quote="For every sub agent you're still paying this almost 40k cost of spinning "
        "out the sub agents.",
        verdict=UNVERIFIABLE,
        evidence="Harness-specific and version-specific: the preamble is system prompt + "
        "CLAUDE.md + skill definitions + tool schemas, which changes release to release "
        "and per repo. The 38k figure is their measurement of their setup, not a constant. "
        "Measurable locally from session transcripts.",
    ),
    Claim(
        id="C26",
        topic="Four-minute rule",
        quote="By just telling it to wait four minutes at most, you'll raise your cache "
        "rate to like 99% in some cases.",
        verdict=UNVERIFIABLE,
        evidence="Directionally sound and it follows from C11/C12, but '99%' is a claim "
        "about their workload's timing distribution. The benchmark's continuous vs "
        "sawtooth scenarios bound the achievable range for a given shape.",
    ),
    Claim(
        id="C27",
        topic="Busy is cheap",
        quote="A busy session is typically a cheap session.",
        verdict=CONFIRMED,
        evidence="Follows from the 12.5x write-to-read ratio: keeping every request inside "
        "the TTL bills the prefix at 0.1x instead of 1.25x. Note the corollary the talk "
        "states correctly — a pure question-and-answer session on a large context is fine; "
        "it is idle gaps, not context size, that cost money.",
        check=lambda: (reads_per_write("5m"), 12.5),
    ),
    Claim(
        id="C28",
        topic="MCP loading",
        quote="I think right now the MCPs will not get loaded at all. They're going to be "
        "searched for.",
        verdict=CONFIRMED,
        evidence="Matches deferred tool loading: MCP tool schemas are not all resident in "
        "the prefix; they are fetched on demand via tool search. This keeps them out of "
        "the cached preamble, so it lowers rather than raises the per-request floor.",
    ),
    Claim(
        id="C29",
        topic="Unbounded fan-out",
        quote="Your six prompts can easily turn into 31 API requests. They can also turn "
        "into 3,000 API requests. Sometimes it can go on for 20 hours from one prompt.",
        verdict=CONFIRMED,
        evidence="Nothing bounds the loop but the task and the autonomy granted. This is "
        "why prompt count is a useless cost proxy and request count is the unit that "
        "matters — the benchmark records both, and its own runs show the ratio moving.",
    ),
    Claim(
        id="C30",
        topic="Per-request cost is linear",
        quote="You settle into this linear payment for each request — about 6 cents — and "
        "it just keeps increasing slowly and slowly.",
        verdict=CONFIRMED,
        evidence="Exactly right, and it is the bridge to what context-clock already "
        "measures. A warm request bills its whole prefix at the read rate, so per-request "
        "cost is linear in context; context grows by roughly a constant per turn; summing "
        "a linear series over n turns is **quadratic cumulative spend**. Caching lowers "
        "the constant by 12.5x. It does not change the exponent — that is what retrieved "
        "memory does.",
    ),
    Claim(
        id="C31",
        topic="Partial cache survival",
        quote="We're still going to have a cache read, because the initial layer of the "
        "system prompt and the project context might live on, but our conversation "
        "suddenly becomes uncached.",
        verdict=CONFIRMED,
        evidence="With multiple breakpoints, an expiry does not have to be all-or-nothing: "
        "an earlier entry can still be read while a later one is rewritten. So a miss "
        "shows up as reads collapsing to the size of the surviving prefix, not to zero — "
        "which is why the benchmark records read and write per request rather than a "
        "hit/miss flag.",
    ),
    Claim(
        id="C32",
        topic="Write-heavy start, read-heavy tail",
        quote="Initially we have a bunch of cache writes because it's making calls and "
        "creating files, and then eventually it verifies the game works and doesn't have "
        "to put that back into its cache.",
        verdict=CONFIRMED,
        evidence="Directly observable in a real session: early turns append large tool "
        "results (files written, tests run) so writes dominate; later turns append little "
        "and read a large prefix, so reads dominate. The benchmark's per-request CSV shows "
        "the crossover.",
    ),
    Claim(
        id="C33",
        topic="Restart vs carry",
        quote="Depending on how big the conversation becomes after, let's say, two hours, "
        "it might make sense to just kill that and start a new one. You pay the 38k "
        "penalty again, but it might be cheaper than repaying whatever accumulated.",
        verdict=CONFIRMED,
        evidence="The break-even is exact: restarting costs one preamble rewrite, carrying "
        "on costs a rewrite of the accumulated prefix on the next miss. Restart wins once "
        "the conversation exceeds the preamble — i.e. almost immediately, if you are going "
        "to miss anyway. See ``restart_beats_carrying``.",
        check=lambda: (
            miss_penalty(38_000, "claude-sonnet-5") / miss_penalty(275_000, "claude-sonnet-5"),
            0.138,
        ),
        tolerance=0.05,
    ),
    Claim(
        id="C34",
        topic="Small sub-agent contexts",
        quote="By keeping those conversations fairly small you avoid the cache read "
        "penalty when you get into a 500k window where you end up paying a bunch of money "
        "just for cache reads.",
        verdict=REFINED,
        evidence="The economics are right — read cost is linear in prefix size, so ten "
        "50k sub-agents read far less than one 500k agent — but 'penalty' is the wrong "
        "word for a cache read, which is the cheap path at 0.1x. The real trade is against "
        "C25: each sub-agent re-pays its own preamble, so fan-out wins only when each "
        "sub-task is substantially larger than the preamble it costs to start.",
    ),
    Claim(
        id="C35",
        topic="Blocked main agent",
        quote="You keep your main agent occupied by just this synthetic 'hey, are you "
        "alive' until the sub agents are done — otherwise the main agent will blow past "
        "the cache window.",
        verdict=CONFIRMED,
        evidence="A main agent waiting on sub-agents issues no requests, so its entry ages "
        "out exactly like an idle human's. Same mechanism as C11, different cause, and it "
        "is the one people do not anticipate because the session looks busy.",
    ),
    Claim(
        id="C36",
        topic="Keep-alive duration",
        quote="It basically keeps it alive for 20 to 25 minutes ... a very small cache "
        "read every four minutes.",
        verdict=REFINED,
        evidence="The mechanism works indefinitely, not for 20-25 minutes — a read "
        "refreshes the timer every time (C12). The 20-25 minute figure is the sensible "
        "*budget*, not a limit, and it sits just inside the 12.5-read break-even from C22 "
        "(~50 minutes). The tool is right; the stated reason is not.",
    ),
    Claim(
        id="C37",
        topic="Periodic loop economics",
        quote="If I have a loop that runs every 30 minutes for six hours, does it make "
        "sense to keep the cache warm through the 20 minutes of downtime?",
        verdict=REFINED,
        evidence="Answerable exactly, and the talk left it at 'some math here'. Bridging a "
        "25-minute gap costs ~6 refresh reads (0.6x a prefix) against a miss at 1.25x, so "
        "heartbeating wins per gap. Over 12 gaps in six hours it stays ahead — but a "
        "1-hour TTL beats both (C22), and the conversation should be reset between "
        "firings anyway (C33) rather than carried for six hours.",
        check=lambda: (reads_per_write("5m"), 12.5),
    ),
    Claim(
        id="C38",
        topic="Cheaper model for watching",
        quote="If you're watching the deployment, see if you can try a cheaper model than "
        "Opus, like Sonnet — then the costs are just a lot cheaper.",
        verdict=CONFIRMED,
        evidence="Sonnet 5 is 2.5x cheaper than Opus 5 in every bucket, and the ratio holds "
        "for cache reads and writes because the multipliers are structural. For a watching "
        "loop, which is nearly all cache reads, the saving is the full 2.5x.",
        check=lambda: (
            price_card("claude-opus-5").cache_read_per_mtok
            / price_card("claude-sonnet-5").cache_read_per_mtok,
            2.5,
        ),
    ),
    Claim(
        id="C39",
        topic="Opus 5 context appetite",
        quote="A lot of my conversations are getting into like 500k plus tokens, which is "
        "not super normal for me.",
        verdict=UNVERIFIABLE,
        evidence="One engineer's impression of one model over an unstated period, with no "
        "baseline. Recorded because it drove advice in the room, not because it is "
        "evidence. Worth noting that if true it multiplies every miss cost in C20/C21.",
    ),
    Claim(
        id="C40",
        topic="Non-Anthropic pricing",
        quote="Recently I started using flash 3.7 more from cursor ... it's 0.75 per "
        "million and I think the caching is at least half an hour.",
        verdict=UNVERIFIABLE,
        evidence="Out of scope: not an Anthropic model, and this benchmark only prices "
        "models it holds a verified card for. Recorded so the ledger is complete rather "
        "than silently dropping a cost claim it cannot check.",
    ),
    Claim(
        id="M1",
        topic="20-block lookback",
        quote="(not covered in the talk)",
        verdict=MISSING,
        evidence="Every miss in the talk is attributed to the 5-minute clock. There is a "
        "second, time-independent cause with the identical 12.5x penalty: a cache "
        "breakpoint walks back at most 20 content blocks, so a single turn that appends "
        "more than 20 blocks — routine in an agentic loop with many parallel tool calls — "
        "misses with zero elapsed time. The four-minute rule does not help; an "
        "intermediate breakpoint every ~15 blocks does.",
    ),
    Claim(
        id="M2",
        topic="Cache-miss diagnosis",
        quote="(not covered in the talk)",
        verdict=MISSING,
        evidence="usage.cache_creation splits writes by TTL "
        "(ephemeral_5m_input_tokens / ephemeral_1h_input_tokens), which separates a "
        "clock-expiry miss from a prefix-invalidation miss. Without that split, C8-style "
        "invalidation and C18-style expiry look identical in the bill.",
    ),
)


def restart_beats_carrying(
    *, conversation_tokens: int, preamble_tokens: int, model: str, ttl: str = "5m"
) -> bool:
    """Whether killing the session and re-paying the preamble beats carrying it (C33).

    Both sides are a cache write; only the size differs. Restarting wins as soon
    as the accumulated conversation exceeds the preamble it costs to rebuild.
    """
    if conversation_tokens < 0 or preamble_tokens < 0:
        raise ValueError("token counts must be non-negative")
    return miss_penalty(preamble_tokens, model, ttl) < miss_penalty(
        conversation_tokens, model, ttl
    )


def annual_miss_overpay(
    *,
    engineers: int,
    misses_per_engineer_per_day: float,
    avg_context_tokens: int,
    model: str,
    working_days: int = 250,
) -> float:
    """Reproduce the shape of the org-wide projection in C24, with your own inputs.

    Returns the annual USD *overpay* — the rewrite cost of every expired prefix,
    net of what reading it would have cost. The talk's $3.5M is not reproducible
    without their telemetry; this makes the model explicit so the sensitivity is
    visible instead of implied.
    """
    if engineers < 0 or misses_per_engineer_per_day < 0 or avg_context_tokens < 0:
        raise ValueError("projection inputs must be non-negative")
    per_miss = miss_penalty(avg_context_tokens, model) - hit_cost(avg_context_tokens, model)
    return per_miss * misses_per_engineer_per_day * engineers * working_days
