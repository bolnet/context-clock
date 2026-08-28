# Cache-cost claim ledger

Validation of an internal engineering talk on **where the money actually goes in a Claude Code
session** (transcript, 2026-08-28). Every claim in that talk is recorded here with a verdict and
the evidence that settles it.

This file is the human-readable view. The **authority** is executable:

```bash
python -m context_clock.cachecost.verify              # the ledger, re-derived
python -m context_clock.cachecost.verify --scenarios  # + derived comparisons
pytest tests/test_cachecost_*.py                      # 87 tests, no network
```

Every numeric verdict is recomputed from `context_clock/cachecost/pricing.py` at print time. If a
published rate changes, the verdicts change with it — no number in this file is transcribed by hand
from a previous run.

## Provenance of every figure

| Label | Means | Where it comes from |
|---|---|---|
| **CONFIRMED** | Matches published rates or documented API semantics | Anthropic price card + prompt-caching docs |
| **CONSISTENT** | Not independently checkable, but the speaker's own figures reproduce to the stated result at published rates | arithmetic in `usage.py` |
| **REFINED** | True as far as it goes; the correction is recorded | as above |
| **ORG_SPECIFIC** | A fact about their deployment, not about the API | — |
| **UNVERIFIABLE** | Needs telemetry we do not have. Recorded, never asserted | — |
| **MISSING** | A mechanism the talk did not cover that changes the advice | prompt-caching docs |

Nothing in this module has been **measured** yet. The arithmetic and semantics layers are complete;
the live-measurement layer is not built. Derived scenario output is labelled *derived* wherever it
prints. Per the project rule in `CLAUDE.md`, no figure here may be presented as measured, and none is.

## The price card is independently confirmed

The verdicts all rest on one price card, which would be a single point of failure if it were a
lone transcription. OpenRouter publishes per-model rates for all four billing buckets on a public,
keyless endpoint:

```bash
python -m context_clock.cachecost.price_check
```

**15 rates across 3 models, 0 disagreements** — every input, output, cache-read, 5-minute-write and
1-hour-write rate matches. C1–C5 are confirmed by two independently maintained sources.

## The price card everything derives from

USD per million tokens. Cache multipliers are structural, not per-model: read `0.10x`,
5-minute write `1.25x`, 1-hour write `2.00x` of the base input rate.

| Model | Input | Output | Cache read | Write (5m) | Write (1h) |
|---|---|---|---|---|---|
| claude-opus-5 | $5.00 | $25.00 | $0.50 | $6.25 | $10.00 |
| claude-sonnet-5 | $2.00 | $10.00 | $0.20 | $2.50 | $4.00 |
| claude-haiku-4-5 | $1.00 | $5.00 | $0.10 | $1.25 | $2.00 |

## Verdict summary

30 claims: **15 CONFIRMED · 6 CONSISTENT · 3 REFINED · 1 ORG_SPECIFIC · 3 UNVERIFIABLE ·
2 MISSING**. 18 are numerically checked; all 18 reproduce.

**The talk holds up.** Every pricing and mechanism claim is correct, including the least obvious one
(C11). Three claims are true but incomplete in ways that change the advice, and two real mechanisms
are absent.

### CONFIRMED — matches published rates and documented semantics

