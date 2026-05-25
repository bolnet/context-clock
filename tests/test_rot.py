"""Rot-until-complete stress test — keep adding documents and probing every
turn, with NO compaction, until recall accuracy bottoms out and stays there.

The stopping predicate (`is_fully_rotted`) is pure and unit-tested here; the
early-stop loop behavior is exercised with a stub provider (no network).
"""

from context_clock.driver import is_fully_rotted, run_session
from context_clock.provider import Completion


class TestIsFullyRotted:
    def test_not_rotted_without_enough_probes(self):
        # need a full streak of recorded recalls before declaring rot
        assert is_fully_rotted([0.0, 0.0], streak=3) is False

    def test_rotted_after_sustained_zero(self):
        assert is_fully_rotted([1.0, 0.0, 0.0, 0.0], streak=3) is True

    def test_ignores_none_acknowledgements(self):
        # None rows (non-probe turns) don't count or reset the streak
        assert is_fully_rotted([0.0, None, 0.0, None, 0.0], streak=3) is True

    def test_not_rotted_if_recall_recovers(self):
        assert is_fully_rotted([0.0, 0.0, 1.0], streak=3) is False

    def test_respects_floor(self):
        # 0.33 sits above the 0.0 floor → not fully rotted
        assert is_fully_rotted([0.0, 0.33, 0.0], streak=3, floor=0.0) is False


class _AlwaysWrong:
    """Stub provider: never returns the right code, so recall is always 0."""

    def complete(self, messages, max_tokens: int = 256) -> Completion:
        return Completion(text="nope", prompt_tokens=10, completion_tokens=1)


class TestRunStopsWhenRotted:
    def test_stops_after_sustained_zero_recall(self):
        rows = run_session(
            _AlwaysWrong(),
            turns=50,            # safety cap, should stop well before this
            limit=1024,
            cadence=1,           # probe every turn
            compaction_enabled=False,
            stop_when_rotted=True,
            rot_streak=3,
        )
        # recall is 0 from turn 1; rot declared after a streak of 3
        assert len(rows) == 3
        assert all(r.recall == 0.0 for r in rows)

    def test_probe_max_tokens_is_configurable(self):
        # reasoning models need a bigger answer budget so the code isn't truncated
        class _RecordMaxTokens:
            def __init__(self):
                self.seen = []

            def complete(self, messages, max_tokens=256):
                self.seen.append(max_tokens)
                return Completion(text="nope", prompt_tokens=10, completion_tokens=1)

        stub = _RecordMaxTokens()
        run_session(stub, turns=3, limit=1024, cadence=1, compaction_enabled=False,
                    stop_when_rotted=True, probe_max_tokens=2048)
        assert stub.seen and all(mt == 2048 for mt in stub.seen)  # every probe used the budget

    def test_records_per_turn_token_usage(self):
        # stub reports prompt=10, completion=1 per call; turn 1 makes one probe call
        rows = run_session(
            _AlwaysWrong(), turns=50, limit=1024, cadence=1,
            compaction_enabled=False, stop_when_rotted=True, rot_streak=3,
        )
        first = rows[0]
        assert first.prompt_tokens == 10
        assert first.completion_tokens == 1
        assert first.turn_tokens == 11   # prompt + completion spent this turn
