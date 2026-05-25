# context-clock — findings report

**Date:** 2026-05-25
**Scope:** 7 models, 3B → 671B, across local (Ollama) and API (OpenRouter) · `temperature=0`
**Reproduce:**
```bash
# headline: rot until complete — no compaction, probe every turn, stop at full rot
python -m context_clock.run --model llama3.2 --until-rotted --limit 1024 --turns 60
# a frontier model on the same test (client-side window; reasoning models need room to answer)
python -m context_clock.run --provider openrouter --model deepseek/deepseek-r1 \
    --client-window 1024 --probe-max-tokens 2048 --until-rotted
```

## What this measures

A long-running session on a finite context window with no external memory. Each turn injects a
uniquely-answerable fact — a varied needle-in-a-haystack document (`documents.py`) with the needle
("Memo N: … the vault code is `<code>` …") buried in the middle. The code is deterministic per
memo but **unpredictable** (e.g. `k44cb6`), so it can't be inferred — the only way to answer is to
actually have the fact in context. We probe recall of the oldest facts and record, per turn, live
context size, cumulative tokens, recall, and any compaction events.

> **Models don't self-compact.** A raw LLM never summarizes its own history — when the prompt
> exceeds the window it simply **truncates** (drops the oldest tokens). The compaction and memory
> sections below are *agent-layer interventions* layered on top of the model, not behaviors the
> model exhibits on its own.

## The core measurement: rot until complete (no intervention)

Add a document every turn, probe recall **every turn**, with **no compaction** — the model is on
its own. Run until **fully rotted** (recall pinned at 0% for 3 straight probes), not to a fixed
turn count.

| Turn | 1–11 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|
| Recall (oldest 3) | 100% | 67% | 33% | **0%** | **0%** | **0%** |

Accuracy holds at 100% while everything still fits, then **collapses over three turns**
(100 → 67 → 33 → 0) as the prompt overflows and the oldest memos are truncated away — and stays at
0%. This is raw context rot with nothing intervening, and the accuracy drop is exactly the signal
we track.

## The headline: rot is model-independent

Running the identical `--until-rotted` workload across 7 models (the needle can't be guessed, so
recall is real). API models have no `num_ctx`, so the prompt is truncated **client-side** to 1024
tokens to give them a comparable fixed window.

| Model | Size | Source | Recall trajectory | Hits 0% |
|---|---|---|---|---|
| llama3.2 | 3B | local | 100%×11 → 67 → 33 → 0 | turn **14** |
| qwen2.5 | 14B | local | 100%×11 → 67 → 33 → 0 | turn **14** |
| phi-4 | 14B | local | 100%×11 → 67 → 33 → 0 | turn **14** |
| mistral-small | 24B | local | 100%×11 → 67 → 33 → 0 | turn **14** |
| DeepSeek-R1 | 671B | API | 100%×7 → 67 → 33 → 0 | turn **10** |
| GPT-5.4-mini | — | API | 100%×7 → 67 → 33 → 0 | turn **10** |
| Claude Haiku | — | — | 100%×7 → 67 → 33 → 0 | turn **10** |

**Identical curve across all seven** — a ~200× parameter spread (3B → 671B), three model families,
and three deployment paths. Once the answer can't be inferred, every model rots on the same
`100 → 67 → 33 → 0` staircase. **Raw context rot is model-independent** — truncation drops the
oldest tokens regardless of how capable the model is. The only difference is *onset*: the local
cohort hits 0% at turn 14, the client-windowed cohort at turn 10 — a **window-size artifact** (the
client-side token estimate left the API runs a slightly smaller effective window), not a model
effect.

