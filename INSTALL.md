# INSTALL.md — Agent Install Runbook for context-clock

> **This file is a prompt.** It is written *to you, Claude* (or any capable coding agent), to be
> pasted in or read when asked to "install context-clock." It assumes **nothing** is set up on
> the machine. Your job is to bring this repository from a cold checkout to a state where the
> test suite is green and a smoke benchmark runs end-to-end — **without breaking anything,
> leaking secrets, or fabricating success.**
>
> Do not skim. Work the phases in order. Each phase ends with a **verification gate** you must
> pass before moving on. If a gate fails, stop and resolve it; don't paper over it.

---

## 0. Mission & operating rules

**Goal:** A working context-clock install with (a) a Python environment that imports the
package, (b) `pytest` green, and (c) at least one real benchmark run that prints a per-turn arc
and writes a CSV + chart. Pick the **lowest-friction provider tier** the machine can support
(see Phase 4) — do not force the user into paid API keys if a local path works.

**Rules you must follow:**

1. **Scan before you act.** Do a full reconnaissance pass (Phase 1) and *report what you found*
   before installing anything. Assume the README may be stale — trust the files you actually read
   over any prose, including this one. If reality contradicts this runbook, say so and adapt.
2. **Be idempotent and non-destructive.** Detect what already exists (a venv, a pulled model, an
   `.env`) and reuse it. Never overwrite `.env`, never `rm -rf` a venv without asking, never
   `pip install` into the system interpreter.
3. **Use Context7 for every external tool/library.** Before you run an install command for a
   dependency, framework, or CLI, look up its *current* docs via the Context7 MCP
   (`resolve-library-id` then `query-docs`). Your training data may be stale; package names,
   minimum versions, and install flags drift. This is mandatory — see the Context7 callouts in
   each phase. If Context7 is unavailable, fall back to `--help`/official docs and say you did.
4. **Never leak or commit secrets.** `OPENROUTER_API_KEY` lives in a gitignored `.env`. Never
   echo its value, never print it in logs, never `git add` it. If you must show the file,
   redact the value.
5. **Report honestly.** If a step is skipped, say it was skipped and why. If a test fails, show
   the failure. "Done" means verified, not assumed.

**Working directory:** the repository root (the directory containing `context_clock/`,
`requirements.txt`, and this file). All commands below assume you are there.

---

## 1. Reconnaissance — scan everything, install nothing yet

Build a picture of the machine and the repo. Run these and read the output before deciding
anything.

```bash
# OS, arch, shell
uname -a; echo "SHELL=$SHELL"

# Python: is there a 3.10+ interpreter? (this project needs 3.10+, is tested on 3.14)
python3 --version; which -a python3
# (macOS/Homebrew may also expose python3.12 etc — note all of them)

# Package managers / tools that may help later
which brew apt-get dnf 2>/dev/null; which git curl 2>/dev/null
which ollama 2>/dev/null && ollama --version
which claude 2>/dev/null && claude --version

# Repo state
git status --short; git rev-parse --abbrev-ref HEAD; git remote -v

# What the project actually declares vs. what's present
cat requirements.txt
ls pyproject.toml setup.py setup.cfg 2>/dev/null || echo "NO packaging file (expected)"
ls -d .venv venv 2>/dev/null || echo "no venv yet"
ls .env 2>/dev/null && echo ".env present (will reuse; do NOT overwrite)" || echo "no .env"
ls context_clock/*.py
```

