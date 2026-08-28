"""Agent loop and timing policies — driven by a fake provider, no network.

The fake models the cache rules the real API implements, so the loop's
bookkeeping (requests per turn, gaps, blocks added, miss attribution) can be
tested exactly without spending money or waiting out a TTL.
"""

import pytest

from context_clock.cachecost.agent import MAX_ROUNDS_PER_TURN, run_session
from context_clock.cachecost.anthropic_provider import (
    CachedCompletion,
    RequestRecord,
    _mark_last_block,
)
from context_clock.cachecost.bench import (
    busy_policy,
    find_lookback_misses,
    sawtooth_policy,
    summarize,
    to_session_usage,
    write_records_csv,
)
from context_clock.cachecost.tasks import Task
from context_clock.cachecost.tools import Workspace


class FakeProvider:
    """Replays scripted responses and models the prefix cache."""

    def __init__(self, script, model="claude-sonnet-5"):
        self.model = model
        self.script = list(script)
        self.calls = []
        self._cached = 0

    def complete(self, messages, *, system=None, tools=None, max_tokens=4096, cache_ttl="5m"):
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        content = self.script.pop(0) if self.script else [{"type": "text", "text": "TASK COMPLETE"}]
        new_tokens = 100
        read, write = (self._cached, new_tokens) if self._cached else (0, 1_000)
        self._cached = read + write
        return CachedCompletion(
            content=tuple(content),
            stop_reason="tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn",
            model=self.model,
            cache_creation=write,
            cache_read=read,
            output_tokens=20,
            latency=0.01,
        )


def _tool_use(name, arguments, block_id="tu_1"):
    return {"type": "tool_use", "id": block_id, "name": name, "input": arguments}


def _text(text="done"):
    return [{"type": "text", "text": text}]


ONE_TURN = Task(name="t", brief="do the thing", followups=())


