"""The benchmark workloads themselves.

A task is measurement apparatus: if its prompt sequence is wrong, every cost
figure downstream is measuring the wrong session. These tests pin the shape a
task must have — length, truncation, cycle extension — and the one structural
property the long workload depends on: it must ask for **several modules**, not
one giant file, because ``write_file`` re-sends whole files and a module larger
than ``--max-tokens`` truncates mid-write and burns the turn.
"""

import pytest

from context_clock.cachecost.tasks import MINESWEEPER, SNAKE, TASKS, Task, get_task


def test_snake_is_registered():
    assert get_task("snake") is SNAKE
    assert SNAKE.name == "snake"


def test_unknown_task_lists_the_known_ones():
    with pytest.raises(KeyError) as exc:
        get_task("pacman")
    assert "snake" in str(exc.value)
    assert "minesweeper" in str(exc.value)


def test_snake_is_the_long_workload():
    """It exists to be longer than minesweeper — that is its whole purpose."""
    assert SNAKE.n_turns > MINESWEEPER.n_turns
    assert SNAKE.n_turns >= 12


def test_snake_spreads_work_across_modules():
    """Guards the output-cap truncation that killed a recorded run.

    One monolithic file eventually exceeds max_tokens on every rewrite. The
    brief must name distinct modules so no single write_file is unbounded.
    """
    text = " ".join((SNAKE.brief, *SNAKE.followups))
    named = {m for m in ("snake.py", "levels.py", "ai.py", "replay.py") if m in text}
    assert len(named) >= 4, f"expected several modules, found {named}"


def test_snake_ends_every_turn_by_running_the_tests():
    """The suite is the ground truth for 'done', so every turn must reach it."""
    for prompt in (SNAKE.brief, *SNAKE.followups):
        assert "run the tests" in prompt.lower()


def test_prompts_defaults_to_the_scripted_sequence():
    assert len(SNAKE.prompts()) == SNAKE.n_turns
    assert SNAKE.prompts()[0] == SNAKE.brief


def test_prompts_truncates_to_a_shorter_run():
    assert SNAKE.prompts(3) == (SNAKE.brief, *SNAKE.followups[:2])


def test_prompts_extends_past_the_script_through_the_cycle():
    """A longer run must still be real work, and never a byte-identical repeat."""
    extended = SNAKE.prompts(SNAKE.n_turns + 2)
    assert len(extended) == SNAKE.n_turns + 2
    assert extended[: SNAKE.n_turns] == SNAKE.prompts()
    assert len(set(extended)) == len(extended)
    assert extended[SNAKE.n_turns].startswith("(round 2)")


def test_a_task_without_a_cycle_refuses_to_extend():
    task = Task(name="t", brief="first", followups=("second",))
    with pytest.raises(ValueError, match="no cycle"):
        task.prompts(5)


def test_every_registered_task_is_self_consistent():
    for name, task in TASKS.items():
        assert task.name == name
        assert task.brief.strip()
        assert task.n_turns == 1 + len(task.followups)
