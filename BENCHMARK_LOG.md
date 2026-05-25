# Benchmark log — source of truth for REPORT.md and the deck

Every measured run, kept here so `REPORT.md` and `context-clock-deck.html` can be
updated/regenerated later without re-running. Raw per-turn CSVs + PNGs live in
`results/` (gitignored, local only); this file is the durable, committed record.

All runs: local, Ollama, macOS (Apple Silicon), `temperature=0`, zero API cost.

---

## 1. Rot-until-complete — the headline (ctx 1024, `--until-rotted`, unpredictable needles)

No compaction · probe every turn · stop after recall stays 0% for 3 probes.
Needle = deterministic-per-memo but unpredictable code (e.g. `k44cb6`), so recall
can't be faked by pattern inference.

| Model | Size | Family | Turns | Final cumulative tokens | Recall trajectory | Fully rotted |
|---|---|---|---|---|---|---|
| llama3.2 | 3B | Llama | 16 | 33,187 | 100%×11 → 67 → 33 → 0 → 0 → 0 | turn 16 |
| qwen2.5:14b-instruct | 14B | Qwen | 16 | 33,197 | 100%×11 → 67 → 33 → 0 → 0 → 0 | turn 16 |
| phi4:14b | 14B | Phi | 16 | 32,879 | 100%×11 → 67 → 33 → 0 → 0 → 0 | turn 16 |
| mistral-small:24b | 24B | Mistral | 16 | 33,294 | 100%×11 → 67 → 33 → 0 → 0 → 0 | turn 16 |

**Finding:** identical across an 8× parameter spread (3B→24B) and 3 families →
**raw context rot is model-independent** (truncation is model-agnostic).
Per-turn cost plateaus at ~2.95–3.0K tokens once the window saturates (~980 ctx tokens).

**Local ceiling:** `qwq:32b` (32B reasoning) exceeded a 600s/call timeout before
completing a turn — intractable locally; reasoning traces also confound a 16-token probe.
Practical local ceiling on this hardware ≈ 24B.

**Frontier model on the same rot test (API, constrained window):** DeepSeek-R1 via OpenRouter,
same `--until-rotted` workload with a **client-side 1024-token window** (API models have no
`num_ctx`, so we truncate the sent prompt instead) and `--probe-max-tokens 2048`.

- Clean curve: recall 100% (t1–7) → 67 (t8) → 33 (t9) → **0 (t10–12)**, rot at **turn 12**.
  Same monotonic shape as the local models → **model-independence holds for a 671B frontier
  model too.** Onset is a few turns earlier (12 vs 16) only because the `len/4` estimate caps
  R1's *effective* window to ~635 actual tokens (< the local 1024-actual) — a window-size
  artifact, not a model difference.
- **Reasoning-model caveat (important):** the first run used the default 16-token answer cap and
  produced garbage — noisy 0/100/67/… incl. **0% at turn 1 with the fact in context** — because
  R1 burns the budget *thinking* and its code gets truncated before it's emitted. Raising
  `--probe-max-tokens` to 2048 de-confounded it. (~$0.07 total for both R1 rot runs.)
  CSV: `results/deepseek_deepseek-r1_rot_ctx1024.csv`.

---

## 2. The pattern-inference confound (preserved — this CSV was overwritten by the fix)

Same rot run on **qwen2.5:14b** but with the OLD **deterministic** needle (`Memo N → k{N:03d}`):

- Recall by turn (1–27): `100×12 → 67,67,67 → 100,100 → 67 → 100 → 33,33,33,33,33 → 0,0,0`
- Fully rotted: **turn 27** (vs turn 16 with unpredictable needle)
- Final cumulative tokens: ~65,378

The 14B model "held on" to turn 27 and even recovered to 100% — but by turn 16 the
oldest memos were already truncated out of context. It was **inferring** `kNNN` from the
pattern, not recalling. Fix = unpredictable needles → curve drops at turn 16 (§1).
This is why §1 is credible.

---

## 3. No-compaction naive baseline (llama3.2, ctx 1024, 18 turns, cadence 3)

- Recall: 100% (t3,6,9) → 67% (t12) → 0% (t15, t18)
- Live context plateaus ~958 (window cap → oldest truncated); cumulative ≈ 21,952 tokens
- 0 compactions. The clean decay curve; precursor to the per-turn rot run in §1.

---

## 4. Agent-compaction cross-model (ctx 1024, 18 turns, threshold 0.85)

| Model | Compactions | Total tokens | Min recall |
|---|---|---|---|
| llama3.2 (3B) | 4 | 23,449 | **33%** |
| qwen2.5:14b | 4 | 22,934 | 100% |
| phi4:14b | 4 | 23,525 | 100% |

