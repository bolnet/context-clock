"""The benchmark workload — a real coding task, built turn by turn.

Measuring prompt-cache economics needs a session that behaves like a real one:
a growing conversation, tool results that pile up inside a single turn, and a
finish line the harness can check without asking the model.

The task is a **headless Minesweeper engine**. It earns its place:

* Pure logic, no rendering, no input loop — so a test suite is the ground truth
  for "done", not a screenshot or the agent's own say-so.
* Seeded mine placement makes every run reproducible.
* Flood-fill reveal, adjacency counts, flagging and win/lose detection are
  enough real work to take several turns and many tool calls — which is the
  point, because that is what fills a cached prefix.

Deliberately *not* the Snake game from the talk being validated: reproducing
their exact workload would test whether we can copy a session, not whether the
mechanism holds on a task nobody tuned for it.
"""

from __future__ import annotations

from dataclasses import dataclass

SYSTEM_PROMPT = """You are a software engineer working in a sandboxed workspace.

You have four tools: write_file, read_file, list_files and run_tests.

Work in small, verifiable steps. Write code, run the tests, read the failures,
fix them, and run the tests again. Keep going until the suite passes.

Rules:
- Write complete files. write_file overwrites, so always send the whole file.
- Never claim the tests pass without running run_tests and reading the output.
- Do not ask the user questions. You have everything you need; make a decision
  and proceed.
- When the suite is green, reply with exactly: TASK COMPLETE
"""


@dataclass(frozen=True)
class Task:
    """A benchmark workload: an opening brief plus follow-up turns."""

    name: str
    brief: str
    followups: tuple[str, ...]

    @property
    def n_turns(self) -> int:
        return 1 + len(self.followups)


MINESWEEPER = Task(
    name="minesweeper",
    brief="""Build a headless Minesweeper engine in `minesweeper.py`.

Requirements:
- `Board(width, height, mine_count, seed)` places exactly `mine_count` mines
  deterministically for a given seed.
- `board.adjacent(x, y)` returns how many of the eight neighbours hold a mine.
- `board.reveal(x, y)` reveals a cell. Revealing a cell whose adjacent count is
  zero recursively reveals its neighbours (flood fill). Revealing a mine loses
  the game.
- `board.flag(x, y)` toggles a flag. A flagged cell cannot be revealed.
- `board.state` returns one of "playing", "won", "lost". The game is won when
  every non-mine cell is revealed.
- Out-of-bounds coordinates raise `ValueError`.

Also write `test_minesweeper.py` covering: deterministic placement for a fixed
seed, adjacency counts, flood fill across a zero region, flagging blocking a
reveal, the lose condition, the win condition, and the bounds errors.

Start now: write both files, then run the tests.""",
    followups=(
        "Add `board.chord(x, y)`: on an already-revealed cell whose adjacent "
        "count equals the number of flags around it, reveal all its unflagged "
        "neighbours. If any of those is a mine, the game is lost. Add tests for "
        "the chord succeeding, the chord being a no-op when the flag count does "
        "not match, and a chord that loses the game. Run the tests.",
        "Add `board.render()` returning the board as a multi-line string: '#' "
        "for hidden, 'F' for flagged, '.' for a revealed zero, the digit for a "
        "revealed count, and '*' for a revealed mine. Add tests for a fresh "
        "board, a partially revealed board, and a lost board. Run the tests.",
        "Add `Board.from_layout(rows)` building a board from a list of strings "
        "where '*' marks a mine and '.' marks an empty cell, so tests can pin "
        "an exact layout without relying on the seed. Rewrite at least three "
        "existing tests to use it. Run the tests.",
        "Add `board.solve_step()`: apply the two basic Minesweeper deductions "
        "once. If a revealed cell's adjacent count equals its hidden neighbour "
        "count, flag those neighbours; if its count equals its flag count, "
        "reveal the unflagged ones. Return True if anything changed. Add tests "
        "for both deductions and for the no-op case. Run the tests.",
        "Review the whole module. Add docstrings, make sure every public method "
        "validates its coordinates, and add any test you think is missing for "
        "an edge case. Run the tests one final time.",
    ),
)

TASKS: dict[str, Task] = {MINESWEEPER.name: MINESWEEPER}


def get_task(name: str) -> Task:
    try:
        return TASKS[name]
    except KeyError:
        known = ", ".join(sorted(TASKS))
        raise KeyError(f"unknown task {name!r}; known: {known}") from None
