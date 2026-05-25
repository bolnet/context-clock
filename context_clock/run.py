"""CLI: run one context-clock session and print the per-turn arc."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .driver import run_memory_session, run_session
from .provider import OllamaProvider
from .report import plot, write_csv


def _load_openrouter_key() -> str:
    """OPENROUTER_API_KEY from env, else from a local gitignored .env. Never logged."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        env = Path(".env")
        if env.exists():
            for line in env.read_text().splitlines():
                if line.strip().startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not found (env var or .env)")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description="context-clock: benchmark the context-decay arc")
    parser.add_argument("--model", default="llama3.2")
    parser.add_argument("--turns", type=int, default=15)
    parser.add_argument("--limit", type=int, default=1024, help="context window cap (num_ctx)")
    parser.add_argument("--cadence", type=int, default=3, help="probe every N turns")
    parser.add_argument("--threshold", type=float, default=0.85, help="fraction of limit that triggers compaction")
    parser.add_argument("--no-compaction", action="store_true", help="naive baseline: let it overflow")
    parser.add_argument("--until-rotted", action="store_true", help="rot stress test: no compaction, probe every turn, run until recall stays at 0%% (full rot); --turns is the safety cap")
    parser.add_argument("--memory", action="store_true", help="memory backend: retrieve relevant fact, keep context flat")
    parser.add_argument("--pad-repeat", type=int, default=4, help="haystack size dial per memo (×16 words ≈ tokens/turn); raise to fill big windows")
    parser.add_argument("--timeout", type=float, default=120.0, help="per-call timeout (s); raise for big windows / cold loads")
    parser.add_argument("--provider", choices=["ollama", "openrouter", "claude-cli"], default="ollama", help="local Ollama, an OpenRouter API model, or Claude via the `claude` CLI (subscription, no API key)")
    parser.add_argument("--client-window", type=int, default=None, help="truncate each sent prompt to N tokens client-side (gives API models a num_ctx-like window)")
    parser.add_argument("--probe-max-tokens", type=int, default=16, help="answer-token budget per probe; raise (e.g. 2048) for reasoning models that think before answering")
    parser.add_argument("--out", default="results", help="directory for CSV + chart")
    args = parser.parse_args()

    if args.provider == "openrouter":
        from .openai_provider import OpenAICompatProvider
        provider = OpenAICompatProvider(model=args.model, api_key=_load_openrouter_key(), timeout=args.timeout)
    elif args.provider == "claude-cli":
        from .claude_cli_provider import ClaudeCliProvider
        provider = ClaudeCliProvider(model=args.model, timeout=args.timeout)
    else:
        provider = OllamaProvider(model=args.model, num_ctx=args.limit, timeout=args.timeout)

    if args.memory:
        rows = run_memory_session(provider, turns=args.turns, cadence=args.cadence, pad_repeat=args.pad_repeat)
    elif args.until_rotted:
        # pure rot stress: no compaction, probe every turn, stop on sustained 0% recall
        rows = run_session(
            provider,
            turns=args.turns,
            limit=args.limit,
            cadence=1,
            compaction_enabled=False,
            pad_repeat=args.pad_repeat,
            stop_when_rotted=True,
            client_window=args.client_window,
            probe_max_tokens=args.probe_max_tokens,
        )
    else:
        rows = run_session(
            provider,
            turns=args.turns,
            limit=args.limit,
            cadence=args.cadence,
            threshold=args.threshold,
            compaction_enabled=not args.no_compaction,
            pad_repeat=args.pad_repeat,
            client_window=args.client_window,
            probe_max_tokens=args.probe_max_tokens,
        )

    if args.until_rotted:
        print(f"\nmodel={args.model}  limit={args.limit}  mode=until-rotted (no compaction, probe every turn)\n")
    elif args.memory:
        print(f"\nmodel={args.model}  limit={args.limit}  mode=memory\n")
    else:
        print(f"\nmodel={args.model}  limit={args.limit}  cadence={args.cadence}  threshold={args.threshold}  compaction={not args.no_compaction}\n")
    print(f"{'turn':>4} {'ctx_tok':>8} {'used':>7} {'prompt':>7} {'compl':>6} {'cum_tok':>9} {'recall':>7}  event")
    print("-" * 66)
    for r in rows:
        recall = "" if r.recall is None else f"{r.recall * 100:3.0f}%"
        event = "  <<< SELF-COMPACT" if r.compaction_event else ""
        print(
            f"{r.turn:>4} {r.context_tokens:>8} {r.turn_tokens:>7} {r.prompt_tokens:>7} "
            f"{r.completion_tokens:>6} {r.cumulative_tokens:>9} {recall:>7}{event}"
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.memory:
        mode = "memory"
    elif args.until_rotted:
        mode = f"rot_ctx{args.limit}"
    else:
        mode = f"ctx{args.limit}"
    tag = f"{args.model.replace(':', '_').replace('/', '_')}_{mode}"
    csv_path = out_dir / f"{tag}.csv"
    chart_path = out_dir / f"{tag}.png"
    write_csv(rows, csv_path)
    plot(rows, chart_path, title=f"context-clock — {args.model} (limit {args.limit})", limit=args.limit)
    print(f"\nwrote {csv_path}\nwrote {chart_path}")


if __name__ == "__main__":
    main()
