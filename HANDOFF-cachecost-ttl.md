# Handoff — cachecost TTL experiment (in flight)

**Written 2026-08-28, mid-experiment. Delete this file once the results are in
`BENCHMARK_LOG.md`.**

## What is running right now

A detached (`nohup`, PPID 1) run that survives session exit:

```
.venv/bin/python -m context_clock.cachecost.run --task snake --turns 8 \
  --model claude-sonnet-5 --provider openrouter --policy sawtooth --idle 420 \
  --ttl 5m --max-tokens 16384 --capture-context --tag cache-snake-sonnet-sawtooth
```

It idles **420s between each of the 8 turns** (7 pauses, ~49 min of pure waiting)
plus roughly 25 min of work — so **~75 min end to end** from its start.

Check whether it is still alive:

```bash
pgrep -f "policy sawtooth"                        # empty means finished (or died)
tail -5 results/cache-snake-sonnet-sawtooth.log
grep -c MISS results/cache-snake-sonnet-sawtooth.log
```

The log ends with `SAWTOOTH_EXIT=0` (suite green) or `SAWTOOTH_EXIT=1` (red).
A red suite is still valid cost data — see the no-fabrication rule.

## The experiment

Matched pair, identical task/model/prompts, **only the clock differs**. Both arms
started from an empty workspace.

| arm | policy | idle between turns | status |
|---|---|---|---|
| control | `busy` | none | **DONE** — see below |
| treatment | `sawtooth` | **420s** (> the 300s TTL) | in flight |

**Prediction under test:** control 0 misses, treatment ~7 (one per turn boundary),
each rewriting the whole prefix at 1.25x instead of reading it at 0.1x. The miss
*count* is categorical and survives the +-58% run-to-run cost noise; the cost delta
is what the matched control licenses.

Lands claims **C11, C18, C20, C21** — currently arithmetic, not measurement.

## Arm 1 result (control, `busy`) — already recorded

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
- **The 13-turn Snake run was aborted** at turn 3 ($0.7288 forfeited) when it
  projected $25-35 for the pair. The full 13-turn cost curve is still unmeasured.
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
