# Benchmark log — source of truth for REPORT.md and the decks

Every measured run, kept here so `REPORT.md`, `context-clock-report.html`, and
`context-clock-deck.html` can be updated/regenerated without re-running. Raw per-turn CSVs +
PNGs live in `results/` (gitignored, local only); this file is the durable, committed record.

Runs are either **local** (Ollama, macOS Apple Silicon, `num_ctx` truncation) or **API**
(OpenRouter, truncated client-side to mimic a fixed window). All runs `temperature=0`.

---

## 1. Rot-until-complete — the headline (ctx 1024, `--until-rotted`, unpredictable needles)

No compaction · probe every turn · stop after recall stays 0% for 3 straight probes. The needle
is a uniquely-answerable code planted mid-haystack — deterministic per memo but **unpredictable**
(e.g. `k44cb6`), so recall can't be faked by inferring a pattern.

API models have no `num_ctx`, so the prompt is truncated **client-side to 1024 tokens** and the
identical workload runs (with `--probe-max-tokens 2048`, so a reasoning model can finish before
answering — see the caveat below).

**Local `num_ctx` cohort** — recall `100%×11 → 67 → 33 → 0 → 0 → 0`, hits 0% at **turn 14** (run ends t16):

| Model | Size | Cumulative tokens |
|---|---|---|
| llama3.2 | 3B | 33,187 |
| qwen2.5:14b | 14B | 33,197 |
| phi4:14b | 14B | 32,879 |
| mistral-small:24b | 24B | 33,294 |

**Client-windowed cohort** (client-side 1024 window) — recall `100%×7 → 67 → 33 → 0 → 0 → 0`, hits 0% at **turn 10** (run ends t12):

| Model | Size | Cumulative tokens | Source |
|---|---|---|---|
| DeepSeek-R1 | 671B | 25,730 | OpenRouter API |
| GPT-5.4-mini | — | 17,901 | OpenRouter API |
| Claude Haiku | — | 8,173 | — |

**Finding:** identical `100 → 67 → 33 → 0` staircase across a ~200× parameter spread (3B → 671B)
and across local / API runs → **raw context rot is model-independent** (truncation is model-agnostic).
The only difference is onset: the local cohort hits 0% at turn 14, the client-windowed cohort at
turn 10 — a **window-size artifact** (the `len/4` token estimate left the API runs an effective
~635-token window, smaller than the local 1024-actual), not a model difference. Per-turn cost
plateaus at ~2.95–3.0K tokens once the window saturates.

**Reasoning-model caveat:** the first DeepSeek-R1 run used the default 16-token answer cap and
produced garbage (noisy 0/100/67, incl. 0% at turn 1 with the fact present) because R1 burns the
budget *thinking* and its code gets truncated before emission. Raising `--probe-max-tokens` to
2048 de-confounded it → the clean curve above. (~$0.07 for the R1 rot runs; ~$0.27 incl. the NIAH
sweep in §8. GPT-5.4-mini rot run ~a few cents.)

**Local ceiling:** `qwq:32b` (32B reasoning) exceeded a 600s/call timeout before completing a turn
— intractable locally; long `<think>` traces also confound a small probe cap. Practical local
ceiling on this hardware ≈ 24B; bigger models go via the API path.

---

## 2. The pattern-inference confound (why §1 is credible)

Same rot run on **qwen2.5:14b** but with the OLD **deterministic** needle (`Memo N → k{N:03d}`):

- Recall by turn (1–27): `100×12 → 67,67,67 → 100,100 → 67 → 100 → 33,33,33,33,33 → 0,0,0`
- Fully rotted: **turn 27** (vs turn 16 with the unpredictable needle); ~65,378 cumulative tokens.

The 14B model "held on" to turn 27 and even *recovered* to 100% — but by turn 16 the oldest memos
were already truncated out of context. It was **inferring** `kNNN` from the index pattern, not
recalling. A 14B spots the pattern; a 3B doesn't — which masqueraded as "the bigger model resists
rot." Fix = unpredictable needles → every model drops at turn 16 (§1). This confound is the reason
the headline is trustworthy.

