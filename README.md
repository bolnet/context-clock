# context-clock

A local, free benchmark that **demonstrates and measures the failure arc of a long-running
LLM session** on a finite context window:

1. **context fills** — the running transcript grows toward the limit
2. **recall decays** — accuracy on earlier facts drops *before* the hard limit (context rot)
3. **tokens exhaust** — cumulative token cost climbs
4. **self-compaction fires** — when the window fills, older turns are lossily summarized;
   we measure compaction's **token cost** and the **recall it loses**

This is the *problem* benchmark. A later phase plugs in memory backends (Attestor / Zep / Mem0)
to show the problem going away.

## Why local-only

100% [Ollama](https://ollama.com) — zero API keys, zero cost, anyone can reproduce.
Models are referenced by name via the OpenAI-compatible endpoint at `localhost:11434/v1`,
and real token counts come from Ollama's `prompt_eval_count` / `eval_count`.

```bash
ollama pull llama3.2        # dev model (3B, cliffs early)
# larger contrast models, all free:
ollama pull qwen2.5:14b-instruct
ollama pull phi4:14b
```

> **Not 1:1 across models.** The arc *shape* reproduces on every model, but onset, steepness,
> and absolute scale are model-specific (Chroma "Context Rot" found degradation is non-uniform).
> So each model must be re-run; small-model results validate the harness, not the numbers.

## Knobs

| Knob | Meaning |
|------|---------|
| `model` | Ollama model name |
| `context_limit` | imposed token cap (= Ollama `num_ctx`); small (e.g. 4096) → arc completes in seconds |
| `probe_cadence` | how often (in turns) to fire a recall probe |
| `compaction_threshold` | fraction of `context_limit` that triggers self-compaction |

## Output

`results/*.csv` (per turn: `turn, context_tokens, cumulative_tokens, recall_correct, compaction_event`)
plus the hero chart: **tokens-vs-turn** (climbing) + **recall-vs-turn** (decaying), with the
limit line and compaction markers.

## Status

v1 — baseline + self-compaction, synthetic inject-and-probe workload, local models only.
Built test-first.