| # | Claim | Evidence |
|---|---|---|
| C1 | Sonnet ~$2/MTok, Opus $5, Haiku $1 | Exact, not approximate |
| C2 | Output costs ~5x input | Exactly 5x on all three models |
| C3 | Cache reads $0.20/MTok, "a tenth of the cost" | Read = 0.10x input |
| C4 | Write is 1.25x at 5-minute TTL, 2x at 1-hour | Documented multipliers |
| C5 | Opus cache write is $6.25/MTok, not $5 | $5.00 x 1.25 |
| C6 | Models are stateless; the whole conversation is resent | The premise context-clock already measures |
| C7 | You pay per API request; 6 prompts became 31 requests | Prompt count is not a cost proxy |
| C8 | Editing CLAUDE.md or switching model blows the cache | Prefix match on exact bytes; entries are model-scoped. Also true of switching **effort** — not mentioned |
| C9 | "Three or four layers of caching" | Max 4 `cache_control` breakpoints per request |
| C11 | **The TTL clock starts when you send, not when the response lands** | Lifetime is measured from request *start*; generation time is spent inside the window. The sharpest claim in the talk and exactly right |
| C12 | A periodic no-op keeps the session warm | A cache read refreshes the timer for free |
| C20 | A miss at 275k on Sonnet costs ~70c | 275k x $2.50/MTok = $0.69 |
| C21 | The same miss on Opus costs ~$1.75 | 275k x $6.25/MTok = $1.72 |
| C27 | "A busy session is a cheap session" | Follows from the 12.5x write-to-read ratio |
| C28 | MCP tools are searched for, not all preloaded | Deferred tool loading keeps them out of the cached preamble |

### CONSISTENT — their own numbers reproduce

| # | Claim | Check |
|---|---|---|
| C13 | 277k context, 6M read + 308k written, paid ~$2 | 6.0M x $0.20 + 308k x $2.50 = **$1.97** |
| C14 | "Naive math says 50 cents" | 277k x $2.00/MTok = **$0.55**, understating the real bill ~3.6x |
| C15 | "Without caching we'd have paid ~$13" | 6.308M x $2.00/MTok = **$12.62** — caching took the session from $12.62 to $1.97, **6.4x cheaper, 84% saved** |
| C16 | First request: 38k cached, "almost 10 cents" | 38k x $2.50/MTok = **$0.095** |
| C18 | 418s wait: "5 cents becomes 80 cents" | 418s > the 300s TTL, so the prefix is rewritten. Self-consistent at a ~320k prefix: read $0.064 → rewrite $0.80 |
| C19 | Steady state settles at 6-7c/request | Reading a 277k prefix = $0.055, plus the turn's write and output |

### REFINED — true, with a correction that changes the advice

**C17 — "the second request costs 10 times less, writing 178 tokens and reading 3,000."**
The 10x is right and it is *structural*: it is the write-to-read ratio, `1.25x / 0.1x = 12.5x`. The
"3,000 tokens" does not fit his own numbers — re-reading the 38k prefix gives $0.008, i.e. 12x less
than 9.5 cents, which is the 10x he describes. Reading only 3,000 would have been ~95x less.

**C22 — "it's basically 10 reads for every cache write, so if you spun it 12 times you're paying
more than letting the cache expire."** The exact break-even is **12.5 reads**, so "10ish" and "12
times" bracket it correctly. At a 4-minute heartbeat that is **50 minutes** of keep-warm. But the
talk treats this as a two-way choice, and there is a third option:

| Idle gap | Cheapest strategy |
|---|---|
| under 5 min | nothing — normal traffic refreshes the entry for free |
| 5–30 min | heartbeat at 4-minute intervals |
| **30–60 min** | **switch that prefix to a 1-hour TTL** |
| over 60 min | take the miss |

The 1-hour TTL costs `0.75x` extra on the write, which equals only **7.5 reads** — so past ~30
minutes it beats heartbeating, and it does so without keeping an agent artificially busy.

**C23 — "we looked at turning the 1-hour TTL on globally, but it would lose us money."** Correct for
continuous traffic: a 5-minute entry refreshes free on every read, so the 1-hour TTL buys nothing
and doubles the write. But *globally* is the load-bearing word — see C22. The right policy is
per-prefix TTL selection, not one org-wide switch.

### ORG_SPECIFIC

**C10 — "we only support 5-minute caches."** A limit of their LLM proxy, not of the API;
`cache_control` accepts `ttl: "1h"`. This is what forecloses the C22/C23 middle option for them.

### UNVERIFIABLE — recorded, not asserted

**C24 — "$3.5M/year org-wide overpay."** Rests on one month of their telemetry. The projection
*model* is reproducible — `claims.annual_miss_overpay()` — so the sensitivity is visible instead of
implied. For scale: 500 engineers x 10 misses/day x 275k context on Sonnet ≈ $790k/yr; reaching
$3.5M needs roughly 4x that miss rate, an Opus-weighted mix, or a larger org.

