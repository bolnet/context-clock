"""CLI: run one context-clock session and print the per-turn arc."""

from __future__ import annotations

import argparse
from pathlib import Path

from .driver import run_memory_session, run_session
from .provider import OllamaProvider
from .report import plot, write_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="context-clock: benchmark the context-decay arc")
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--turns", type=int, default=15)
    parser.add_argument("--limit", type=int, default=1024, help="context window cap (num_ctx)")
    parser.add_argument("--cadence", type=int, default=3, help="probe every N turns")
    parser.add_argument("--threshold", type=float, default=0.85, help="fraction of limit that triggers compaction")
    parser.add_argument("--no-compaction", action="store_true", help="naive baseline: let it overflow")
    parser.add_argument("--memory", action="store_true", help="memory backend: retrieve relevant fact, keep context flat")
    parser.add_argument("--out", default="results", help="directory for CSV + chart")
    args = parser.parse_args()

    provider = OllamaProvider(model=args.model, num_ctx=args.limit)
    if args.memory:
        rows = run_memory_session(provider, turns=args.turns, cadence=args.cadence)
    else:
        rows = run_session(
            provider,
            turns=args.turns,
            limit=args.limit,
            cadence=args.cadence,
            threshold=args.threshold,
            compaction_enabled=not args.no_compaction,
        )

    print(f"\nmodel={args.model}  limit={args.limit}  cadence={args.cadence}  threshold={args.threshold}\n")
    print(f"{'turn':>4} {'ctx_tok':>8} {'cum_tok':>9} {'recall':>7}  event")
    print("-" * 44)
    for r in rows:
        recall = "" if r.recall is None else f"{r.recall * 100:3.0f}%"
        event = "  <<< SELF-COMPACT" if r.compaction_event else ""
        print(f"{r.turn:>4} {r.context_tokens:>8} {r.cumulative_tokens:>9} {recall:>7}{event}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = "memory" if args.memory else f"ctx{args.limit}"
    tag = f"{args.model.replace(':', '_').replace('/', '_')}_{mode}"
    csv_path = out_dir / f"{tag}.csv"
    chart_path = out_dir / f"{tag}.png"
    write_csv(rows, csv_path)
    plot(rows, chart_path, title=f"context-clock — {args.model} (limit {args.limit})", limit=args.limit)
    print(f"\nwrote {csv_path}\nwrote {chart_path}")


if __name__ == "__main__":
    main()
