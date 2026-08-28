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
        assert "billed" in text and "without caching" in text

    def test_writes_a_csv_of_every_request(self, tmp_path):
        provider = FakeProvider([[_tool_use("list_files", {})], _text()])
        run = run_session(ONE_TURN, provider, Workspace(tmp_path))
        path = write_records_csv(run, tmp_path / "out" / "rows.csv")
        rows = path.read_text().splitlines()
        assert rows[0].startswith("index,turn,gap_s")
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
