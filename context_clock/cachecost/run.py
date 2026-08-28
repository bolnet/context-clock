"""CLI — run the real coding task and measure what the prompt cache did.

    # the natural session: the agent works straight through
    python -m context_clock.cachecost.run --policy busy

    # the same task, idling past the TTL between turns
    python -m context_clock.cachecost.run --policy sawtooth --idle 420

Both write per-request rows to ``results/`` so every published figure traces
back to the request that produced it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .anthropic_provider import AnthropicProvider, load_api_key
from .agent import run_session
from .bench import (
    POLICIES,
    busy_policy,
    find_lookback_misses,
    sawtooth_policy,
    summarize,
    write_records_csv,
)
from .pricing import PRICES
from .tasks import get_task
from .tools import Workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m context_clock.cachecost.run",
        description="Run a real coding task and measure its prompt-cache economics.",
    )
    parser.add_argument("--task", default="minesweeper")
    parser.add_argument("--model", default="claude-sonnet-5", choices=sorted(PRICES))
    parser.add_argument("--policy", default="busy", choices=sorted(POLICIES))
    parser.add_argument(
        "--idle", type=float, default=420.0,
        help="seconds to idle between turns under --policy sawtooth (default: 420, past the 5m TTL)",
    )
    parser.add_argument("--ttl", default="5m", choices=["5m", "1h"])
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--workspace", default=None, help="where the agent builds (default: a fresh dir under results/)")
    parser.add_argument("--tag", default=None, help="name for the output CSV")
    args = parser.parse_args(argv)

    task = get_task(args.task)
    tag = args.tag or f"cache-{args.task}-{args.model}-{args.policy}"
    workspace_path = Path(args.workspace or f"results/{tag}-workspace")
    workspace = Workspace(workspace_path)

    policy = (
        busy_policy() if args.policy == "busy" else sawtooth_policy(args.idle)
    )

    provider = AnthropicProvider(model=args.model, api_key=load_api_key())

    estimated_minutes = (task.n_turns - 1) * args.idle / 60 if args.policy == "sawtooth" else 0
    print(
        f"task={task.name}  model={args.model}  policy={args.policy}  ttl={args.ttl}\n"
        f"{task.n_turns} user turns · workspace {workspace_path}"
        + (f"\nidling {args.idle:.0f}s between turns (~{estimated_minutes:.0f} min of waiting)"
           if estimated_minutes else "")
        + "\n"
    )

    def echo(record) -> None:
        marker = "MISS" if record.cache_read == 0 and record.index > 0 else "hit "
        print(
            f"  req {record.index:>3}  turn {record.turn}  {marker}"
            f"  write {record.cache_creation:>7,}  read {record.cache_read:>8,}"
            f"  out {record.output_tokens:>5,}  gap {record.gap:>6.1f}s"
            f"  blocks {record.blocks_added:>3}"
            f"  {'|'.join(record.tool_calls) or record.stop_reason}",
            flush=True,
        )

    run = run_session(
        task,
        provider,
        workspace,
        policy=policy,
        policy_name=args.policy,
        max_tokens=args.max_tokens,
        cache_ttl=args.ttl,
        on_record=echo,
    )

    print("\n" + summarize(run, args.model))

    csv_path = write_records_csv(run, f"results/{tag}.csv")
    print(f"\n  per-request rows -> {csv_path}")

    lookback = find_lookback_misses(run)
    if lookback:
        print(
            f"\n  M1 observed: {len(lookback)} cache miss(es) the clock cannot explain "
            f"(requests {[r.index for r in lookback]}) — the preceding turn appended "
            "more than 20 content blocks."
        )
    else:
        print("\n  M1 not triggered in this run (no >20-block turn followed by a miss).")

    return 0 if run.tests_passed else 1


if __name__ == "__main__":
    sys.exit(main())
