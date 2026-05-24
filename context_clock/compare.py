"""Cross-model comparison — load per-model CSVs, summarize, overlay charts.

Holds the workload + config fixed and varies only the model, so differences
are purely model size. ``summarize`` is pure + tested; loading/plotting is I/O.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .driver import TurnRow


def summarize(rows: list[TurnRow]) -> dict:
    recalls = [r.recall for r in rows if r.recall is not None]
    compaction_rows = [r for r in rows if r.compaction_event]
    return {
        "compactions": len(compaction_rows),
        "first_compaction_turn": compaction_rows[0].turn if compaction_rows else None,
        "tokens_before_compaction": compaction_rows[0].cumulative_tokens if compaction_rows else None,
        "final_recall": recalls[-1] if recalls else None,
        "min_recall": min(recalls) if recalls else None,
        "total_tokens": rows[-1].cumulative_tokens if rows else 0,
        "peak_context": max((r.context_tokens for r in rows), default=0),
    }


def load_csv(path: str | Path) -> list[TurnRow]:
    rows: list[TurnRow] = []
    with open(path, newline="") as handle:
        for record in csv.DictReader(handle):
            recall = record["recall"]
            rows.append(
                TurnRow(
                    turn=int(record["turn"]),
                    context_tokens=int(record["context_tokens"]),
                    cumulative_tokens=int(record["cumulative_tokens"]),
                    recall=float(recall) if recall != "" else None,
                    compaction_event=record["compaction_event"] == "1",
                )
            )
    return rows


def _overlay(runs: dict[str, list[TurnRow]], path: str | Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax_recall, ax_cost) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    for label, rows in runs.items():
        probe_turns = [r.turn for r in rows if r.recall is not None]
        recall_pct = [r.recall * 100 for r in rows if r.recall is not None]
        ax_recall.plot(probe_turns, recall_pct, marker="s", label=label)
        ax_cost.plot([r.turn for r in rows], [r.cumulative_tokens for r in rows], marker="o", label=label)

    ax_recall.set_ylabel("recall %")
    ax_recall.set_ylim(0, 105)
    ax_recall.set_title("recall decay by run")
    ax_recall.legend(fontsize=8)
    ax_cost.set_ylabel("cumulative tokens")
    ax_cost.set_xlabel("turn")
    ax_cost.set_title("token cost by run")
    ax_cost.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="compare context-clock runs across models")
    parser.add_argument("csvs", nargs="+", help="per-model result CSVs")
    parser.add_argument("--out", default="results/compare.png")
    args = parser.parse_args()

    runs = {Path(p).stem: load_csv(p) for p in args.csvs}

    header = f"{'run':<26} {'compactions':>11} {'tok_b4_compact':>14} {'total_tok':>10} {'final_recall':>12} {'min_recall':>10}"
    print(header)
    print("-" * len(header))
    for label, rows in runs.items():
        s = summarize(rows)
        fr = "-" if s["final_recall"] is None else f"{s['final_recall'] * 100:.0f}%"
        mr = "-" if s["min_recall"] is None else f"{s['min_recall'] * 100:.0f}%"
        tbc = "-" if s["tokens_before_compaction"] is None else str(s["tokens_before_compaction"])
        print(f"{label:<26} {s['compactions']:>11} {tbc:>14} {s['total_tokens']:>10} {fr:>12} {mr:>10}")

    _overlay(runs, args.out)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