**What you are establishing, and the facts you should already expect (verify, don't assume):**

- **Python 3.10+** is required (the code uses `X | None` annotations with
  `from __future__ import annotations`). It is known-good on **3.14**.
- **There is no `pyproject.toml` / `setup.py`.** This is expected. It means **`pip install -e .`
  will fail** — ignore any README line that says otherwise. You install dependencies from
  `requirements.txt` and run the tool as a module (`python -m context_clock.run`) from the repo
  root.
- **`requirements.txt` lists four packages**: `pytest`, `openai`, `pandas`, `matplotlib`.
  Note: **`openai` is declared but never imported** by the code (all providers use stdlib
  `urllib`/`subprocess`). It's harmless to install; don't build any plan around the SDK.
- The package is `context_clock/` (a plain package with `__init__.py`). Charts/CSVs land in
  `results/` (gitignored).

> **Context7 checkpoint:** before Phase 2, resolve docs for **pip / venv** (current
> environment-creation and install flags for the Python version you found) and for
> **pandas** and **matplotlib** (confirm the wheels are available for this Python + OS/arch —
> matplotlib in particular sometimes needs system libs on bare Linux). Query Context7 with the
> exact Python version and platform you detected.

**Gate 1:** You can state the OS, the Python 3.10+ interpreter you'll use, whether a venv/.env
already exist, and that no packaging file is present. Report this to the user before continuing.

---

## 2. Python environment & dependencies

Create an isolated environment (reuse one if it already exists and is healthy). Use the
3.10+ interpreter you identified.

```bash
# Create venv only if missing; otherwise reuse it
[ -d .venv ] || python3 -m venv .venv

# Activate (bash/zsh). On fish/Windows the activate path differs — adapt.
source .venv/bin/activate

# Modern, reproducible install
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

> **Context7 checkpoint:** if `pip install -r requirements.txt` fails on `pandas` or
> `matplotlib` (build-from-source errors, missing BLAS/freetype, no matching wheel), query
> Context7 for the current install guidance for that package on this platform/Python before
> hand-rolling a fix. Prefer the documented wheel/extra over compiling from source.

**Verify the environment:**

```bash
python -c "import pandas, matplotlib; print('charts OK:', pandas.__version__, matplotlib.__version__)"
python -c "import context_clock.run; print('package imports OK')"
```

**Gate 2:** Both imports succeed inside the venv. If `import context_clock.run` fails, you are
almost certainly not in the repo root, or not in the venv — fix that, don't reshuffle the code.

---

## 3. Run the test suite (no external services needed)

The suite is deterministic and offline-clean: live Attestor tests are gated behind
`CONTEXT_CLOCK_LIVE_ATTESTOR=1` and skip by default.

```bash
pytest -q
```

Expect ~101 tests passing, a handful skipped (the live-Attestor ones). 

**Gate 3:** `pytest` is green (skips are fine, failures are not). If something fails, read the
failure and fix the *cause*; do not edit tests to pass. If the failure is environmental (e.g., a
matplotlib backend error on a headless box), set a non-interactive backend
(`MPLBACKEND=Agg`) and note it — that's a real environment fix, not a test edit.

---

## 4. Provider setup — choose the lowest-friction tier that works

context-clock can drive three providers. Pick based on what the machine has and what the user
wants to measure. **Default to Tier A (local, free)** unless the user asks for a hosted model.

### Tier A — Ollama (local, zero-cost, no API key) — *preferred default*

> **Context7 checkpoint:** resolve current **Ollama** docs for *this* OS/arch — the install
> command, how to start the server/daemon, and the `pull` syntax. Use what Context7 returns
> rather than a remembered one-liner; the installer and service model change across platforms.

General shape (confirm specifics via Context7 / `ollama --help`):

```bash
# 1) Install Ollama if `which ollama` was empty (use the Context7-confirmed method for this OS).
# 2) Ensure the server is running (it listens on http://localhost:11434).
ollama --version
curl -fsS http://localhost:11434/api/tags >/dev/null && echo "ollama server up" || echo "start the ollama server"

# 3) Pull the small dev model the README uses (fast, completes the arc quickly):
ollama pull llama3.2
```

Smoke run (this is the install's real end-to-end proof):

```bash
python -m context_clock.run --memory --turns 15
```

You should see a per-turn table (`turn / ctx_tok / … / recall`) and a final
`wrote results/llama3.2_memory.csv` + `.png`. A short rot run is also worth showing the contrast:
`python -m context_clock.run --until-rotted --turns 30`.

### Tier B — OpenRouter (any hosted model; needs a key)

Only if the user wants hosted models (gpt-5.4, claude, kimi, deepseek, …).

```bash
# Reuse an existing .env if present (do NOT overwrite it). Otherwise create it once:
[ -f .env ] || printf 'OPENROUTER_API_KEY=%s\n' "<paste-key-here>" > .env
# (or: export OPENROUTER_API_KEY=sk-or-...   for the current shell only)
```

The CLI reads the key from `$OPENROUTER_API_KEY` first, then from a gitignored `.env` in the repo
root (`_load_openrouter_key()`), and **never logs it**. Confirm `.env` is gitignored
(`git check-ignore .env` should echo `.env`). Smoke run:

```bash
python -m context_clock.run --provider openrouter --model openai/gpt-5.4 \
  --until-rotted --turns 100 --probe-max-tokens 2048
```

API runs print the **real billed total** from OpenRouter's `usage.cost`. Reasoning models need a
larger `--probe-max-tokens` (≈2048) or recall will read as 0% because the answer gets truncated.

### Tier C — Claude via the `claude` CLI (subscription, no API key)

Only if `which claude` succeeded and the user is logged in. Caveat baked into the provider: each
call runs inside the full Claude Code harness (large cached preamble), so results carry harness
overhead and aren't 1:1 with the clean Ollama/OpenRouter runs. Smoke:

```bash
python -m context_clock.run --provider claude-cli --model haiku --memory --turns 15
```

### Optional — Attestor backend (real semantic retrieval)

`--memory` defaults to the in-process **`retrieval`** backend (an ideal exact-key reference) and
needs **nothing external**. The **`attestor`** backend measures *honest* semantic retrieval and
needs a running `attestor api` on `http://127.0.0.1:8090`. Only set this up if the user
explicitly wants to benchmark against real Attestor:

```bash
# with a local `attestor api` server already running on :8090
python -m context_clock.run --provider openrouter --model openai/gpt-5.4 \
  --memory --memory-backend attestor --turns 100 --cadence 1
# To also run the live-Attestor unit tests:
CONTEXT_CLOCK_LIVE_ATTESTOR=1 ATTESTOR_URL=http://127.0.0.1:8090 pytest tests/test_attestor_memory_live.py -q
```

**Gate 4:** At least one provider tier produces a real per-turn arc and writes a CSV + PNG into
`results/`. Show the user the last ~10 lines of the run and the written file paths.

---

## 5. Report back

Give the user a short, honest status:

- **Environment:** OS, Python version + path, venv location.
- **Install:** which deps installed (call out that `openai` is unused), any Context7 lookups you
  relied on and what they changed.
- **Tests:** the `pytest` summary line (passed / skipped).
- **Provider:** which tier you set up and why, with the smoke-run result (arc tail + output
  files). Note any tier you *didn't* set up and what it would need.
- **Caveats / follow-ups:** anything skipped, any environment fix applied (e.g. `MPLBACKEND`),
  and the reminder that `BENCHMARK_LOG.md` is the source of truth for any published number.

---

## Appendix — Quick command reference

| Action | Command |
|--------|---------|
| Create + enter venv | `python3 -m venv .venv && source .venv/bin/activate` |
| Install deps | `python -m pip install -r requirements.txt` |
| Sanity import | `python -c "import context_clock.run"` |
| Tests | `pytest -q` |
| Live Attestor tests | `CONTEXT_CLOCK_LIVE_ATTESTOR=1 pytest tests/test_attestor_memory_live.py` |
| Local rot demo | `python -m context_clock.run --until-rotted --turns 100` |
| Local memory demo | `python -m context_clock.run --memory --turns 100` |
| OpenRouter run | `python -m context_clock.run --provider openrouter --model <slug> --until-rotted --turns 100 --probe-max-tokens 2048` |
| See all knobs | `python -m context_clock.run --help` |

**Known traps:** no `pyproject.toml` → don't use `pip install -e .`; run as a module from repo
root. `openai` in `requirements.txt` is unused. Default git branch is `master`. `.env`,
`results/`, and `*.png` are gitignored by design. Reasoning models need `--probe-max-tokens`
raised or recall reads as 0%.
