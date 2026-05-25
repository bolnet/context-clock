"""Report — per-turn CSV (pure, tested) + the hero chart (matplotlib).

The chart tells all four stages in one figure:
  top    — context tokens filling toward the limit, with compaction markers,
           plus recall %% decaying on a twin axis
  bottom — cumulative tokens (the exhaustion / cost curve)
"""

from __future__ import annotations

import csv
from pathlib import Path

from .driver import TurnRow

CSV_HEADER = [
    "turn",
    "context_tokens",
    "cumulative_tokens",
    "turn_tokens",
    "prompt_tokens",
    "completion_tokens",
    "recall",
    "compaction_event",
    "turn_cost",
    "cumulative_cost",
]


def write_csv(rows: list[TurnRow], path: str | Path) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow(
                [
                    row.turn,
                    row.context_tokens,
                    row.cumulative_tokens,
                    row.turn_tokens,
                    row.prompt_tokens,
                    row.completion_tokens,
                    "" if row.recall is None else row.recall,
                    int(row.compaction_event),
                    row.turn_cost,
                    row.cumulative_cost,
                ]
            )


def plot(rows: list[TurnRow], path: str | Path, *, title: str, limit: int | None = None) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    turns = [r.turn for r in rows]
    context = [r.context_tokens for r in rows]
    cumulative = [r.cumulative_tokens for r in rows]
    probe_turns = [r.turn for r in rows if r.recall is not None]
    recall_pct = [r.recall * 100 for r in rows if r.recall is not None]
    compaction_turns = [r.turn for r in rows if r.compaction_event]

    fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)

    # Top: context fill + recall decay + compaction markers
    ax_top.plot(turns, context, color="tab:blue", marker="o", label="context tokens")
    if limit is not None:
        ax_top.axhline(limit, color="red", ls="--", lw=1, label="context limit")
    ax_top.set_ylabel("context tokens", color="tab:blue")
    ax_top.tick_params(axis="y", labelcolor="tab:blue")

    ax_recall = ax_top.twinx()
    ax_recall.plot(probe_turns, recall_pct, color="tab:green", marker="s", label="recall %")
    ax_recall.set_ylabel("recall %", color="tab:green")
    ax_recall.set_ylim(0, 105)
    ax_recall.tick_params(axis="y", labelcolor="tab:green")

    for index, ct in enumerate(compaction_turns):
        ax_top.axvline(ct, color="orange", ls=":", alpha=0.8, label="self-compact" if index == 0 else None)
    ax_top.set_title(title)
    ax_top.legend(loc="upper left", fontsize=8)

    # Bottom: cumulative token exhaustion
    ax_bot.plot(turns, cumulative, color="tab:purple", marker="o")
    ax_bot.set_ylabel("cumulative tokens")
    ax_bot.set_xlabel("turn")
    ax_bot.set_title("token exhaustion (cumulative cost)")

    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