Same window → identical compaction count (window-driven, not model). Difference is
**recall robustness to lossy summaries**: 3B loses codes, 14B retain them. (Distinct
from §1: with compaction, model size matters; for raw truncation rot, it doesn't.)

---

## 5. Window-size sweep (llama3.2, compaction on)

| Window | Compactions | Total tokens | Rot onset |
|---|---|---|---|
| 1024 | 4 | 23,449 | ~turn 15 |
| 2048 | 2 | 44,908 | ~turn 24 |
| 4096 | 2 | 136,813 | ~turn 35 |

Tokens scale **super-linearly** with window (~6× from 1024→4096); every window decays
to the same recall floor. Bigger window buys time, not reliability — at higher cost.

---

## 6. Memory backend vs full-context (llama3.2, ctx 1024, 18 turns)

| | Full-context | Memory backend |
|---|---|---|
| Live context | → 976 | flat 167 |
| Cumulative tokens | 23,449 | **3,060** (~7.7× fewer) |
| Compactions | 4 | 0 |
| Recall | → 33% | **100%** |

---

## 7. Native-window feasibility (llama3.2, single timed call)

| Window | Prompt tokens | Latency | Needle hit |
|---|---|---|---|
| 8K | 5,397 | 11.2s | ✓ |
| 16K | 11,767 | 29.4s | ✓ |

~2.5 ms/token. Viable ≤16K on a small model with raised `--timeout`; ≥32K or 14B+
impractical locally. Bigger models / native windows → API path (see §8).

---

## 8. Frontier model — DeepSeek-R1, native-window NIAH-by-depth (OpenRouter)

Provider: OpenRouter (`deepseek/deepseek-r1`), via `OpenAICompatProvider`. API models
have no `num_ctx` knob, so this is the **intra-window** test (needle at a fractional depth
in a haystack of size N), not truncation rot. R1 returns the answer in `message.content`
after a separate `reasoning` field — the provider reads `content` correctly.

Sweep: sizes {4K, 16K, 32K, 64K} × depths {0, 25, 50, 75, 100%} = 20 probes.

| | 0% | 25% | 50% | 75% | 100% |
|---|---|---|---|---|---|
| 4K | ✓ | ✓ | ✓ | ✓ | ✓ |
| 16K | ✓ | ✓ | ✓ | ✓ | ✓ |
| 32K | ✓ | ✓ | ✓ | ✓ | ✓ |
| 64K | ✓ | ✓ | ✓ | ✓ | ✓ |

**Recall 20/20.** No intra-window degradation up to 64K at any depth. Cost: 484,237 prompt
+ 3,605 completion tokens ≈ **$0.27**; 239s. CSV: `results/niah_deepseek_r1.csv`.

**Caveat:** single-needle, verbatim, low-distractor NIAH is the *easy* regime — frontier models
pass it. This is "R1 handles basic retrieval to 64K," not "R1 never rots." Harder regimes that
would actually stress it: **128K+ windows, multiple needles, distractor facts, or semantic
(non-verbatim) queries.** Smoke test (1 probe, ctx ~200): found needle in `content`, finish=stop,
~$0.0005.

### Pending (not yet run)
- Harder R1 regimes above (128K / multi-needle / distractors / semantic).
- Bedrock alternative: `us.deepseek.r1-v1:0` (us-east-1) — valid AWS creds present; pricier per token.
- ⚠️ The OpenRouter key was shared in chat once — rotate it.

## 9. Claude Haiku via subscription (`claude -p`, no API key)

Run Haiku with no API key via the Claude Code subscription. `ClaudeCliProvider` shells out to
`claude -p --model haiku --output-format json`. Same `--until-rotted` rot test, 1024 client-side
window, `--turns 18` cap.

- **Clean rot curve:** recall 100% (t1–7) → 67 (t8) → 33 (t9) → **0 (t10–12)**, rot at **turn 12**
  — identical to DeepSeek-R1 on the same client-window. Model-independence holds for Haiku too,
  with **no API key**. CSV: `results/haiku_rot_ctx1024.csv`.

**Caveats (real, but didn't break the recall signal):**
- **Token accounting is unreliable** here: `prompt_tokens` reads ~10–30 every turn because our
  content is folded into the Claude Code harness *cache* (`cache_creation`), so `input_tokens`
  under-reports. Treat the token columns as meaningless for this provider; only recall is valid.
- **Agentic/verbose responses** (completion 194–1146 tokens, harness framing); a one-shot smoke
  even flagged the haystack as an "adversarial prompt." But the unpredictable needle still
  surfaced in the text when in-context, so substring-grading produced a clean curve over 50 probes.
- Not 1:1 with the clean Ollama/OpenRouter runs (agent harness), but the rot *signal* is genuine.

**Sonnet via the same path: REFUSED.** `claude -p --model sonnet` hard-fails on the NIAH haystack
with `API Error: …appears to violate our Usage Policy… Try rephrasing` (`is_error:true`), even with
retry/backoff (it's a deterministic policy refusal, not a transient blip). The Claude Code harness's
safety layer flags the random-filler-plus-"vault code" prompt as a likely injection. Haiku tolerated
it (verbose but answered); Sonnet blocks it. **Takeaway:** the subscription/`claude -p` route is
model-dependent and unreliable for this benchmark — a clean Sonnet run needs the Anthropic API (key).
We do not engineer around the usage-policy refusal.
