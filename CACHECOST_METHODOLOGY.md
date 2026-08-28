# cachecost — how the prompt-cache benchmark actually works

*Methodology log for `context_clock/cachecost/`. Written 2026-08-28. Every mechanism
below is described from the code that implements it, and every number quoted is from a
recorded run in `BENCHMARK_LOG.md`. Nothing here is estimated.*

---

## 1. The question

The main context-clock benchmark measures **tokens**. cachecost measures **dollars**, and
it exists because those two are not the same story once prompt caching is switched on.

The specific question: *when an agent works through a long coding session, where does the
money actually go, and how wrong is the number you'd guess from the context meter?*

The intuitive estimate is "context size × input rate". That estimate is wrong in both
directions at once, and the benchmark quantifies both errors:

- It **understates**, because you pay for the context once per API request, not once per
  session. A 6-turn session is not 6 requests.
- It **overstates the input rate**, because a cached prefix bills at 0.1× the input rate,
  not 1.0×.

On the recorded run 1 those two errors netted out to a naive estimate of **$0.1043**
against a real bill of **$0.8992** — understating by **8.6×**.

---

## 2. The workload — a real coding session, not a synthetic prompt

The measurement needs a conversation that grows the way a real agent session grows: a
long prefix, tool results piling up *inside* a single user turn, and a finish line the
harness can verify without asking the model.

**The task is a headless Minesweeper engine** (`tasks.py`). Six scripted user turns:

| Turn | Ask |
|---|---|
| 0 | Build `minesweeper.py` — `Board(width, height, mine_count, seed)`, `adjacent()`, `reveal()` with flood fill, plus tests |
| 1 | Add `chord()` (reveal unflagged neighbours when flags match the count) + 3 tests |
| 2 | Add `render()` returning the board as a multi-line string + 3 tests |
| 3 | Add `Board.from_layout(rows)` so tests can pin an exact layout; rewrite ≥3 existing tests to use it |
| 4 | Add `solve_step()` — the two basic Minesweeper deductions, once + 3 tests |
| 5 | Review the module: docstrings, coordinate validation on every public method, any missing edge-case test |

