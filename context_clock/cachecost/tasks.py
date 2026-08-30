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

Deliberately an unglamorous, self-contained problem: the point is to measure
the cache on a task nobody tuned for it.

``SNAKE`` is the second workload, and it *is* Snake — added on purpose, with
that tradeoff understood. It exists for two reasons minesweeper cannot serve:

* **Length.** 13 scripted turns across four modules, where minesweeper has 6 in
  one file. Cost is a function of session length, so measuring the TTL cliff
  and the cost-vs-turns curve needs a session long enough to have a curve.
* **A second workload.** Two unrelated tasks guard against a result that is
  really a property of one problem. Minesweeper is the short-session check;
  snake is the long-session one. Neither is reported as the other.

The modules are split (``snake.py``, ``levels.py``, ``ai.py``, ``replay.py``)
because ``write_file`` re-sends whole files: a single module that outgrows
``--max-tokens`` truncates mid-write and burns the turn, which is how a
recorded run ended red.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What the model must reply, alone, when it judges the build genuinely finished.
#: Checked by the harness to end an open-ended run — see ``Task.prompts``.
COMPLETION_SENTINEL = "GAME COMPLETE"

#: Appended to cycle prompts in an open-ended run. Only ever attached past the
#: scripted turns: the script is real work and must not be short-circuited.
COMPLETION_CLAUSE = (
    " If the game is genuinely feature-complete, the suite is green, and you "
    "can find nothing worth adding, reply with exactly " + COMPLETION_SENTINEL +
    " and call no tools."
)

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
    """A benchmark workload: an opening brief, scripted follow-ups, then a cycle.

    Cost in an agent session is a function of session *length*, so a fixed
    turn count measures one point on a curve. ``cycle`` lets a run extend past
    the scripted turns with further genuine increments, so cost can be plotted
    against turns the way context-clock plots tokens against turns.
    """

    name: str
    brief: str
    followups: tuple[str, ...]
    #: Repeatable increments used once ``followups`` is exhausted. Each still
    #: asks for real work, so a longer run is a longer session and not idling.
    cycle: tuple[str, ...] = ()

    @property
    def n_turns(self) -> int:
        return 1 + len(self.followups)

    def prompts(
        self, turns: int | None = None, *, open_ended: bool = False
    ) -> tuple[str, ...]:
        """The prompt sequence for a run of ``turns`` turns.

        Beyond the scripted follow-ups the cycle repeats, numbered so each turn
        is a distinct request rather than a byte-identical repeat — a repeated
        prompt would be answered from cache and stop being real work.

        ``open_ended`` appends the completion clause to the **cycle** prompts
        only, so the model can end the run itself once it judges the build
        finished. The scripted turns never carry it: they are the defined
        workload, and letting the model skip them would make two runs of the
        same task incomparable.
        """
        scripted = (self.brief, *self.followups)
        if turns is None or turns <= len(scripted):
            return scripted[: turns or len(scripted)]
        if not self.cycle:
            raise ValueError(
                f"task {self.name!r} has no cycle, so it cannot extend past "
                f"{len(scripted)} turns"
            )
        out = list(scripted)
        for i in range(turns - len(scripted)):
            extra = f"(round {i // len(self.cycle) + 2}) {self.cycle[i % len(self.cycle)]}"
            out.append(extra + COMPLETION_CLAUSE if open_ended else extra)
        return tuple(out)


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
    cycle=(
        "Pick the weakest area of test coverage and add at least three tests "
        "for it that do not duplicate an existing test. Run the tests.",
        "Find a method that could be clearer or more efficient, refactor it, "
        "and confirm the tests still pass. Run the tests.",
        "Add one new query method to Board that a UI would need and is not "
        "there yet, with tests. Run the tests.",
        "Look for an unhandled edge case in the existing code, fix it, and add "
        "a regression test. Run the tests.",
    ),
)