**C25 — "every sub-agent costs almost 40k tokens to spin up."** Harness- and version-specific: the
preamble is system prompt + CLAUDE.md + skill definitions + tool schemas, which changes per release
and per repo. Their measurement of their setup, not a constant.

**C26 — "telling it to wait at most four minutes raises your cache rate to ~99%."** Follows
directionally from C11/C12, but 99% is a claim about their workload's timing distribution.

### MISSING — mechanisms the talk did not cover

**M1 — the 20-block lookback window.** Every miss in the talk is attributed to the 5-minute clock.
There is a second cause with the **identical 12.5x penalty and no clock involved**: a cache
breakpoint walks back at most **20 content blocks** looking for a prior entry. A single turn that
appends more than 20 blocks — routine in an agentic loop with many parallel tool calls — misses with
zero elapsed time. The four-minute rule does not help. An intermediate breakpoint every ~15 blocks
does. For a team whose headline advice is "never wait more than four minutes", this is the gap that
matters most: their heaviest agentic sessions are exactly the ones that miss for a reason the rule
cannot fix.

**M2 — you can tell the two kinds of miss apart.** `usage.cache_creation` splits writes by TTL
(`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`), which separates a clock-expiry miss
from a prefix-invalidation miss. Without that split, a C8 invalidation and a C18 expiry look
identical in the bill — and they have different fixes.

## How this connects to context-clock

The benchmark's existing result is that **input tokens grow O(n²)** because the whole conversation
is resent every turn. This ledger prices that curve: those resent tokens bill at `0.1x` while the
cache holds and at `1.25x` the moment it lapses, so the **same conversation costs 12.5x more per
turn** on the wrong side of a 5-minute boundary.

The two findings compose. Caching flattens the *dollar* curve while leaving the *token* curve
quadratic — which is why `CLAUDE.md` insists input tokens, not cost, is the hero metric. Retrieved
memory attacks the token curve itself: a flat ~200-token retrieval has almost nothing to re-read,
so it is both cheap to serve and nearly immune to the expiry cliff. Caching makes the quadratic
affordable; memory removes it.

## The live benchmark

Built, tested, and not yet run. It drives a **real agentic coding session** — a headless Minesweeper
engine across 6 user turns, with the agent writing files and running pytest until the suite is green
— and records the cache split of every API request.

```bash
python -m context_clock.cachecost.run --policy busy
python -m context_clock.cachecost.run --policy sawtooth --idle 420
```

Same task, same tokens, different timing. The gap between those two bills is the experiment.

Deliberately **not** the Snake game from the talk: reproducing their workload would test whether we
can copy a session, not whether the mechanism holds on a task nobody tuned for it.

Two providers, with an honest difference recorded in the data:

| | cache reads | cache writes | cost |
|---|---|---|---|
| **OpenRouter** (default) | reported (`cached_tokens`) | **derived** from billed cost | **real billed dollars** |
| **Anthropic** direct | reported | reported | derived from the price card |

OpenRouter's OpenAI-compatible usage block has no cache-write field, so writes are recovered
algebraically from the billed total — two equations, two unknowns, exact (`derive_writes`). The
trade is real and cuts both ways: OpenRouter gives a **measured** dollar figure and a derived token
split; the native API gives measured tokens and a derived dollar figure. Runs record which.

### Still unmeasured

C25 (sub-agent overhead), C26 (the 99% cache-rate claim) and the M1 penalty need a live run to
settle. `find_lookback_misses()` watches for the M1 signature during any session — a miss arriving
well inside the TTL after a turn that appended more than 20 content blocks. Observing that in real
work would be stronger evidence than provoking it with a synthetic probe.

**Blocker:** the `OPENROUTER_API_KEY` in `.env` is revoked — `GET /api/v1/key` returns
`401 {"message":"User not found."}`. Nothing can be measured until it is replaced.