Why this task, in the source's own words: pure logic so a **test suite is the ground
truth for "done"** (not a screenshot, and not the agent's own claim); seeded mine
placement so runs are reproducible; and flood fill + adjacency + flagging is enough real
work to take several turns and many tool calls — *which is the point, because that is
what fills a cached prefix*.

It is **deliberately not** the Snake game from the talk this benchmark validates.
Reproducing their exact workload would test whether we can copy a session, not whether
the mechanism holds on a task nobody tuned for it.

### The agent is real, and so are the tools

The model gets four tools (`tools.py`), and they do real work against a real directory:

- `write_file` — create/overwrite (the system prompt requires whole files, so every edit
  re-sends the entire module — a real driver of token growth)
- `read_file` — capped at 40,000 bytes
- `list_files`
- `run_tests` — a **real pytest subprocess** in the workspace, fixed argv (never a shell
  string), 120s timeout, output tail-capped at 40,000 bytes

The workspace is sandboxed: paths are resolved and anything escaping the root is
rejected rather than normalised, and only `.py/.txt/.md/.cfg/.toml/.ini` extensions are
writable. The model is treated as an untrusted caller, because it is one.

**Ground truth for completion** is `workspace.tests_pass()` — the harness runs pytest
itself at the end and checks for exit code 0. The agent is told to reply `TASK COMPLETE`,
but that string is never what decides whether the run counted.

---

## 3. The loop — where "6 prompts" becomes "33 API requests"

`agent.py::run_session` is the core. Per user turn it runs up to `MAX_ROUNDS_PER_TURN =
25` tool-call rounds. Each round is one API request:

```
for each user turn:
    append the user prompt
    loop (max 25 rounds):
        policy(turn, round)          # the timing knob — see §6
        completion = provider.complete(messages, system, tools, ...)
        record everything about this request
        append the assistant blocks
        if no tool calls: break      # the model answered — turn is over
        execute every tool call, append one tool_result block each
```

This is the whole reason the headline "6 turns" and the headline "33 requests" differ:
**one user prompt costs as many API requests as the model needs tool calls**, and every
one of them re-reads the entire conversation so far. Run 1: 6 turns → 33 requests. Run 2:
6 turns → 39 requests.

Failure modes are recorded rather than raised, because a truncated session is still a
valid measurement of the requests that did happen:

- **Window exhausted** (`ContextWindowExceeded`) → stop cleanly, keep every row collected.
  This is how we know Haiku 4.5's 200k window was exhausted at request 61 of a 6-turn
  session — a terminal state of the workload, not a crash.
- **Ran out of rounds** → `completed = False`, rows kept.
- **Malformed tool JSON** → becomes a `tool_result` with `is_error: true` that the model
  can read and correct, so the session (and the measurement) continues.

---

## 4. What is measured, per request

Every single API request produces one row. Not per turn — per request. The fields
(`RequestRecord`, written out by `bench.py::write_records_csv`):

**Billing split (the four disjoint buckets):**
`cache_write`, `cache_read`, `uncached_input`, `output`, `cost_usd`, `cumulative_cost_usd`

**Context, as it stood for *this* request:**
`context_tokens` (the prompt the model actually read), `cumulative_tokens`, `n_messages`,
`n_blocks`, `blocks_added`, `history_chars`

**Timing:** `elapsed_s`, `gap_s` (time since the previous request started — the variable
the TTL cares about), `latency_s`

**Provenance:** `index`, `turn`, `stop_reason`, `tool_calls`

Context shape is measured **from the conversation actually being sent**, at the moment
it is sent — not reconstructed afterwards from the CSV. `blocks_added` counts assistant
blocks plus one tool_result per tool call, because that is exactly what the next
request's cache lookback has to walk back over.

### Optional: full context capture

`--capture-context` dumps the **exact conversation** behind every datapoint to
`results/<tag>-context/req-NNNN.json`. That's what makes a published figure auditable
after the fact: you can go back to request 17 and read the precise prefix that produced
its bill.

One subtlety worth stealing: the capture directory **deletes stale `req-*.json` on
start**. Without that, a shorter re-run leaves high-index files from a previous run
behind, and they read as real data — silently splicing two sessions into one
apparently-continuous curve.

---

## 5. The billing model — four buckets, 12.5× apart

`pricing.py` holds the only place list prices are written down. The cache multipliers are
structural, not per-model:

| Bucket | Rate | On Sonnet 5 |
|---|---|---|
| cache **read** | 0.10× input | $0.20 /Mtok |
| uncached **input** | 1.00× input | $2.00 /Mtok |
| cache **write** (5m TTL) | 1.25× input | $2.50 /Mtok |
| cache **write** (1h TTL) | 2.00× input | $4.00 /Mtok |
| **output** | 5× input (all three models) | $10.00 /Mtok |

Cards: opus-5 $5/$25 (1M window), sonnet-5 $2/$10 (1M), haiku-4-5 $1/$5 (**200k**).
An unpriced model raises rather than defaulting — guessing a price would put a fabricated
number into a published figure.

Two structural constants also live here and both show up in findings:
`LOOKBACK_BLOCKS = 20` (a breakpoint walks back at most 20 content blocks looking for a
prior entry; a turn appending more silently misses) and `MAX_BREAKPOINTS = 4`.

`usage.py` does the arithmetic over these buckets. Everything is `@dataclass(frozen=True)`
— `SessionUsage.with_request()` returns a new session rather than accumulating in place.

---

## 6. The independent variable: *when* requests happen

`bench.py` injects timing as a policy, so the **identical** session can be replayed under
different clocks and the only thing that changes in the bill is *when*:

- **`busy`** — no artificial delay. The agent works straight through. Every request
  starts inside the 5-minute TTL. Runs 1, 2 and 3 are all busy.
- **`sawtooth --idle 420`** — sleeps 420s before each new user turn, past the 5m TTL.
  Models a human who steps away between prompts, or an agent blocked on something slow.

The sawtooth sleep fires **only between turns, not between tool-call rounds** (`turn > 0
and round_index == 0`). An agent mid-turn is generating continuously; inserting waits
there would model a workload nobody runs, and would multiply wall clock by the *request*
count instead of the *turn* count.

**Status:** across the three busy runs the longest gap was 86.2s, so the TTL was never
exercised and claims C11, C18, C20 and C21 rested on arithmetic. The **sawtooth run is
now in flight** on the new `snake` workload — see §11a. Nothing from it is quoted until
it lands.

---

## 7. Getting real dollars out of the provider

This is the part most cost benchmarks fudge. cachecost uses **the bill**, not its own
arithmetic about the bill.

**Provider: OpenRouter** (`openrouter_provider.py`), stdlib `urllib` only, no SDK. It is
used because it passes Anthropic's `cache_control` through *and* returns `usage.cost` —
**real billed dollars**. The request sets `"usage": {"include": True}` to opt in.

The cost is one shape mismatch: OpenRouter speaks OpenAI-compat, the agent loop speaks
Anthropic blocks. The provider owns the translation:

```
Anthropic                      OpenAI-compat
assistant [tool_use]      <->  assistant .tool_calls[]
user [tool_result]        <->  role="tool" messages
tools[]                   <->  tools[{type:"function"}]
```

### Cache writes: reported, and independently cross-checked

The OpenAI-compat usage block reports cache *reads* (`prompt_tokens_details.cached_tokens`)
but historically had no field for cache *writes*. Two paths exist and both are kept:

1. **Reported** — measured against the live endpoint on 2026-08-28, OpenRouter *does*
   return `cache_write_tokens`, so it's read directly.
2. **Derived algebraically from the bill** (`derive_writes`) — with the billed total
   known, writes fall out of two equations:

   ```
   W + U = prompt_tokens - cached_tokens
   cost  = C·r_read + W·r_write + U·r_in + O·r_out
   →  W = (cost - C·r_read - O·r_out - (P-C)·r_in) / (r_write - r_in)
   ```

`writes_disagree_by()` runs both and returns the gap. Two independent paths to the same
number; a non-zero gap means one of them is wrong, **and a benchmark that publishes cost
figures should notice rather than average them**.

If no cost is reported at all, the split is unrecoverable and everything uncached is
reported as plain input rather than guessed at — an inferred write would be a fabricated
number in a measured column. The record carries `write_measured` so a run always knows
which path produced its numbers.

---

## 8. The bug that nearly poisoned everything: the frozen breakpoint

Worth a paragraph in any newsletter, because it is the failure mode that produces
*plausible-looking* numbers.

`cache_control` must land on the **final** message of the conversation so the cached
prefix advances every request. In an agentic loop most turns end on a **tool result** —
and in the OpenAI-compat shape a tool result is its own `role="tool"` message. If the
marker can't attach to one, it walks backward and lands at the start of the turn instead.
The breakpoint then **freezes** while the tail keeps growing, and every appended token
bills at the **full input rate, forever**.

Measured on a live run before the fix: **cache reads pinned at 77,354 tokens while the
context grew past 133,000** — roughly **56,000 tokens paying full price on every
request**. The fix was to give tool messages a *list* content shape so the breakpoint can
land there (`_mark_tail`, commit `7a059cf`).

Nothing about that run looked broken from the outside. It completed, it produced a CSV,
it produced a hit rate. This is the whole argument for the self-checks in §9.

---

## 9. The self-checks — how the harness catches itself lying

Five independent checks, all runnable:

**1. Regression: does the slope recover the published read rate?** (`analysis.py`)
Every request is a datapoint. A warm request bills its whole prefix at the cache-read
rate, so fitting **input-side cost against context size** across warm requests should
have a slope equal to the read rate. On Sonnet that means **$0.20/Mtok — not $2.00**. A
slope near the full input rate means the cache isn't working; that's exactly what a
frozen breakpoint produces. Both recorded runs: **$0.200/Mtok, r² = 1.000**.

Two methodological details make this work at all:
- **Output cost is subtracted first.** Output ranges over two orders of magnitude in an
  agentic session (a 45-token confirmation next to an 8,000-token file) and at 5× the
  input rate that variance swamps the signal. Fitting *raw* cost against context on a real
  run returned **r² = 0.07 — measuring nothing.**
- **Only warm requests are fitted.** Cold starts and misses rewrite at a different rate
  and would drag the slope toward the write rate.

**2. Direct breakpoint check** (`breakpoint_advances`) — a healthy session's cache read
grows every request, because the prefix it reads back is everything sent last time. This
was added after the r² check produced a **false positive** weak-fit warning (`f683dad`);
the direct check doesn't need a regression to be trustworthy.

**3. Price card cross-check** — `python -m context_clock.cachecost.price_check` compares
our 15 rates across 3 models against OpenRouter's published card. **0 disagreements.**

**4. Measured vs priced agreement** — every run prints the billed total next to what our
own price card says it should be. Both recorded runs: **$0.000000 difference.**

**5. M1, the lookback miss** (`find_lookback_misses`) — a cache miss that arrives well
inside the TTL (gap < 0.8 × 300s) whose *preceding* turn appended more than 20 content
blocks. That miss cannot be explained by the clock; it's the 20-block lookback limit. The
harness looks for this signature in every run and reports explicitly when it did **not**
trigger, rather than staying silent.

---

## 10. The counterfactuals reported alongside the bill

Every run prints three numbers for the same session:

- **billed** — what actually happened
- **without caching** — every input token at the full input rate, the stateless-API
  counterfactual. Run 1: $2.2081 vs $0.8992 billed → caching saved **2.5×** (**5.69×** on
  the input side alone, once output is removed)
- **naive estimate** — final context × input rate, i.e. what someone computes after
  reading "52k tokens" off the context meter. Run 1: **$0.1043, understating 8.6×**

Plus the **re-read factor** — cumulative tokens ÷ peak context — how many times the
session paid for its own context. Run 1: **16.4×**.

---

## 11. What three runs of the identical command showed

| Measure | Run 1 | Run 2 | Δ |
|---|---|---|---|
| API requests | 33 | 39 | +18.2% |
| Wall clock | 10.0 min | 12.5 min | +25.0% |
| **Billed** | **$0.8992** | **$1.1745** | **+30.6%** |
| Peak context | 52,141 | 67,197 | +28.9% |
| Cumulative tokens | 855,895 | 1,190,756 | +39.1% |
| Task completed (suite green) | yes | **no** | — |
| Price card vs real bill | $0.000000 | $0.000000 | 0 |
| Read rate recovered from billing | $0.200/Mtok, r²=1.000 | $0.200/Mtok, r²=1.000 | 0 |
| Output share of bill | 69.0% | 67.9% | −1.1pp |
| Cache hit rate | 93.4% | 93.9% | +0.5pp |
| Cache misses | 0 / 33 | 0 / 39 | 0 |

**Run 3** (added after the above): 43 requests, 13.5 min, **suite green**, billed
**$1.4187**. Output 63.6% of the bill, hit rate 94.7%, **0 misses / 43**, read rate
**$0.200/Mtok r²=1.000**, price card error **$0.000000**, re-read factor **19.8×**.
Cumulative-cost-vs-turn: linear r² 0.928, **quadratic r² 0.983**.

Three byte-identical commands: **$0.8992 → $1.1745 → $1.4187, a +57.8% spread.**

**The single most important methodological finding: session totals are not reproducible
(±58% over three runs). The rate structure is reproducible to the digit.**

The agent is non-deterministic and writes different code each run. Therefore cachecost
may publish **rates, bucket shares and invariants**; it may **not** publish an absolute
session cost as "the cost of this workload". Run 2 also cost 31% more *and delivered
less* (red suite, ended on an output-cap truncation) — **cost per completed task is the
only honest unit** for comparing sessions.

### Run 1's bucket split (the run whose suite went green)

| Bucket | Cost | Share | Tokens | Rate |
|---|---|---|---|---|
| output | $0.6204 | **69.0%** | 62,036 | $10.00/Mtok |
| cache reads | $0.1483 | 16.5% | 741,654 | $0.20/Mtok |
| cache writes | $0.1303 | 14.5% | 52,139 | $2.50/Mtok |
| uncached input | $0.0001 | 0.0% | 66 | $2.00/Mtok |

Read that table twice. **741,654 cache-read tokens cost less than 52,139 cache-write
tokens.** And **output — 62k tokens against 794k input tokens — is 69% of the bill.**
On a well-cached agent session the input side is close to solved and *generation* is what
you're actually buying.

Input cost per request by turn (output removed) climbed **1.00× → 1.44× → 2.21× → 3.13×
→ 3.15× → 4.32×** as average context grew 3,143 → 45,911. Same work, more context to
re-read. That is the O(n²) mechanism, priced.

### Cache mechanism probe (claude-haiku-4-5, 15,204-token prefix)

| | Cold (write) | Warm (read) |
|---|---|---|
| Billed | $0.019031 | $0.0015464 |
| Predicted from price card | $0.019031 | $0.0015464 |

Prefix cost ratio **12.50×**, exact — the 1.25×/0.10× structure, confirmed against a real
bill. Also observed: a **2,508-token prefix cached nothing** (minimum cacheable prefix
~4,096 on Haiku) and it **fails silently** — no error, no warning, you just pay full price.

---

## 11a. The TTL experiment — a bigger game, and real idle time

Everything above shares one hole: **no run ever waited out the 5-minute TTL.** Closing it
needs two things the Minesweeper runs don't have — deliberate idle time, and a session
long enough for the cost curve to have shape.

**New workload: `snake`.** 13 scripted turns across four modules — engine, buffered input
queue, scoring + speed curve, `render()`, `from_layout`, wrap mode, obstacle levels,
expiring bonus food, JSON replay round-trip, BFS pathfinding AI, tail-safety AI, 50-state
rewind, and a final review pass.

Two design choices worth stating because each reverses an earlier one:

- **It is the talk's own workload**, where Minesweeper was deliberately chosen *not* to
  be. Accepted for **comparability** — a figure measured here can be placed beside
  theirs. Minesweeper stays the independent check; Snake is the like-for-like one, and
  neither gets reported as the other.
- **Four modules, not one file.** `write_file` re-sends whole files, so a module that
  outgrows `--max-tokens` truncates mid-write and burns the turn. That is exactly how run
  2 ended red. Both Snake runs use `--max-tokens 16384`, and a unit test asserts the brief
  names ≥4 distinct modules so this can't silently regress.

**The matched pair** — identical task, identical model, *only the clock differs*:

| | policy | idle between turns | purpose |
|---|---|---|---|
| control | `busy` | none | same-task baseline |
| treatment | `sawtooth` | **420s** (> the 300s TTL) | forces expiry at every turn boundary |

Both arms run **`--turns 8`** (the first 8 scripted turns: engine, input queue, scoring,
render, `from_layout`, wrap mode, `levels.py`, bonus food) rather than the full 13. The
13-turn version was launched, reached turn 3, and was stopped: measured cumulative cost
at the turn boundaries was $0.1001 / $0.1998 / $0.5269, which on the quadratic shape
projects **~$25-35 for the pair** against a $8-18 estimate. Eight turns still gives
**7 turn boundaries — 7 forced misses** — which is all the categorical 0-vs-N result
needs, and keeps the arms matched. Recorded because it is a change to the apparatus:
the 13-turn cost curve was traded away for budget, and $0.7288 of the aborted run was
forfeited rather than spliced into the new one. **Both arms start from an empty
workspace**, so neither inherits code the other wrote.

The control is not optional. Comparing a sawtooth Snake run against a busy Minesweeper
run would confound *task* with *timing*, and the whole design is "same session, different
clock."

**Observed on the restart — the cache outlives the process.** The relaunched busy arm's
request 0 billed `write 0 / read 1,315`, where the aborted launch's request 0 had billed
`write 1,315 / read 0`. Identical command, opposite bucket: the 1,315-token prefix
(system prompt + first user turn) was still inside its TTL from the run killed minutes
earlier, so the new process **inherited a warm cache**. The entry is keyed on content,
not on the session, and nothing in the output flags it.

Two consequences. **A rerun inside the TTL is not a cold start**, so any figure that
depends on the cold-write bucket must be taken from a run launched well outside it.
And the two arms here are mildly asymmetric — the sawtooth arm begins ~50 minutes after
the busy arm ends and therefore cold-starts genuinely. The difference is ~$0.003 and
changes no conclusion, but the two "request 0" rows are not like for like and are not
reported as if they were.

**Prediction under test:** the control shows **0 cache misses** (as all three Minesweeper
runs did); the treatment shows **one forced miss per turn boundary — about 12** — each
rewriting the entire prefix at 1.25× instead of reading it at 0.1×.

Note the miss *count* is **categorical**: 0 versus ~12 is not a number the ±58% cost noise
can move. That's what makes it a sound result on n=1 per arm, where the cost delta needs
the matched control to be interpretable at all.

This is the run that moves **C11, C18, C20 and C21** from arithmetic onto measurement.

---

## 12. Reproducing it

```bash
# from the repo root; needs OPENROUTER_API_KEY in env or a gitignored .env
python -m context_clock.cachecost.run \
  --task minesweeper --model claude-sonnet-5 --provider openrouter \
  --policy busy --ttl 5m --capture-context --tag cache-mine-sonnet-busy3

# the TTL experiment — the matched Snake pair (in flight)
python -m context_clock.cachecost.run --task snake --model claude-sonnet-5 \
  --policy busy --ttl 5m --max-tokens 16384 --capture-context \
  --tag cache-snake-sonnet-busy

python -m context_clock.cachecost.run --task snake --model claude-sonnet-5 \
  --policy sawtooth --idle 420 --ttl 5m --max-tokens 16384 --capture-context \
  --tag cache-snake-sonnet-sawtooth

# independent price-card cross-check, no network cost
python -m context_clock.cachecost.price_check

# the 30-claim ledger with executable verdicts (deterministic, no network)
python -m context_clock.cachecost.verify
```

Outputs land in `results/<tag>.csv` (per-request rows), `results/<tag>.log` (the live
arc), `results/<tag>-context/` (full conversation per datapoint) and
`results/<tag>-workspace/` (the Minesweeper engine the agent built). All gitignored —
the *findings* are committed to `BENCHMARK_LOG.md` and `CACHE_CLAIMS.md`, the raw
artefacts are not.

Cost per busy run: roughly **$0.90–1.20**, 10–13 minutes.

---

## 13. Limits, stated plainly

- **The 5-minute TTL has not yet been exercised in a completed run.** Longest observed
  gap across three busy runs: 86.2s. The Snake sawtooth run is in flight; until it lands,
  C11, C18, C20 and C21 rest on arithmetic, not measurement.
- **Session totals vary ±58% across three identical runs.** Publish rates and shares;
  never an absolute session cost.
- **Cache writes are reported by the provider, cross-checked algebraically** — not
  observed at the Anthropic API layer directly. (`--provider anthropic` exists and reports
  the write count natively, but the recorded runs used OpenRouter for the real `usage.cost`.)
- **One task, one provider, three models priced, one model measured at length.** The
  bucket shares are Sonnet-5-on-Minesweeper shares. The 12.5× ratio and the 0.1×/1.25×
  structure are general; the 69% output share is not.
- **`n = 3` completed Minesweeper runs**, all busy. The Snake pair (busy control +
  sawtooth treatment) is `n = 1` per arm — enough for the categorical miss-count result,
  not enough to put an error bar on the cost delta.