---

## 3. No-compaction naive baseline (llama3.2, ctx 1024, 18 turns, cadence 3)

`--no-compaction`: let the window overflow, never reclaim headroom.

- Recall by probe (t3/6/9/12/15/18): `100 / 100 / 100 / 67 / 0 / 0`.
- Live context climbs to ~958 then plateaus at the 1024 cap; the oldest memos are silently
  truncated and recall decays **cleanly to 0%** — the clearest rot curve in the project (no
  sawtooth, the window just slides forward and forgets).

---

## 4. Agent-compaction cross-model (ctx 1024, 18 turns, threshold 0.85)

A self-compaction *agent intervention* (lossily summarize oldest turns at 85% fill) on top of the
raw model. Same window + workload, vary the model:

| Model | Size | Compactions | Total tokens | Min recall |
|---|---|---|---|---|
| llama3.2 | 3B | 4 | 23,449 | 33% |
| qwen2.5:14b | 14B | 4 | 22,934 | 100% |
| phi4:14b | 14B | 4 | 23,525 | 100% |

All fill and compact **identically** — the window is the constraint, not the model. The only
difference is **recall robustness to lossy summaries**: the 3B loses codes when old turns are
summarized; the 14B models retain them. (This is a *different* question from raw rot, where model
size is irrelevant — §1.) Compaction is not free: each event adds a summarization call.

---

## 5. Window-size sweep (llama3.2, compaction on)

| Window | Total tokens | Rot onset |
|---|---|---|
| 1024 | 23,449 | ~turn 15 |
| 2048 | 44,908 | ~turn 24 |
| 4096 | 136,813 | ~turn 35 |

Tokens scale **super-linearly** with window (~6× from 1024→4096); every window decays to the same
recall floor. A bigger window buys **time, not reliability**, at dramatically higher cost — the
case for an external memory layer over a bigger context.

---

## 6. Memory backend vs full-context (llama3.2, ctx 1024, 18 turns)

| | Full-context | Memory backend |
|---|---|---|
| Live context | climbs to 976 | flat at 167 |
| Cumulative tokens | 23,449 | **3,060** (~7.7× fewer) |
| Compactions | 4 | 0 |
| Recall | drops to 33% | **100%** |

Retrieve-what's-needed stays flat and never forgets; stuff-everything explodes and rots. The v1
backend is an exact-key retriever (ideal-retrieval reference); real backends (Attestor / Zep /
Mem0) implement the same `add` / `recall` interface and slot in here.

---

## 7. Native-window feasibility (llama3.2, single timed call)

| Window | Prompt tokens | Latency | Needle hit |
|---|---|---|---|
| 8K | 5,397 | 11.2s | ✓ |
| 16K | 11,767 | 29.4s | ✓ |

~2.5 ms/token. Viable ≤16K on a small model with `--timeout` raised; ≥32K or 14B+ impractical
locally → API path (§8).

---

## 8. DeepSeek-R1 — native-window NIAH-by-depth (OpenRouter)

Local truncation rot is settled (model-independent). For a frontier model behind an API there's
no `num_ctx` knob, so the question shifts to **intra-window** degradation. NIAH-by-depth sweep on
DeepSeek-R1: sizes {4K, 16K, 32K, 64K} × depths {0–100%}, 20 probes.