SNAKE = Task(
    name="snake",
    brief="""Build a headless Snake game engine in `snake.py`.

This is a multi-module project. Keep each module focused and under ~300 lines:
you will add `levels.py`, `ai.py` and `replay.py` in later turns, so do not put
everything in one file.

Requirements for `snake.py`:
- `Game(width, height, seed)` starts a snake of length 3 in the middle facing
  east, and places the first food deterministically for a given seed.
- The snake is an ordered sequence of (x, y) cells, head first.
- `game.step(direction=None)` advances one tick. `direction` is one of
  "up"/"down"/"left"/"right" or None to continue straight.
- Eating food grows the snake by one and spawns new food on a random free
  cell, chosen deterministically from the seed. Food never spawns on the snake.
- Running into a wall or into the snake's own body ends the game.
- A 180-degree reversal is ignored, not fatal.
- `game.score`, `game.ticks`, `game.state` ("playing" or "over").
- Invalid directions and non-positive dimensions raise `ValueError`.

Also write `test_snake.py` covering: deterministic food placement for a fixed
seed, movement in all four directions, growth on eating, the reversal being
ignored, wall collision, self collision, and the validation errors.

Start now: write both files, then run the tests.""",
    followups=(
        "Add a buffered input queue to `snake.py`: `game.enqueue(direction)` "
        "stores up to 3 pending direction changes, and each tick consumes one. "
        "This stops fast inputs being dropped when several arrive inside one "
        "tick. A queued reversal is still ignored when it is consumed. Add "
        "tests for buffering two turns across two ticks, the queue capping at "
        "3, and a queued reversal being skipped. Run the tests.",

        "Add scoring and a speed curve to `snake.py`: each food is worth 10 "
        "points plus a bonus for eating it in fewer than 20 ticks since the "
        "last one, and `game.tick_interval` starts at 200ms and drops 5% per "
        "food to a floor of 60ms. Add tests for the base score, the speed "
        "bonus, the interval decay, and the floor being respected. Run the "
        "tests.",

        "Add `game.render()` to `snake.py`, returning the board as a "
        "multi-line string: '#' for wall border, 'O' for the head, 'o' for "
        "body, '*' for food and ' ' for empty. Add tests for a fresh board, a "
        "board after several moves, and a finished game. Run the tests.",

        "Add `Game.from_layout(rows, seed=0)` to `snake.py`, building a game "
        "from a list of strings using the same characters `render()` emits, so "
        "tests can pin an exact position instead of stepping a seeded game "
        "into place. The snake's body order is given by a separate `body` "
        "argument listing its cells head first. Rewrite at least three "
        "existing tests to use it. Run the tests.",

        "Add wrap-around mode to `snake.py`: `Game(..., wrap=True)` makes the "
        "snake re-enter the opposite edge instead of dying on a wall. Self "
        "collision still ends the game, and `render()` draws no wall border in "
        "wrap mode. Add tests for wrapping on all four edges, self collision "
        "still being fatal while wrapped, and the default staying wrap=False. "
        "Run the tests.",

        "Create `levels.py`: a `Level` holding a set of obstacle cells, "
        "`Level.from_layout(rows)` reading 'X' as an obstacle, and at least "
        "three built-in levels of increasing difficulty. Wire it into "
        "`snake.py` via `Game(..., level=None)`: obstacles are fatal on "
        "contact, food never spawns on one, and `render()` draws them as 'X'. "
        "Add tests in `test_levels.py` for the layout parser, each built-in "
        "level being non-empty and inside its bounds, fatal contact, and food "
        "avoiding obstacles. Run the tests.",

        "Add bonus food to `snake.py`: after every 5 normal foods a bonus "
        "food appears on a free cell, is worth 50 points, and disappears if "
        "not eaten within 30 ticks. Only one bonus may exist at a time and "
        "`render()` draws it as '$'. Add tests for the spawn cadence, the "
        "score, the expiry, and that a bonus never overlaps the snake, the "
        "normal food or an obstacle. Run the tests.",

        "Create `replay.py`: `record(game)` captures the seed, dimensions, "
        "options and the direction consumed on every tick; `to_json(recording)` "
        "and `from_json(text)` round-trip it; `replay(recording)` re-runs it "
        "and returns the finished game. Add `test_replay.py` proving a "
        "recorded game replays to an identical final snake, score and tick "
        "count, that the JSON round-trips exactly, and that a recording with a "
        "corrupted tick list raises a clear error. Run the tests.",

        "Create `ai.py`: `next_direction(game)` picking a move by breadth-first "
        "search from the head to the food around the snake, obstacles and "
        "walls, with a deterministic tiebreak so the same position always "
        "gives the same move. If no path exists, move to the neighbouring free "
        "cell with the most reachable space. Add `test_ai.py` proving it takes "
        "the shortest path on an open board, routes around an obstacle, never "
        "returns a fatal or reversing move, and survives at least 100 ticks on "
        "a seeded 10x10 board. Run the tests.",

        "Add a safety layer to `ai.py`: before committing to the BFS move, "
        "check that after taking it the snake can still reach its own tail; if "
        "not, prefer a move that keeps the tail reachable, and fall back to "
        "following the tail when no food route is safe. Add tests for a "
        "position where the greedy food move traps the snake and the safe move "
        "does not, and that the AI now survives at least 400 ticks on a seeded "
        "8x8 board. Run the tests.",

        "Add rewind to `snake.py`: the game keeps an internal stack of the "
        "last 50 states and `game.undo()` restores the previous one exactly — "
        "snake, food, bonus, score, ticks, tick_interval, queue and state — "
        "returning False when there is nothing to undo. Prove exactness by "
        "stepping, undoing and comparing `render()` plus every public "
        "attribute. Add tests for a single undo, 50 chained undos, the cap "
        "discarding the oldest, and undoing past a game-over. Run the tests.",

        "Review all four modules. Add docstrings to every public class and "
        "method, make sure every public method validates its arguments and "
        "raises ValueError with a clear message, and add any test you think is "
        "missing for an edge case — especially interactions between features "
        "(wrap plus obstacles, bonus food plus undo, AI on a wrapped board). "
        "Run the tests one final time.",
    ),
    cycle=(
        "Pick the weakest area of test coverage across the four modules and "
        "add at least three tests for it that do not duplicate an existing "
        "test. Run the tests.",
        "Find a method that could be clearer or more efficient, refactor it, "
        "and confirm the tests still pass. Run the tests.",
        "Add one new capability a real Snake front-end would need and is not "
        "there yet, with tests. Run the tests.",
        "Look for an unhandled edge case in the existing code, fix it, and add "
        "a regression test. Run the tests.",
    ),
)

TASKS: dict[str, Task] = {t.name: t for t in (MINESWEEPER, SNAKE)}


def get_task(name: str) -> Task:
    try:
        return TASKS[name]
    except KeyError:
        known = ", ".join(sorted(TASKS))
        raise KeyError(f"unknown task {name!r}; known: {known}") from None
