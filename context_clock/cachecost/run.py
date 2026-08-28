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
from .openrouter_provider import SLUGS, OpenRouterCacheProvider
from .bench import (
    POLICIES,
    busy_policy,
    find_lookback_misses,
    sawtooth_policy,
    summarize,
    write_records_csv,
)
from .analysis import summarize_scaling
from .pricing import PRICES
from .tasks import get_task
from .tools import Workspace


def _load_openrouter_key(env_path: str = ".env") -> str:
    """OPENROUTER_API_KEY from env, else from a local gitignored .env. Never logged."""
    import os

    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        env = Path(env_path)
        if env.exists():
            for line in env.read_text().splitlines():
                if line.strip().startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not found (env var or .env)")
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m context_clock.cachecost.run",
        description="Run a real coding task and measure its prompt-cache economics.",
    )
    parser.add_argument("--task", default="minesweeper")
    parser.add_argument(
        "--turns", type=int, default=None,
        help="session length; extends past the scripted turns with further "
             "increments so cost can be plotted against turns (default: scripted)",
    )
    parser.add_argument(
        "--provider", default="openrouter", choices=["openrouter", "anthropic"],
        help="openrouter reports real billed cost and derives cache writes from it; "
             "anthropic reports the write count directly (needs ANTHROPIC_API_KEY)",
    )
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
    parser.add_argument(
        "--until-complete", action="store_true",
        help="let the MODEL end the session: it keeps drawing cycle turns until "
             "it declares the build finished over a green suite, or --max-turns. "
             "Two such runs may differ in length, so they are no longer a "
             "controlled pair — compare them by cost per completed task.",
    )
    parser.add_argument(
        "--max-turns", type=int, default=30,
        help="safety cap for --until-complete (default: 30)",
    )
    parser.add_argument(
        "--capture-context", action="store_true",
        help="dump the exact conversation behind every datapoint to results/<tag>-context/",
    )
    args = parser.parse_args(argv)

    task = get_task(args.task)
    tag = args.tag or f"cache-{args.task}-{args.model}-{args.policy}"
    workspace_path = Path(args.workspace or f"results/{tag}-workspace")
    workspace = Workspace(workspace_path)

    policy = (
        busy_policy() if args.policy == "busy" else sawtooth_policy(args.idle)
    )

    if args.provider == "anthropic":
        provider = AnthropicProvider(model=args.model, api_key=load_api_key())
    else:
        provider = OpenRouterCacheProvider(
            model=args.model, api_key=_load_openrouter_key()
        )

    estimated_minutes = (task.n_turns - 1) * args.idle / 60 if args.policy == "sawtooth" else 0
    print(
        f"task={task.name}  model={args.model}  via {args.provider}  "
        f"policy={args.policy}  ttl={args.ttl}\n"
        + (f"open-ended: the model ends the run, capped at {args.max_turns} turns"
           if args.until_complete
           else f"{len(task.prompts(args.turns))} user turns")
        + f" · workspace {workspace_path}"
        + (f"\nidling {args.idle:.0f}s between turns (~{estimated_minutes:.0f} min of waiting)"
           if estimated_minutes else "")
        + "\n"
    )

    def echo(record) -> None:
        marker = "MISS" if record.cache_read == 0 and record.index > 0 else "hit "
        print(
            f"  req {record.index:>3} t{record.turn} {marker}"
            f" ctx {record.context_tokens:>7,}"
            f" cum {record.cumulative_tokens:>8,}"
            f" | wr {record.cache_creation:>6,} rd {record.cache_read:>7,}"
            f" out {record.output_tokens:>5,}"
            f" | ${(record.cost or 0):.5f} cum ${record.cumulative_cost:.4f}"
            f" | gap {record.gap:>5.1f}s blk {record.blocks_added:>2}"
            f" {'|'.join(record.tool_calls) or record.stop_reason}",
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
        capture_dir=f"results/{tag}-context" if args.capture_context else None,
        turns=args.turns,
        until_complete=args.until_complete,
        max_turns=args.max_turns,
    )

    print("\n" + summarize(run, args.model))
    print("\n" + summarize_scaling(run, args.model))

    csv_path = write_records_csv(run, f"results/{tag}.csv")
    print(f"\n  per-request rows -> {csv_path}")
    if args.capture_context:
        print(f"  per-request context -> results/{tag}-context/")

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
