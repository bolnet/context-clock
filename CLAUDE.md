# CLAUDE.md — context-clock

Project memory for Claude Code. Read this first; it tells you what the project is, how it's
laid out, how to run it, and the conventions that are easy to violate. For a from-scratch
machine setup, follow `INSTALL.md` instead (it's an install runbook addressed to an agent).

## What this is

**context-clock** is the open-source benchmark for **agent token efficiency**. It measures how
many tokens an LLM agent burns as a conversation grows, and demonstrates that retrieved memory
cuts **input (context) tokens ~21×** while holding **100% recall** — identically across six
models, open and closed.

The mechanism, in one breath: re-sending the whole conversation every turn (the default agent
loop) makes the **input tokens** the model must read grow **O(n²)**. Capping the window keeps
tokens flat but recall **rots to 0% by ~turn 10**. Retrieving only what's needed (memory) keeps
context **flat O(n) (~200 tok/call)** *and* recall at 100%. That contrast is the whole product.

**input tokens** is the hero metric (universal ~21–22.5× at turn 100). Cost is a provider-dependent
footnote (prompt-caching + answer verbosity move it around); don't lead with cost.

## Non-negotiable conventions

- **No fabricated numbers.** Every figure that appears in `README.md`, `REPORT.md`, the HTML
  pages, or any chart must trace to a real run recorded in `BENCHMARK_LOG.md`. That file is the
  committed source of truth. If you don't have the data, run it or say so — never estimate and
  present it as measured.
- **We do no compaction "on our own."** The benchmark uses the model's **native window**; don't
  silently summarize/truncate to make numbers look better. `--client-window` exists only to
  *reproduce* cap-and-rot as an explicit experiment.
- **Providers stay stdlib-only.** `OllamaProvider`, `OpenAICompatProvider`, and
  `ClaudeCliProvider` use `urllib`/`subprocess` — no SDK. The `openai` entry in
  `requirements.txt` is **vestigial and unused**; don't build on it. Keep new HTTP calls on
  `urllib` with the existing bounded-retry pattern (429/5xx retried, other 4xx fail fast,
  nothing swallowed).
- **Immutability.** `Completion`, `Fact`, and the Attestor view rows are `@dataclass(frozen=True)`.
  Return new objects; don't mutate.
- **Secrets never leak.** `.env` (holds `OPENROUTER_API_KEY`) is gitignored — keep it that way.
  Never echo the key, never commit it, read it via `_load_openrouter_key()`.

## Module map (`context_clock/`)

| File | Responsibility |
|------|----------------|
| `run.py` | CLI entry (`python -m context_clock.run`). Arg parsing, provider/backend wiring, prints the per-turn arc, writes CSV + chart. |
| `driver.py` | Core loop. `run_session` (grow / cap / until-rotted) and `run_memory_session` (retrieve). Defines `Fact`, `TurnRow`. |
| `provider.py` | `Completion` dataclass + `OllamaProvider` (native `/api/chat`, real token counts, honors `num_ctx`). |
| `openai_provider.py` | `OpenAICompatProvider` — OpenRouter/OpenAI-style. Opts into `usage.cost` for **real billed $**; bounded retry. |
| `claude_cli_provider.py` | `ClaudeCliProvider` — shells out to `claude -p … --output-format json` (subscription, no API key). Carries harness overhead — not 1:1 with bare endpoints. |
| `attestor_memory.py` | `AttestorMemory` + `connect()` — real semantic retrieval over a running `attestor api` (REST via urllib). DI'd client; honest misses return `None`. |
| `memory.py` | `RetrievalMemory` — the **ideal exact-key reference** backend (can never miss); the default for `--memory`. |
| `meter.py` | Cumulative token + cost accounting. |
| `niah.py` / `documents.py` | Needle-in-a-haystack workload + memo/haystack generation. |
| `grader.py` | Answer-level recall grading (exact planted-code match, e.g. `k9f3a2`). |
| `compaction.py` / `compactor.py` | The cap-and-rot / self-compaction experiment machinery. |
| `report.py` | `write_csv` + `plot` (pandas + matplotlib). |
| `compare.py` | Cross-run comparison charts. |
| `cachecost/` | **Prompt-cache economics.** `pricing.py` (published price card + the 0.1×/1.25×/2× multipliers), `usage.py` (per-request/session cost arithmetic, frozen), `scenarios.py` (cache-lifecycle simulator), `claims.py` (the 30-claim ledger with executable verdicts), `verify.py` (CLI). Deterministic, no network. Findings in `CACHE_CLAIMS.md`. |

## How to run

Run **from the repo root** as a module (there is no installed entry point — see Gotchas).

```bash
# Local, zero-cost (needs Ollama running + a model pulled; no API key)
python -m context_clock.run --until-rotted --turns 100      # grow → O(n²) input, recall rots
python -m context_clock.run --memory --turns 100            # the fix: flat context, 100% recall

# Any model via OpenRouter (needs OPENROUTER_API_KEY in env or .env)
python -m context_clock.run --provider openrouter --model openai/gpt-5.4 \
  --until-rotted --turns 100 --probe-max-tokens 2048

# Against a live Attestor backend (a local `attestor api` on :8090)
python -m context_clock.run --provider openrouter --model openai/gpt-5.4 \
  --memory --memory-backend attestor --turns 100 --cadence 1
```

Each run prints the arc and writes `results/<tag>.csv` + `results/<tag>.png`. Key knobs:
`--turns`, `--until-rotted`, `--memory` / `--memory-backend {retrieval,attestor}`,
`--client-window N` (reproduce cap-and-rot on an API model), `--cadence`, `--probe-max-tokens`
(raise to ~2048 for reasoning models).

## Testing

```bash
pytest          # 186 tests, deterministic, no network needed
```

Live Attestor tests are gated behind `CONTEXT_CLOCK_LIVE_ATTESTOR=1` (and `ATTESTOR_URL`) and are
**skipped by default**, so the suite is clean offline. Follow TDD for changes (tests first).

## Gotchas

- **No packaging file.** There's no `pyproject.toml`/`setup.py`, so `pip install -e .` **fails**
  despite what older README text may say. Install deps with `pip install -r requirements.txt`
  and run via `python -m context_clock.run` from the repo root.
- **`requirements.txt` lists `openai`** but nothing imports it — don't assume an SDK is in use.
- **`.gitignore` hides** `results/`, `*.png`, `.env`, `.venv/`, `__pycache__/`,
  `.playwright-mcp/`. Generated CSVs/charts and the API key are intentionally untracked.
- **Default branch is `master`** (not `main`). GitHub Pages serves from `master` `/docs` →
  https://bolnet.github.io/context-clock/ . `main` is protected; deploy via PR.
- **`docs/`** holds the published landing page (`index.html`) + animated deep-dive
  (`context-clock-benchmark.html`). Keep numbers in sync with `BENCHMARK_LOG.md`.

## The fix it points to

context-clock *measures* the problem. **[Attestor](https://attestor.dev)** is the open-source
memory layer that *fixes* it (flat ~200 tok/call, two API calls `add()`/`recall()`). Both MIT.