class TestAgentLoop:
    def test_a_turn_with_no_tool_call_is_one_request(self, tmp_path):
        provider = FakeProvider([_text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert run.n_requests == 1
        assert run.n_turns == 1

    def test_one_prompt_fans_out_into_many_requests(self, tmp_path):
        # The talk's "6 prompts turned into 31 API requests" mechanism.
        provider = FakeProvider(
            [
                [_tool_use("write_file", {"path": "a.py", "content": "x = 1"})],
                [_tool_use("list_files", {})],
                _text(),
            ]
        )
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert run.n_requests == 3
        assert run.n_turns == 1  # still a single user prompt

    def test_tool_results_are_fed_back(self, tmp_path):
        provider = FakeProvider(
            [[_tool_use("write_file", {"path": "a.py", "content": "hello"})], _text()]
        )
        workspace = Workspace(tmp_path)
        run_session(ONE_TURN, provider, workspace)
        assert workspace.read_file("a.py").text == "hello"  # the tool really ran
        followup = provider.calls[-1]["messages"]
        assert any(
            block.get("type") == "tool_result"
            for message in followup
            if isinstance(message.get("content"), list)
            for block in message["content"]
        )

    def test_records_blocks_added_per_request(self, tmp_path):
        provider = FakeProvider(
            [[_tool_use("list_files", {}), _tool_use("list_files", {}, "tu_2")], _text()]
        )
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert run.records[0].blocks_added == 4  # 2 tool_use + 2 tool_result

    def test_records_gap_between_requests(self, tmp_path):
        provider = FakeProvider([[_tool_use("list_files", {})], _text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert run.records[0].gap == 0.0     # nothing preceded it
        assert run.records[1].gap >= 0.0

    def test_malformed_tool_input_does_not_crash_the_loop(self, tmp_path):
        provider = FakeProvider(
            [[{"type": "tool_use", "id": "x", "name": "write_file", "input": "not-a-dict"}], _text()]
        )
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert run.n_requests == 2  # it recovered and carried on

    def test_runaway_tool_calling_is_capped(self, tmp_path):
        provider = FakeProvider([[_tool_use("list_files", {})]] * 200)
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert run.n_requests == MAX_ROUNDS_PER_TURN

    def test_multi_turn_task_records_every_turn(self, tmp_path):
        task = Task(name="t", brief="first", followups=("second", "third"))
        run = run_session(task, FakeProvider([]), Workspace(tmp_path))
        assert run.n_turns == 3
        assert [r.turn for r in run.records] == [0, 1, 2]

    def test_checks_tests_independently_of_what_the_model_says(self, tmp_path):
        # The model says TASK COMPLETE; the workspace has no passing suite.
        provider = FakeProvider([_text("TASK COMPLETE")])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert run.tests_passed is False


class TestCacheControlPlacement:
    def test_marks_the_final_content_block(self):
        marked = _mark_last_block([{"role": "user", "content": [{"type": "text", "text": "hi"}]}], "5m")
        assert marked[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_promotes_a_string_content_to_a_marked_block(self):
        marked = _mark_last_block([{"role": "user", "content": "hi"}], "5m")
        assert marked[0]["content"] == [
            {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}
        ]

    def test_one_hour_ttl_is_passed_through(self):
        marked = _mark_last_block([{"role": "user", "content": "hi"}], "1h")
        assert marked[0]["content"][-1]["cache_control"]["ttl"] == "1h"

    def test_does_not_mutate_the_callers_history(self):
        # The driver replays one history under several policies; a leaked
        # marker would silently change the second run's prefix.
        history = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        _mark_last_block(history, "5m")
        assert "cache_control" not in history[0]["content"][0]

    def test_unknown_ttl_is_rejected(self):
        with pytest.raises(ValueError, match="unknown cache TTL"):
            _mark_last_block([{"role": "user", "content": "hi"}], "30m")


class TestTimingPolicies:
    def test_busy_policy_never_sleeps(self):
        assert busy_policy()(3, 0) is None

    def test_sawtooth_sleeps_between_turns_only(self):
        slept = []
        policy = sawtooth_policy(420, sleep=slept.append)
        policy(0, 0)   # first turn: nothing to preserve yet
        policy(1, 0)   # new turn: idle past the TTL
        policy(1, 1)   # mid-turn round: generating, no wait
        assert slept == [420]

    def test_sawtooth_wall_clock_scales_with_turns_not_requests(self):
        slept = []
        policy = sawtooth_policy(420, sleep=slept.append)
        for turn in range(6):
            for round_index in range(5):
                policy(turn, round_index)
        assert len(slept) == 5  # 5 gaps for 6 turns, not 30 for 30 requests


class TestPricingAMeasuredRun:
    def test_converts_records_to_the_ledger_cost_model(self, tmp_path):
        provider = FakeProvider([[_tool_use("list_files", {})], _text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        session = to_session_usage(run, "claude-sonnet-5")
        assert session.n_requests == run.n_requests
        assert session.cost > 0

    def test_the_first_write_is_not_counted_as_a_miss(self, tmp_path):
        # Cold start is the cost of entry. Counting it would inflate every
        # miss statistic the benchmark reports.
        provider = FakeProvider([_text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        assert to_session_usage(run, "claude-sonnet-5").n_cache_misses == 0

    def test_a_later_write_with_no_read_is_a_miss(self):
        from context_clock.cachecost.agent import AgentRun

        run = AgentRun(task="t", model="claude-sonnet-5", policy="sawtooth")
        run.records = [
            RequestRecord(0, 0, 1_000, 0, 0, 10, 0.1, 0.0, 2, "end_turn"),
            RequestRecord(1, 1, 1_100, 0, 0, 10, 0.1, 420.0, 2, "end_turn"),
        ]
        assert to_session_usage(run, "claude-sonnet-5").n_cache_misses == 1

    def test_summary_names_the_task_and_the_bill(self, tmp_path):
        provider = FakeProvider([_text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        text = summarize(run, "claude-sonnet-5")
        # Cost leads; context is supporting detail, not the headline.
        assert text.index("COST") < text.index("CONTEXT")
        assert "billed" in text and "without caching" in text
        assert "cache writes" in text and "cache reads" in text
        assert "peak context" in text and "cumulative" in text

    def test_writes_a_csv_of_every_request(self, tmp_path):
        provider = FakeProvider([[_tool_use("list_files", {})], _text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        path = write_records_csv(run, tmp_path / "out" / "rows.csv")
        rows = path.read_text().splitlines()
        header = rows[0].split(",")
        assert header[:3] == ["index", "turn", "elapsed_s"]
        # Context is captured per datapoint, not only cost.
        for column in ("context_tokens", "cumulative_tokens", "n_blocks", "cost_usd"):
            assert column in header
        assert len(rows) == run.n_requests + 1


class TestLookbackDetection:
    def _run(self, records):
        from context_clock.cachecost.agent import AgentRun

        run = AgentRun(task="t", model="m", policy="busy")
        run.records = records
        return run

    def test_flags_a_miss_that_the_clock_cannot_explain(self):
        # 21 blocks appended, 2 seconds elapsed, cache read collapses: M1.
        run = self._run([
            RequestRecord(0, 0, 500, 5_000, 0, 10, 0.1, 0.0, 21, "tool_use"),
            RequestRecord(1, 0, 6_000, 0, 0, 10, 0.1, 2.0, 2, "end_turn"),
        ])
        assert [r.index for r in find_lookback_misses(run)] == [1]

    def test_ignores_a_miss_the_clock_explains(self):
        run = self._run([
            RequestRecord(0, 0, 500, 5_000, 0, 10, 0.1, 0.0, 21, "tool_use"),
            RequestRecord(1, 1, 6_000, 0, 0, 10, 0.1, 420.0, 2, "end_turn"),
        ])
        assert find_lookback_misses(run) == []

    def test_ignores_a_short_turn_that_hit(self):
        run = self._run([
            RequestRecord(0, 0, 500, 5_000, 0, 10, 0.1, 0.0, 4, "tool_use"),
            RequestRecord(1, 0, 100, 5_500, 0, 10, 0.1, 2.0, 2, "end_turn"),
        ])
        assert find_lookback_misses(run) == []


class TestContextCapture:
    """Every datapoint carries the context it was measured on, not just the cost."""

    def _run(self, tmp_path, **kwargs):
        provider = FakeProvider([[_tool_use("list_files", {})], _text()])
        return run_session(ONE_TURN, provider, Workspace(tmp_path), **kwargs)

    def test_records_the_live_context_size(self, tmp_path):
        run = self._run(tmp_path)
        # The fake caches 1000 then reads it back plus 100 new.
        assert run.records[0].context_tokens == 1_000
        assert run.records[1].context_tokens == 1_100

    def test_cumulative_tokens_grow_monotonically(self, tmp_path):
        run = self._run(tmp_path)
        totals = [r.cumulative_tokens for r in run.records]
        assert totals == sorted(totals)
        assert totals[-1] > totals[0]

    def test_cumulative_exceeds_peak_context(self, tmp_path):
        # The whole point: the bill sums every request, the meter shows one.
        run = self._run(tmp_path)
        assert run.cumulative_tokens > run.peak_context

    def test_records_conversation_shape(self, tmp_path):
        run = self._run(tmp_path)
        assert run.records[0].n_messages == 1        # just the user brief
        assert run.records[1].n_messages == 3        # + assistant + tool_result
        assert run.records[1].n_blocks >= 3
        assert run.records[1].history_chars > 0

    def test_records_elapsed_from_session_start(self, tmp_path):
        run = self._run(tmp_path)
        assert run.records[0].elapsed >= 0
        assert run.records[1].elapsed >= run.records[0].elapsed

    def test_unknown_cost_is_none_not_zero(self, tmp_path):
        # The fake reports no cost; conflating that with $0 would understate.
        run = self._run(tmp_path)
        assert all(r.cost is None for r in run.records)
        assert run.measured_cost is None

    def test_measured_cost_sums_when_every_request_reports_one(self, tmp_path):
        from context_clock.cachecost.agent import AgentRun

        run = AgentRun(task="t", model="m", policy="busy")
        run.records = [
            RequestRecord(0, 0, 100, 0, 0, 5, 0.1, 0.0, 2, "end_turn", cost=0.01),
            RequestRecord(1, 0, 10, 100, 0, 5, 0.1, 1.0, 2, "end_turn", cost=0.002),
        ]
        assert run.measured_cost == pytest.approx(0.012)

    def test_capture_dir_writes_the_exact_context_per_request(self, tmp_path):
        import json as _json

        capture = tmp_path / "ctx"
        run = self._run(tmp_path, capture_dir=capture)
        files = sorted(capture.glob("req-*.json"))
        assert len(files) == run.n_requests
        payload = _json.loads(files[1].read_text())
        assert payload["index"] == 1
        assert payload["context_tokens"] == run.records[1].context_tokens
        assert payload["messages"], "the conversation itself must be recoverable"

    def test_no_capture_dir_writes_nothing(self, tmp_path):
        self._run(tmp_path)
        assert not (tmp_path / "ctx").exists()


class TestCaptureIsolation:
    """A re-run must not splice its rows with a previous session's."""

    def test_stale_rows_from_a_previous_run_are_cleared(self, tmp_path):
        capture = tmp_path / "ctx"
        capture.mkdir()
        stale = capture / "req-0099.json"
        stale.write_text('{"index": 99, "cumulative_tokens": 999999}')

        provider = FakeProvider([_text()])
        run_session(ONE_TURN, provider, Workspace(tmp_path), capture_dir=capture)

        assert not stale.exists()
        assert sorted(p.name for p in capture.glob("req-*.json")) == ["req-0000.json"]

    def test_captured_indices_are_contiguous_from_zero(self, tmp_path):
        capture = tmp_path / "ctx"
        provider = FakeProvider([[_tool_use("list_files", {})], _text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path), capture_dir=capture)
        indices = sorted(int(p.stem.split("-")[1]) for p in capture.glob("req-*.json"))
        assert indices == list(range(run.n_requests))

    def test_cumulative_tokens_never_decrease(self, tmp_path):
        import json as _json

        capture = tmp_path / "ctx"
        provider = FakeProvider([[_tool_use("list_files", {})], _text()] * 3)
        run_session(ONE_TURN, provider, Workspace(tmp_path), capture_dir=capture)
        totals = [
            _json.loads(p.read_text())["cumulative_tokens"]
            for p in sorted(capture.glob("req-*.json"))
        ]
        assert totals == sorted(totals)


class TestWindowExhaustion:
    """Outgrowing the context window is a terminal state, not a crash."""

    class _ExhaustingProvider(FakeProvider):
        def __init__(self, n_ok):
            super().__init__([[_tool_use("list_files", {})]] * n_ok)
            self.n_ok = n_ok
            self.seen = 0

        def complete(self, *a, **kw):
            from context_clock.cachecost.anthropic_provider import ContextWindowExceeded

            if self.seen >= self.n_ok:
                raise ContextWindowExceeded("prompt is too long: 202214 > 200000")
            self.seen += 1
            return super().complete(*a, **kw)

    def test_run_ends_cleanly_and_is_flagged(self, tmp_path):
        run = run_session(ONE_TURN, self._ExhaustingProvider(3), Workspace(tmp_path))
        assert run.window_exhausted is True

    def test_measurements_before_the_limit_are_kept(self, tmp_path):
        run = run_session(ONE_TURN, self._ExhaustingProvider(3), Workspace(tmp_path))
        assert run.n_requests == 3
        assert all(r.context_tokens > 0 for r in run.records)

    def test_partial_run_still_prices_and_writes_a_csv(self, tmp_path):
        run = run_session(ONE_TURN, self._ExhaustingProvider(2), Workspace(tmp_path))
        assert to_session_usage(run, "claude-sonnet-5").n_requests == 2
        path = write_records_csv(run, tmp_path / "partial.csv")
        assert len(path.read_text().splitlines()) == 3

    def test_wall_clock_is_recorded(self, tmp_path):
        run = run_session(ONE_TURN, self._ExhaustingProvider(1), Workspace(tmp_path))
        assert run.wall_clock >= 0


class TestContextWindows:
    def test_haiku_has_the_smaller_window(self):
        from context_clock.cachecost.pricing import price_card

        assert price_card("claude-haiku-4-5").context_window == 200_000
        assert price_card("claude-sonnet-5").context_window == 1_000_000
