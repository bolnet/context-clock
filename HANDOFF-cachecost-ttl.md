# Handoff — cachecost TTL experiment (in flight)

**Written 2026-08-28, mid-experiment. Delete this file once the results are in
`BENCHMARK_LOG.md`.**

## What is running right now

**Turn counts are no longer fixed at all — both arms run `--until-complete`.**
The model ends the session by replying `GAME COMPLETE`, which the harness only
honours over a **green suite** (it re-runs pytest to check). Safety cap
`--max-turns 16` = the 13 scripted turns plus up to 3 open-ended ones. A detached
(`nohup`, PPID 1) chain that survives session exit: busy first, then sawtooth.

```
# arm 1, running now
.venv/bin/python -m context_clock.cachecost.run --task snake --until-complete --max-turns 16 \
  --model claude-sonnet-5 --provider openrouter --policy busy \
  --ttl 5m --max-tokens 16384 --capture-context --tag cache-snakeopen-sonnet-busy

# arm 2, starts automatically when arm 1 exits
.venv/bin/python -m context_clock.cachecost.run --task snake --until-complete --max-turns 16 \
  --model claude-sonnet-5 --provider openrouter --policy sawtooth --idle 420 \
  --ttl 5m --max-tokens 16384 --capture-context --tag cache-snakeopen-sonnet-sawtooth
```

Sawtooth idles **420s before each of turns 1-12** — 12 pauses, **84 min of pure
waiting** — plus the work itself. Expect **~45-60 min for busy** and **~2.2h for
sawtooth**: roughly **3.2h end to end**. Estimated **$18-22** for the pair.

Check progress:

```bash
pgrep -f cachecost.run                                   # empty = both finished
tail -3 results/cache-snakeopen-sonnet-busy.log
tail -3 results/cache-snakeopen-sonnet-sawtooth.log
grep -c MISS results/cache-snakeopen-sonnet-sawtooth.log   # expect ~12
```

Logs end with `BUSY_EXIT=N` / `SAWTOOTH_EXIT=N`; 0 = suite green, 1 = red.
A red suite is still valid cost data — see the no-fabrication rule.

**13 turns = 12 turn boundaries = ~12 forced misses** (the 8-turn version would
have given 7). It also completes the whole game: `ai.py` and `replay.py` and the
review pass all fall inside 13 turns but outside 8.

## The experiment

Matched pair, identical task/model/prompts, **only the clock differs**. Both arms
started from an empty workspace.

| arm | policy | turns | idle between turns | status |
|---|---|---|---|---|
| control | `busy` | model-decided, cap 16 | none | running |
| treatment | `sawtooth` | model-decided, cap 16 | **420s** (> the 300s TTL) | queued behind it |

**Read this before comparing the arms.** Because each arm now stops when the model
says so, the two may run *different numbers of turns*. That makes them no longer a
controlled pair: session length becomes a second variable alongside the clock. The
only honest comparison is then **cost per completed task**, which this log already
argues for. If you need a clean busy-vs-sawtooth cost delta, re-run both with a
fixed `--turns N` and drop `--until-complete`. The **miss count** stays valid either
way — it is per turn boundary, not per session.

A completed **8-turn busy run** is preserved separately as
`results/cache-snake-sonnet-busy-t8.{log,csv}` — a valid shorter datapoint, but
**not** the control for the 13-turn sawtooth arm. Do not compare across lengths.

**Prediction under test:** control 0 misses, treatment ~7 (one per turn boundary),
each rewriting the whole prefix at 1.25x instead of reading it at 0.1x. The miss
*count* is categorical and survives the +-58% run-to-run cost noise; the cost delta
is what the matched control licenses.

Lands claims **C11, C18, C20, C21** — currently arithmetic, not measurement.

## Earlier 8-turn busy run (kept as a shorter datapoint, NOT the matched control)

8 turns -> **63 requests**, 22.2 min, **suite green**, billed **$2.9624**
(price card agreement 100.00%).

| bucket | cost | share | tokens |
|---|---|---|---|
| output | $1.5213 | 51.4% | 152,134 |
| cache reads | $0.9543 | 32.2% | 4,771,639 |
| cache writes | $0.4865 | 16.4% | 194,598 |
| uncached input | $0.0003 | 0.0% | 126 |

**0 misses / 63.** Hit rate 96.1%. Without caching $11.4541 (3.9x). Naive estimate
$0.3918 (understates 7.6x). Peak context 195,915; cumulative 5,118,497;
re-read factor 26.1x. Read rate recovered from billing $0.200/Mtok r2=1.000.
Cumulative cost vs turn: linear r2 0.922, **quadratic r2 0.988**.

## What to do when it finishes

1. Read the summary: `sed -n '/^task=snake/,$p' results/cache-snake-sonnet-sawtooth.log`
2. Confirm the miss count and, per miss, **how big the rewritten prefix was** — the
   cost of a miss scales with context, so miss 7 should cost far more than miss 1.
   That progression is the part arithmetic cannot confirm.
3. Record it in `BENCHMARK_LOG.md` under the TTL section (the design is already
   written there; only results are missing).
4. Update `CACHECOST_METHODOLOGY.md` §6 and §11a — both currently say the TTL has
   **not** been exercised in a completed run. Fix that, and update §13 limits.
5. Update `CACHE_CLAIMS.md` verdicts for C11/C18/C20/C21 from derived to measured.

## Open threads

- **Third arm not run: the heartbeat.** Bridging idle with cheap refresh reads
  instead of eating the miss. Break-even is exactly 12.5 reads per write, so a
  4-minute heartbeat pays for itself for ~50 min of idling. Claims C12/C22, still
  arithmetic. `provider.warm()` already exists for this.
- **13 turns is now being measured** after two aborted attempts ($0.7288 + $0.0764
  forfeited). If it is stopped again, the CSV and summary are written **only on
  completion** — kill it and you keep only the log lines and the per-request
  context capture.
- **Cache outlives the process** — a rerun inside the TTL inherits the previous
  run's warm prefix and its request 0 bills as a read, not a cold write. Observed
  this session. Any cold-start figure must come from a run launched outside the TTL.

## Ground rules that apply to whatever comes next

- Publish **rates, shares and invariants**; never an absolute session cost.
  Three identical minesweeper runs spanned **+57.8%** ($0.8992 / $1.1745 / $1.4187).
- The sawtooth arm is a **worst case, not a typical case** — it forces expiry at
  *every* boundary. Busy is the floor, sawtooth the ceiling; real sessions land
  between. Do not report it as "the cost of a normal session".
- Every number in README/REPORT/docs must trace to `BENCHMARK_LOG.md`.
