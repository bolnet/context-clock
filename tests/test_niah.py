"""Needle-in-a-haystack by depth — intra-window long-context degradation.

For big-window API models (no num_ctx truncation knob): build a haystack of a
target token size with a single unpredictable needle at a fractional depth, probe
recall, and sweep (size × depth). Distinct from the local truncation-rot test.
"""

from context_clock.niah import build_haystack, run_niah
from context_clock.provider import Completion


def _needle_fraction(h) -> float:
    words = h.statement.split()
    i = next(k for k, w in enumerate(words) if h.answer in w)
    return i / len(words)


class TestBuildHaystack:
    def test_needle_present_and_deterministic_but_unpredictable(self):
        h = build_haystack(2000, index=7, depth=0.5)
        assert h.answer in h.statement
        assert build_haystack(2000, index=7, depth=0.5).answer == h.answer  # deterministic
        assert h.answer != "k00007"  # not derivable from the index

    def test_depth_controls_needle_position(self):
        early = build_haystack(4000, index=1, depth=0.0)
        late = build_haystack(4000, index=1, depth=1.0)
        assert _needle_fraction(early) < 0.2
        assert _needle_fraction(late) > 0.8

    def test_size_scales_with_target_tokens(self):
        small = build_haystack(1000, index=1)
        big = build_haystack(8000, index=1)
        assert len(big.statement) > 5 * len(small.statement)

    def test_varied_vocabulary(self):
        h = build_haystack(3000, index=1)
        assert len(set(h.statement.split())) > 25


class _PerfectRecall:
    """Stub provider that always returns the needle (depth/size irrelevant to it)."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages, max_tokens=256) -> Completion:
        self.calls += 1
        # echo a plausible code from the prompt so the grader matches
        import re
        m = re.search(r"the vault code is (k[0-9a-f]+)", messages[-1]["content"])
        return Completion(text=(m.group(1) if m else "none"), prompt_tokens=100, completion_tokens=5)


class TestRunNiah:
    def test_sweeps_size_by_depth_and_records_hits(self):
        stub = _PerfectRecall()
        rows = run_niah(stub, sizes=[1000, 2000], depths=[0.0, 0.5, 1.0])
        assert len(rows) == 6  # 2 sizes × 3 depths
        assert stub.calls == 6
        assert all(r.hit for r in rows)  # perfect-recall stub finds every needle
        assert {r.size for r in rows} == {1000, 2000}
        assert all(r.prompt_tokens == 100 for r in rows)