- **20/20 — perfect recall at every size and depth, no intra-window degradation to 64K.**
  (~$0.27, 239s; answer lands in R1's `content` after the reasoning trace.)
- Honest caveat: single-needle, verbatim, low-distractor retrieval is the *easy* regime frontier
  models pass. Real stress needs 128K+, multiple needles, distractors, or semantic queries.

---

## 9. Attestor memory backend — real semantic retrieval (the payoff, measured)

Same memory-session harness (`run_memory_session`) but the backend is **Attestor** (graph+vector
memory system) instead of the exact-key `RetrievalMemory`. 24 turns, probe every 3rd turn (8 probes
at t3,6,…,24). Each run uses a fresh per-run namespace (`context-clock-<uuid>`) — **no DB cleanup**;
isolation is by namespace. Attestor run locally via Docker (Postgres + Neo4j + Pinecone Local on
:5080), host API on :8090.

### Top-1 retrieval (recall returns the single best match)
| Model | Size | Recall (every probe) | Live context | Cumulative tokens |
|---|---|---|---|---|
| llama3.2 | 3B | 100% | flat **139** | 3,408 |
| qwen2.5:14b | 14B | 100% | flat 129 | 3,184 |
| phi4:14b | 14B | 100% | flat 130 | 3,216 |
| DeepSeek-R1 | 671B (API) | 100% | flat 114 | 8,600 |
| GPT-5.4-mini | — (API) | 100% | flat 127 | 3,200 |
| Claude Haiku 4.5 | — (API) | 100% | flat 140 | 3,512 |

Both API models (DeepSeek-R1, GPT-5.4-mini) were run on a **freshly-wiped DB** (Postgres/Neo4j volumes destroyed + reprovisioned via `schema.sql`, Pinecone Local reset) — so zero historical-graph confound. Its 8,600 cumulative is R1's *reasoning* tokens (≈640–715 completion/probe), NOT context growth — context stays flat at 114. **Model-independent across 3B→671B, local+API, reasoning+non-reasoning.**

**Two aspects proven:** (1) **context doesn't grow** — live context flat ~139 vs the raw-rot run
climbing to the ~976 cap; (2) **still answerable as it grows** — 100% recall through turn 24 where
raw rot is pinned at 0% from turn 14. Model-independent. For the token/$ reduction, do not quote a
mismatched-run figure — use the matched, billed comparison below.

### Real billed cost (OpenRouter `usage.cost`, measured) ⭐ headline source
The harness records OpenRouter's **real billed dollars** (`usage.cost`) per call — not list-price math.

**Methodology (the honest frame):** keep the model's **native context window** — do NOT artificially
cap it. Modern models (gpt-5.4 = 1.05M tokens) can hold the whole history, so the honest demo is that
re-sending the growing transcript into that native window **costs more every turn** while Attestor keeps
context flat (~212 tok). With the native window raw does **not rot** (nothing truncates → recall stays
100%), so the win is **cost + tokens at equal quality**, and the gap **widens with session length**.
(Capping to 1024 is only a secondary illustration of the *eventual-fill* failure mode — see ② / ③.)

**⓪ gpt-5.4 — native window, 100 turns, cadence 1, 100% recall on BOTH (THE headline).**
Only difference: flat (Attestor top-1, budget 200) vs growing (raw, native window) context. CSVs:
`results/gpt54_raw_native_100t/`, `results/gpt54_mem_native_100t/`.

| Turn | Raw (cum tokens / billed $) | Attestor (cum tokens / billed $) | token × | cost × |
|---|---|---|---|---|
| 24 | 81,449 / $0.1002 | 14,957 / $0.0443 | 5.4× | 2.26× |
| 50 | 339,323 / $0.2260 | 31,882 / $0.0944 | 10.6× | 2.39× |
| 75 | 753,182 / $0.3819 | 48,044 / $0.1423 | 15.7× | 2.68× |
| **100** | **1,329,110 / $0.5701** | **64,194 / $0.1902** | **20.7×** | **3.00×** |

→ **At 100 turns: ~95% fewer tokens (20.7×) and ~67% lower cost (3.00×), at identical 100% recall.**
The gap **widens with length** on both axes (5.4×→20.7× tokens, 2.26×→3.00× cost). **Tokens diverge far
faster than cost — prompt caching** (raw's repeated prefix bills at the cached rate) damps the dollar
gap: raw burns 20.7× more tokens but is billed only 3.0× more. Report cost at a *named* turn count.
gpt-5.4 list price: $2.50/M prompt, $15/M completion.

**SIX-model summary @ turn 100 — INPUT/context tokens (native window, cadence 1, 100% recall on BOTH).**
HERO METRIC = cumulative **input (prompt) tokens** — the part memory controls. Output tokens are the
model's answer (unchanged by memory) and are excluded so verbosity doesn't distort the result.

| Model | Provider | Raw inP → mem inP | **input ×** | Raw $ → mem $ | cost × |
|---|---|---|---|---|---|
| gpt-5.4 | OpenAI | 1,326,734 → 61,818 | **21.5×** | $0.5701 → $0.1902 | 3.0× |
| gpt-5.4-mini | OpenAI | 1,326,734 → 61,818 | **21.5×** | $0.2191 → $0.0571 | 3.8× |
| claude-sonnet-4 | Anthropic | 1,599,747 → 72,333 | **22.1×** | $4.8304 → $0.2482 | 19.5× |
| claude-opus-4 | Anthropic | 1,599,747 → 72,216 | **22.2×** | $24.1521 → $1.2392 | 19.5× |
| moonshotai/kimi-k2.6 | Moonshot | 1,305,943 → 62,307 | **21.0×** | $0.4263 → $0.1677 | 2.5× |
| deepseek/deepseek-v3.2 | DeepSeek | 1,318,755 → 58,574 | **22.5×** | $0.1617 → $0.0093 | 17.4× |

**Input× is universal (~21–22.5×) across all six** (two open-source) — model/provider-independent
(workload geometry: raw cumulative O(n²) vs memory O(n)). Per-call input curve identical across models:
raw climbs 132→~8,700–10,500/call; memory flat ~196–245. **Cost× is a messy footnote (2.5×–19.5×)**
distorted by two things memory doesn't touch: (a) **prompt caching** — OpenAI auto-caches the raw prefix
→ cost× damped to ~3–4×; Anthropic/DeepSeek none → cost× tracks input ~17–19.5×; (b) **answer verbosity**
— kimi emits ~165 output tok/answer vs ~8 terse, so its memory-side *output* cost drags cost× to 2.5×
(its *input*× is still 21.0×, and total-token× is 12.8× — which is why we report INPUT, not total).
A single 100-turn opus-4 raw session billed **$24.15** vs **$1.24** with memory. CSVs:
`results/{gpt54,gptmini,sonnet4,opus4,kimi,deepseek}_{raw,mem}_native_100t/`. (Qwen3-Max excluded —
repeated provider 429 rate-limiting; six models already agree.)

---
The gpt-5.4-mini runs below corroborate the same pattern at the mini's price, and add the **capped
"run-till-rot"** failure mode (① — raw forgets *and* costs more).

**Methodology variants below (gpt-5.4-mini):** ① caps raw @1024 so it rots; ② keeps the native window
(equal quality). Both confirm Attestor wins.

**① Rot-span comparison — raw capped @1024, run until rot (cadence 1, 12 turns).** Raw forgets; Attestor
runs the same 12 turns. CSVs: `results/openai_gpt-5.4-mini_rot_ctx1024.csv`, `results/mem_rotmatch/`.

| | Without Attestor (capped, runs till rot) | With Attestor (same 12 turns, flat) |
|---|---|---|
| Recall | `100×7 → 67 → 33 → 0 → 0 → 0` — **gone by t10** | **100% every turn** |
| Live ctx / call | climbs to the ~645 (cap) | flat **~212** |
| Cumulative tokens | 17,909 | 7,127 |
| **Billed total** | **$0.014669** | **$0.006358** |

→ Over the same span it takes raw to forget everything, Attestor is **~57% cheaper (2.3×)**, uses
**~60% fewer tokens (2.5×)**, **and** still has 100% recall. Raw is strictly worse: pays more, remembers nothing.

**② Equal-quality comparison — raw uncapped, recall held at 100% (cadence 1, 24 turns).** The other raw
failure mode: avoid rot by never truncating, and cost grows unbounded. CSVs: `results/raw_uncapped/`,
`results/mem_matched/`.

| | Without Attestor (uncapped, grows) | With Attestor (flat) |
|---|---|---|
| Recall | 100% (held) | 100% |
| Live ctx / call | climbs **125 → 2,122** | flat **~212** |
| Cumulative tokens | 81,449 | 14,963 |
| **Billed total** | **$0.038792** | **$0.013315** |

→ At equal 100% recall, Attestor is **~66% cheaper (2.9×)** with **~82% fewer tokens (5.4×)** at turn 24,
and the gap widens every turn.

**Why tokens and cost diverge — prompt caching.** In ②, raw processes 5.4× more tokens but is billed only
2.9× more: after ~turn 15 the raw per-turn cost *falls while tokens keep rising* (t14→15 +$0.00314, t15→16
+$0.00160 on *more* tokens) because OpenRouter bills the repeated transcript prefix at the cached rate.
So on a cached API the token saving overstates the dollar saving. The honest cost multiplier for
gpt-5.4-mini is **~2.3–2.9× (NOT the ~9× earlier estimated)** — that estimate used local-llama token
counts as a cost proxy and compared mismatched call counts. Corrected per the no-fabrication rule.

**State the multiplier at a named session length, never as one fixed number** — it grows with session
length (raw ctx/call climbs, Attestor flat) and is larger for long sessions and models *without* prompt
caching. Other measured billings: Attestor budget-512 (default) 24t cadence-3 $0.008773 (flat ~460 ctx,
100%); Attestor top-1 24t cadence-3 $0.004645 (flat ~212 ctx, 100%).

### Recall-budget sweep — pack ALL retrieved memos up to a token budget (llama3.2)
| | top-1 (≈1 memo) | budget 2048 |
|---|---|---|
| recall | 100% | 100% (no gain) |
| live context | flat 139 | **grows 311→1900** |
| cumulative tokens | 3,408 | **28,137 (~8×)** |

Per-turn context at budget 2048: t3=311, t6=564, t9=810, t12=1064, t15=1312, t18=1562, t21=1814, t24=1900.
**Finding:** a bigger recall budget does NOT improve recall (already 100%) — it **forfeits the
flat-context win and costs ~8×**, because Attestor packs more memos as the store grows (context
climbs back toward the rot regime; stayed 100% only because 1900 < the 4096 window).

### Per-turn token accounting (llama3.2, top-1)
- **Answering** (answerer LLM, from CSV `turn_tokens`): 0 on plain turns; **426 per probe turn**
  (3 questions × ~142, almost all the re-sent ~130-tok retrieved memo); avg ~142/turn.
- **Embedding** (Voyage, estimated — Attestor exposes no embedding-token count): ~137/turn (1 memo
  ingest) + ~18 on probe turns; ≈3,430 total/model.
- **Ingest extraction LLM: 0** — a plain `add` embeds+stores only (verified: 0 new Neo4j edges per add).

### mistral-small:24b — local ceiling, incomplete
Attempted at budget 2048 / `num_ctx 4096`: the worker blocks on a glacially-slow Ollama call —
the 24B (15 GB) + 4096 KV cache spills to CPU (26%/74% CPU/GPU split), minutes per answer. Not a
deadlock (Attestor `:8090` recalls all returned 200 OK; the worker waits on Ollama). Confirms the
**~24B local ceiling**; the budget-2048 pattern is already established (model-independent) from llama.

### Infra / data-isolation notes
- **No DB cleanup** between runs; isolation via fresh per-run namespace. Pinecone Local (ephemeral)
  held 24 vectors/run, namespace-scoped. Postgres (286,518 rows) and Neo4j (41,908 nodes / 41,438
  edges) are **shared/persistent historical data** — the vector lane is namespace-scoped, but
  `graph_expansion` traverses the shared graph (a mild confound + a slowdown source).

---

## Notes

- **Rigor:** 74 tests passing (grader, meter, driver helpers, documents/unpredictable needles,
  rot-until-complete stop logic, per-turn token usage, retrieval memory, NIAH, providers) —
  test-first; provider + end-to-end paths validated by the real runs above.
- ⚠️ The OpenRouter API key was shared in chat — **rotate it.**

---

## cachecost — prompt-cache economics (2026-08-28)

Real agentic coding session (headless Minesweeper engine, 6 user turns) driven through a
tool-use loop against **claude-sonnet-5 via OpenRouter**, `--policy busy` (no artificial
delay). Every request's four-way billing split and **real billed cost** (`usage.cost`)
recorded. Per-request CSVs in `results/cache-mine-sonnet-busy*.csv` (gitignored).

### Two identical runs — totals vary, structure does not

| Measure | Run 1 | Run 2 | Run 3 | spread |
|---|---|---|---|---|
| API requests | 33 | 39 | 43 | +30.3% |
| Wall clock | 10.0 min | 12.5 min | 13.5 min | +35.0% |
| **Billed** | **$0.8992** | **$1.1745** | **$1.4187** | **+57.8%** |
| Peak context | 52,141 | 67,197 | 85,423 | +63.8% |
| Cumulative tokens | 855,895 | 1,190,756 | 1,689,687 | +97.4% |
| Task completed (suite green) | yes | **no** | yes | — |
| Price card vs real bill | $0.000000 | $0.000000 | $0.000000 | 0 |
| Read rate recovered from billing | $0.200/Mtok, r²=1.000 | $0.200/Mtok, r²=1.000 | $0.200/Mtok, r²=1.000 | 0 |
| Output share of bill | 69.0% | 67.9% | 63.6% | −5.4pp |
| Cache hit rate | 93.4% | 93.9% | 94.7% | +1.3pp |
| Cache misses | 0 / 33 | 0 / 39 | 0 / 43 | 0 |

**Finding:** session totals are **not reproducible (±58% across three runs)** — the agent is non-deterministic
and writes different code each run. The **rate structure is reproducible to the digit**.
Therefore cachecost may publish rates, bucket shares and invariants; it may **not** publish an
absolute session cost as "the cost of this workload". Run 2 also cost 31% more and delivered
less (red suite, ended on an output-cap truncation); run 3 cost **58% more than run 1**
for the same six prompts and the same green suite — cost per *completed* task is the only
honest unit for comparing sessions.

### Run 1 bucket split (the completed run)

| Bucket | Cost | Share | Tokens | Rate |
|---|---|---|---|---|
| output | $0.6204 | **69.0%** | 62,036 | $10.00/Mtok |
| cache reads | $0.1483 | 16.5% | 741,654 | $0.20/Mtok |
| cache writes | $0.1303 | 14.5% | 52,139 | $2.50/Mtok |
| uncached input | $0.0001 | 0.0% | 66 | $2.00/Mtok |

Without caching: $2.2081 (2.5× the bill; **5.69×** on the input side alone). Naive
context-meter estimate $0.1043, understating **8.6×**. Re-read factor **16.4×**.

Input cost per request by turn (output removed): 1.00× → 1.44× → 2.21× → 3.13× → 3.15× →
**4.32×** as average context grew 3,143 → 45,911. Same work, more context to re-read.

### Cache mechanism probe (claude-haiku-4-5, 15,204-token prefix)

| | Cold (write) | Warm (read) |
|---|---|---|
| Billed | $0.019031 | $0.0015464 |
| Predicted from price card | $0.019031 | $0.0015464 |

Prefix cost ratio **12.50×**, exact. Also observed: a 2,508-token prefix cached nothing
(minimum cacheable prefix ~4,096 on Haiku, fails silently); Haiku 4.5's 200k window was
exhausted at request 61 of a 6-turn session.

### Price card cross-check

`python -m context_clock.cachecost.price_check` — 15 rates across 3 models vs OpenRouter's
published card, **0 disagreements**.

### Not measured

The 5-minute TTL was **never exercised** — longest gap in either run was 86.2s. Claims C11,
C18, C20, C21 rest on arithmetic and documentation, not on measurement here. Needs
`--policy sawtooth --idle 420`.


### Run 3 — third identical busy run (2026-08-28)

`--task minesweeper --model claude-sonnet-5 --policy busy --ttl 5m --capture-context`

6 user turns -> **43 API requests**, 13.5 min, **suite green**, billed **$1.4187**
(price card agreement 100.00%, worst-case error $0.000000).

| Bucket | Cost | Share | Tokens | Rate |
|---|---|---|---|---|
| output | $0.9022 | **63.6%** | 90,220 | $10.00/Mtok |
| cache reads | $0.3028 | 21.3% | 1,513,960 | $0.20/Mtok |
| cache writes | $0.2136 | 15.1% | 85,421 | $2.50/Mtok |
| uncached input | $0.0002 | 0.0% | 86 | $2.00/Mtok |

Without caching $4.1011 (**2.9×** the bill). Naive context-meter estimate $0.1708,
understating **8.3×**. Peak context 85,423; cumulative 1,689,687; **re-read factor 19.8×**.
Cache hit rate 94.7%, **0 misses / 43 requests**. Read rate recovered from billing
**$0.200/Mtok, r² = 1.000**; breakpoint advancing True; M1 not triggered.

Cumulative cost vs turn: linear fit r² 0.928, **quadratic fit r² 0.983** — the
super-linear shape, measured.

Three runs of a byte-identical command now span **$0.8992 / $1.1745 / $1.4187 (+57.8%)**
while every rate and invariant reproduced exactly. The n=3 evidence is stronger than the
n=2 statement it replaces: **publish rates, shares and invariants; never an absolute
session cost.**

---

## cachecost — the TTL experiment (snake, in flight 2026-08-28)

The gap in every run above: the **5-minute TTL was never exercised** (longest observed
gap 86.2s). Closing it needs idle time, and a session long enough to have a curve.

**New workload: `snake`** — 13 scripted turns across four modules (`snake.py`,
`levels.py`, `ai.py`, `replay.py`): engine, buffered input queue, scoring + speed curve,
render, `from_layout`, wrap mode, obstacle levels, expiring bonus food, JSON replay
round-trip, BFS AI, tail-safety AI, 50-state rewind, review pass.

Two deliberate design choices, both recorded because both cut against an earlier one:

* It **is** the talk's own workload, where `minesweeper` was chosen to *not* be. The
  tradeoff is accepted for **comparability** — a figure here can be set beside theirs.
  Minesweeper stays the independent check; snake is the like-for-like one.
* Work is **split across four modules** because `write_file` re-sends whole files, and a
  module outgrowing `--max-tokens` truncates mid-write and burns the turn — the failure
  that ended run 2 red. Both snake runs use `--max-tokens 16384`.

**The pair** (identical task, identical model, only the clock differs):

| | policy | idle between turns | purpose |
|---|---|---|---|
| control | `busy` | none | same-task baseline |
| treatment | `sawtooth` | **420s** (> 300s TTL) | forces expiry at every turn boundary |

Prediction under test: the control shows **0 cache misses**; the treatment shows **one
forced miss per turn boundary (~12)**, each rewriting the whole prefix at 1.25× instead
of reading it at 0.1×. The miss *count* is categorical and survives the ±58% total-cost
noise; the cost delta is what the matched control exists to license.

This is the run that moves C11, C18, C20 and C21 off arithmetic and onto measurement.
Results to be recorded here when it lands — **nothing from it is quoted until then.**