> **Why this is credible — the pattern-inference confound.** An earlier cross-model run used a
> *deterministic* needle (`Memo N → k{N:03d}`). Under it, qwen2.5:14b appeared to resist rot to
> turn 27 and even *recovered* to 100% — but by turn 16 the oldest memos were already truncated
> out of context. It was **inferring** the codes from the index pattern, not recalling them (a 14B
> spots the pattern; a 3B doesn't). Switching to unpredictable needles collapses every model to
> turn 16 — proving the staircase is recall, not inference.

> **Local ceiling:** `qwq:32b` (32B reasoning) exceeded a 600s/call timeout before completing a
> turn — intractable locally. **Reasoning-model probes** need room: a 16-token answer cap truncated
> DeepSeek-R1's code mid-thought and produced garbage until `--probe-max-tokens` was raised to 2048.

## Agent intervention: self-compaction (vs the raw rot above)

A self-compaction agent (lossily summarize the oldest turns at 85% fill), layered on the model.
All four stages of context-window failure appear, measured:

| Stage | Evidence |
|---|---|
| **1. Context fills** | live context climbs 164 → 976 tokens, right to the 1024 limit |
| **2. Recall decays** | recall on the oldest facts drops from 100% to 33% |
| **3. Tokens exhaust** | cumulative spend climbs to ~23,400 tokens over 18 turns |
| **4. Self-compaction fires** | triggered at turns 8, 11, 14, 17 — the sawtooth in the context curve |

Compaction is not free: each event adds a summarization call and buys headroom by **discarding
detail** — which is the recall cost. Here model size *does* matter, but only for robustness to
lossy summaries (3B loses codes, 14B retains them) — a different question from raw rot.

## A bigger window is a trap (llama3.2, vary the window)

| Window | Total tokens | Rot onset |
|---|---|---|
| 1024 | 23,449 | ~turn 15 |
| 2048 | 44,908 | ~turn 24 |
| 4096 | 136,813 | ~turn 35 |

Two measured truths: (1) token consumption scales **super-linearly** with window size (~6× from
1024→4096); (2) bigger windows **delay the recall cliff but never escape it** — every window decays
to the same floor. More room buys *time*, not *reliability*, at dramatically higher cost.

## The payoff: external memory vs full-context (llama3.2, 18 turns)

Same workload, but a memory backend retrieves only the relevant fact per probe instead of stacking
the whole transcript:

| | Full-context (ctx 1024) | Memory backend |
|---|---|---|
| Live context | climbs to 976 | **flat at 167** |
| Cumulative tokens | 23,449 | **3,060** (~7.7× fewer) |
| Compactions | 4 | **0** |
| Recall | drops to 33% | **100%** |

Retrieve-what's-needed stays flat and never forgets; stuff-everything explodes and rots. The v1
backend is an exact-key retriever (the ideal-retrieval reference); real backends (Attestor / Zep /
Mem0) implement the same `add` / `recall` interface and slot in here.

## Native-window feasibility (llama3.2, single timed call)

| Window | Prompt tokens | Latency | Needle found |
|---|---|---|---|
| 8K | 5,397 | 11.2s | ✓ |
| 16K | 11,767 | 29.4s | ✓ |

~2.5 ms/token. Viable on a small model at ≤16K with `--timeout` raised; ≥32K or 14B+ stays
impractical locally.

## Frontier intra-window retrieval: DeepSeek-R1 NIAH-by-depth (API)

Local truncation rot is settled. For a frontier model behind an API the question shifts to
**intra-window** degradation — does it lose a needle *inside* its window, by size and position? A
NIAH-by-depth sweep on DeepSeek-R1 (sizes {4K, 16K, 32K, 64K} × depths {0–100%}, 20 probes):

**20/20 — perfect recall at every size and depth, no intra-window degradation to 64K** (~$0.27,
239s). Honest caveat: single-needle, verbatim, low-distractor retrieval is the *easy* regime
frontier models pass — this shows R1 handles basic retrieval to 64K, not that it never rots. Real
stress needs 128K+, multiple needles, distractors, or semantic queries.

## Cost & reach

- **$0** — 4 local models on Ollama (unlimited, offline).
- **≈$0.27 total** — DeepSeek-R1 + GPT-5.4-mini via OpenRouter API.

## Status

- **74 tests green** — grader, meter, driver helpers, documents/unpredictable needles,
  rot-until-complete stop logic, per-turn token usage, retrieval memory, NIAH, and providers;
  test-first, with provider + end-to-end paths validated by the real runs above.
- 100% reproducible locally with Ollama; API runs reproduce with an OpenRouter key.

## Next

- Stress a frontier model beyond the easy regime: 128K+, multiple needles, distractors, semantic queries.
- Full native-window arc (llama3.2, 8K–16K) now that per-call latency is known tractable.
- Plug a real memory backend (Attestor / Zep / Mem0) into the `add`/`recall` interface to show the arc flattening.
