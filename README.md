# context-clock

**The open-source benchmark for agent token efficiency.** It measures how many tokens an
LLM agent burns as a conversation grows — and proves that retrieved memory cuts input tokens
**~21×** while holding **100% recall**, identically across six models (open and closed).

> Free · MIT · runs locally on [Ollama](https://ollama.com) (no API keys) or against any
> OpenRouter model. Every number below is reproducible — 99 tests, deterministic workload.

---

## The finding

Re-sending the whole conversation to the model every turn (the default agent loop) makes the
**input tokens** it must read grow **O(n²)** — a parabola that compounds with every session.
You have two bad options, and one good one:

| Strategy | Input tokens | Recall |
|---|---|---|
| **Grow the context** (re-send all) | **O(n²)** — 1.33M by turn 100 (gpt-5.4) | 100% |
| **Cap the context** (truncate old) | flat-ish | **rots to 0% by turn 10** |
| **Retrieve what's needed** (memory) | **O(n)** — flat ~200 tok/call | **100%** |

Capping is mechanical: once the window fills, the oldest turns drop and recall decays in an
identical staircase (`100% ×7 → 67 → 33 → 0`) from a 3B model to a 671B one. Retrieval escapes
the trap — flat per-call context **and** perfect recall.

### Input-token reduction at turn 100 — measured, 6 models, 100% recall both sides

| Model | Provider | input × | cost (raw → mem) |
|---|---|---|---|
| gpt-5.4 | OpenAI | **21.5×** | $0.57 → $0.19 |
| gpt-5.4-mini | OpenAI | **21.5×** | $0.22 → $0.06 |
| claude-opus-4 | Anthropic | **22.2×** | $24.15 → $1.24 |
| claude-sonnet-4 | Anthropic | **22.1×** | $4.83 → $0.25 |
| kimi-k2.6 | Moonshot (open) | **21.0×** | $0.43 → $0.17 |
| deepseek-v3.2 | DeepSeek (open) | **22.5×** | $0.16 → $0.009 |

Input × is set by workload geometry, not the model — every model traces the same curve:
5.6× (t24) → 11× (t50) → 16× (t75) → ~21–22.5× (t100). *Cost* reduction varies with provider
prompt-caching and answer verbosity; **input tokens are the universal number.**

---

## Quickstart

### Local, zero-cost (Ollama — no API keys)
```bash
ollama pull llama3.2                       # 3B dev model, arc completes fast
python -m context_clock.run --until-rotted --turns 100   # watch input go O(n²), recall rot
python -m context_clock.run --memory --turns 100         # the fix: flat context, 100% recall
```

### Any model via OpenRouter
```bash
export OPENROUTER_API_KEY=sk-or-...
python -m context_clock.run --provider openrouter --model openai/gpt-5.4 \
  --until-rotted --turns 100 --probe-max-tokens 2048
```

### Against a real memory backend (Attestor)
```bash
# with a local `attestor api` running on :8090
python -m context_clock.run --provider openrouter --model openai/gpt-5.4 \
  --memory --memory-backend attestor --turns 100 --cadence 1
```

Each run prints the per-turn arc and writes `results/<tag>.csv` + a chart. API runs record the
**real billed cost** from OpenRouter's `usage.cost` — not list-price math.

## What it measures

- **Input (context) tokens** — the tokens the model reads each turn. This is the hero metric:
  raw grows quadratically; memory stays flat. (Output tokens are the model's answer, unchanged
  by memory, so they're excluded from "what memory saves.")
- **Recall** — answer-level grading: an unpredictable code (`k9f3a2`) is planted in each memo;
  we ask for a specific memo's code later and check the exact string comes back.
- **Cost** — real billed dollars per call (OpenRouter `usage.cost`).

## Knobs

| Knob | Meaning |
|------|---------|
| `--model` | Ollama name, or an OpenRouter slug with `--provider openrouter` |
| `--turns` | session length (the curve is most dramatic at 100) |
| `--until-rotted` | rot stress test: probe every turn, run until recall stays at 0% |
| `--memory` / `--memory-backend {retrieval,attestor}` | retrieve-what's-needed instead of re-sending all |
| `--client-window N` | cap an API model's prompt to N tokens (reproduces cap-and-rot) |
| `--cadence` | probe every N turns |
| `--probe-max-tokens` | answer budget per probe (raise for reasoning models) |

## Reproduce

```bash
pip install -e . && pytest          # 99 tests, deterministic
```

`BENCHMARK_LOG.md` is the committed source of truth — every run, every number, with CSV pointers.

## The fix

context-clock measures the problem. **[Attestor](https://attestor.dev)** is the open-source
memory layer that fixes it — flat ~200 tokens per call, 21× fewer input tokens, 100% recall, two
API calls (`add()` / `recall()`). Both tools are MIT.

## License

MIT. No lock-in.
