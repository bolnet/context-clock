"""OpenRouter adapter — shape translation and recovering writes from cost.

OpenRouter reports cache reads but has no field for cache writes, so writes are
recovered from the billed total. These tests pin that algebra exactly, because
a wrong write count would silently corrupt every cost figure the benchmark
publishes.
"""

import json

import pytest

from context_clock.cachecost.openrouter_provider import (
    OpenRouterCacheProvider,
    derive_writes,
    from_openai_response,
    parse_usage,
    to_openai_messages,
    to_openai_tools,
)
from context_clock.cachecost.pricing import price_card

SONNET = "claude-sonnet-5"


def _cost(model, *, read=0, write=0, uncached=0, output=0, ttl="5m"):
    """The bill the provider would return for a known split."""
    card = price_card(model)
    return (
        read * card.cache_read_per_mtok
        + write * card.cache_write_per_mtok(ttl)
        + uncached * card.input_per_mtok
        + output * card.output_per_mtok
    ) / 1e6


class TestDeriveWrites:
    def test_recovers_a_cold_start_write(self):
        split = derive_writes(
            prompt_tokens=10_000, cached_tokens=0, output_tokens=100,
            cost=_cost(SONNET, write=10_000, output=100), model=SONNET,
        )
        assert split.cache_write == 10_000
        assert split.cache_read == 0
        assert split.uncached_input == 0

    def test_recovers_a_warm_turn_split(self):
        split = derive_writes(
            prompt_tokens=10_500, cached_tokens=10_000, output_tokens=100,
            cost=_cost(SONNET, read=10_000, write=500, output=100), model=SONNET,
        )
        assert split.cache_read == 10_000
        assert split.cache_write == 500

    def test_recovers_a_wholly_uncached_request(self):
        split = derive_writes(
            prompt_tokens=5_000, cached_tokens=0, output_tokens=50,
            cost=_cost(SONNET, uncached=5_000, output=50), model=SONNET,
        )
        assert split.cache_write == 0
        assert split.uncached_input == 5_000

    @pytest.mark.parametrize("model", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"])
    def test_round_trips_on_every_priced_model(self, model):
        split = derive_writes(
            prompt_tokens=80_000, cached_tokens=60_000, output_tokens=400,
            cost=_cost(model, read=60_000, write=20_000, output=400), model=model,
        )
        assert split.cache_write == 20_000

    def test_one_hour_ttl_uses_the_one_hour_write_rate(self):
        split = derive_writes(
            prompt_tokens=10_000, cached_tokens=0, output_tokens=0,
            cost=_cost(SONNET, write=10_000, ttl="1h"), model=SONNET, ttl="1h",
        )
        assert split.cache_write == 10_000

    def test_without_a_cost_no_write_is_invented(self):
        # Guessing here would put a fabricated number in a measured column.
        split = derive_writes(
            prompt_tokens=10_000, cached_tokens=4_000, output_tokens=0,
            cost=None, model=SONNET,
        )
        assert split.cache_write == 0
        assert split.uncached_input == 6_000
        assert split.derived_write is False

    def test_writes_never_exceed_the_uncached_remainder(self):
        split = derive_writes(
            prompt_tokens=1_000, cached_tokens=900, output_tokens=0,
            cost=99.0, model=SONNET,  # absurd cost
        )
        assert split.cache_write <= 100

    def test_writes_are_never_negative(self):
        split = derive_writes(
            prompt_tokens=1_000, cached_tokens=0, output_tokens=0,
            cost=0.0, model=SONNET,
        )
        assert split.cache_write == 0


class TestParseUsage:
    def test_reads_an_openrouter_usage_block(self):
        body = {
            "usage": {
                "prompt_tokens": 10_500,
                "completion_tokens": 100,
                "cost": _cost(SONNET, read=10_000, write=500, output=100),
                "prompt_tokens_details": {"cached_tokens": 10_000},
            }
        }
        split = parse_usage(body, SONNET)
        assert (split.cache_read, split.cache_write, split.output) == (10_000, 500, 100)

    def test_missing_usage_is_all_zeros_not_a_crash(self):
        split = parse_usage({}, SONNET)
        assert (split.cache_read, split.cache_write, split.output) == (0, 0, 0)


class TestToolTranslation:
    def test_anthropic_tool_becomes_an_openai_function(self):
        tools = to_openai_tools(
            [{"name": "write_file", "description": "d", "input_schema": {"type": "object"}}]
        )
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "write_file"
        assert tools[0]["function"]["parameters"] == {"type": "object"}


class TestMessageTranslation:
    def test_user_text_survives(self):
        out = to_openai_messages([{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
        assert out[0]["role"] == "user"
        assert out[0]["content"][0]["text"] == "hi"

    def test_assistant_tool_use_becomes_tool_calls(self):
        out = to_openai_messages([
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a.py"}}
            ]}
        ])
        call = out[0]["tool_calls"][0]
        assert call["id"] == "t1"
        assert call["function"]["name"] == "read_file"
        assert json.loads(call["function"]["arguments"]) == {"path": "a.py"}

    def test_tool_results_become_tool_role_messages(self):
        out = to_openai_messages([
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "t2", "content": "also ok"},
            ]}
        ])
        assert [m["role"] for m in out] == ["tool", "tool"]
        assert [m["tool_call_id"] for m in out] == ["t1", "t2"]

    def test_cache_control_lands_on_the_final_markable_part(self):
        out = to_openai_messages([{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
        assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_marker_lands_on_a_trailing_tool_result(self):
        # This previously asserted the marker walking BACKWARD to the user turn,
        # which was the bug: the breakpoint froze and the growing tail billed at
        # the full input rate. It must land on the tail instead.
        out = to_openai_messages([
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "r"}]},
        ])
        assert out[-1]["role"] == "tool"
        assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in out[0]["content"][-1]

    def test_one_hour_ttl_is_passed_through(self):
        out = to_openai_messages(
            [{"role": "user", "content": [{"type": "text", "text": "hi"}]}], "1h"
        )
        assert out[-1]["content"][-1]["cache_control"]["ttl"] == "1h"

    def test_ordering_is_preserved(self):
        out = to_openai_messages([
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": "x", "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "r"}]},
        ])
        assert [m["role"] for m in out] == ["user", "assistant", "tool"]


class TestResponseTranslation:
    def test_plain_text_becomes_a_text_block(self):
        blocks, stop = from_openai_response(
            {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]}
        )
        assert blocks == ({"type": "text", "text": "hello"},)
        assert stop == "end_turn"

    def test_tool_calls_become_tool_use_blocks(self):
        blocks, stop = from_openai_response({
            "choices": [{
                "message": {"content": None, "tool_calls": [{
                    "id": "t1", "type": "function",
                    "function": {"name": "run_tests", "arguments": "{}"},
                }]},
                "finish_reason": "tool_calls",
            }]
        })
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["name"] == "run_tests"
        assert stop == "tool_use"

    def test_malformed_tool_arguments_are_passed_through_not_raised(self):
        # The agent loop turns this into a tool error the model can correct.
        blocks, _ = from_openai_response({
            "choices": [{"message": {"tool_calls": [{
                "id": "t", "function": {"name": "x", "arguments": "{not json"}}]}}]
        })
        assert blocks[0]["input"] == "{not json"

    def test_empty_response_does_not_crash(self):
        blocks, stop = from_openai_response({})
        assert blocks == ()


class TestProviderConstruction:
    def test_rejects_a_model_with_no_slug(self):
        with pytest.raises(KeyError, match="no OpenRouter slug"):
            OpenRouterCacheProvider(model="gpt-5.4", api_key="sk-or-test")

    def test_rejects_an_empty_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            OpenRouterCacheProvider(model=SONNET, api_key="")

    def test_maps_to_the_namespaced_slug(self):
        p = OpenRouterCacheProvider(model=SONNET, api_key="sk-or-test")
        assert p.slug == "anthropic/claude-sonnet-5"


class TestReportedWriteCount:
    """OpenRouter reports cache_write_tokens directly — verified live 2026-08-28."""

    def _body(self, *, prompt, cached, write, output, cost):
        return {"usage": {
            "prompt_tokens": prompt, "completion_tokens": output, "cost": cost,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": write},
        }}

    def test_prefers_the_reported_write_over_the_derivation(self):
        body = self._body(prompt=15_210, cached=0, write=15_204, output=4,
                          cost=_cost("claude-haiku-4-5", write=15_204, uncached=6, output=4))
        split = parse_usage(body, "claude-haiku-4-5")
        assert split.cache_write == 15_204
        assert split.derived_write is False  # measured, not inferred

    def test_uncached_is_the_remainder_of_prompt_tokens(self):
        # prompt_tokens is the sum of all three input buckets.
        body = self._body(prompt=15_210, cached=15_204, write=0, output=4, cost=0.0015464)
        assert parse_usage(body, "claude-haiku-4-5").uncached_input == 6

    def test_falls_back_to_derivation_when_the_field_is_absent(self):
        body = {"usage": {
            "prompt_tokens": 10_000, "completion_tokens": 0,
            "cost": _cost(SONNET, write=10_000),
            "prompt_tokens_details": {"cached_tokens": 0},
        }}
        split = parse_usage(body, SONNET)
        assert split.cache_write == 10_000
        assert split.derived_write is True

    def test_the_two_paths_agree_on_the_live_cold_response(self):
        from context_clock.cachecost.openrouter_provider import writes_disagree_by

        body = self._body(prompt=15_210, cached=0, write=15_204, output=4, cost=0.019031)
        assert writes_disagree_by(body, "claude-haiku-4-5") == 0

    def test_the_two_paths_agree_on_the_live_warm_response(self):
        from context_clock.cachecost.openrouter_provider import writes_disagree_by

        body = self._body(prompt=15_210, cached=15_204, write=0, output=4, cost=0.0015464)
        assert writes_disagree_by(body, "claude-haiku-4-5") == 0

    def test_disagreement_is_surfaced_not_averaged(self):
        from context_clock.cachecost.openrouter_provider import writes_disagree_by

        body = self._body(prompt=10_000, cached=0, write=10_000, output=0,
                          cost=_cost(SONNET, write=4_000, uncached=6_000))
        assert writes_disagree_by(body, SONNET) != 0


class TestBreakpointAdvances:
    """Regression: the cache breakpoint must land on the conversation's tail.

    Measured on a live run before the fix — the marker walked backward past
    string-bodied tool messages to the user turn, froze there, and left the
    growing tail billing at the full input rate on every request.
    """

    def _turn(self, n_results):
        return [
            {"role": "user", "content": [{"type": "text", "text": "brief"}]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "run_tests", "input": {}}
                for i in range(n_results)]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": "out"}
                for i in range(n_results)]},
        ]

    def test_marker_lands_on_the_final_tool_result(self):
        out = to_openai_messages(self._turn(1))
        assert out[-1]["role"] == "tool"
        assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_marker_does_not_walk_back_to_the_user_turn(self):
        out = to_openai_messages(self._turn(2))
        assert "cache_control" not in out[0]["content"][-1]

    def test_exactly_one_breakpoint_is_placed(self):
        out = to_openai_messages(self._turn(3))
        marked = sum(
            1 for m in out if isinstance(m.get("content"), list)
            for part in m["content"] if "cache_control" in part
        )
        assert marked == 1

    def test_tool_content_is_a_markable_list_not_a_bare_string(self):
        out = to_openai_messages(self._turn(1))
        assert isinstance(out[-1]["content"], list)
        assert out[-1]["content"][0]["type"] == "text"

    def test_tool_call_id_still_pairs_the_result_to_its_call(self):
        out = to_openai_messages(self._turn(2))
        assert [m["tool_call_id"] for m in out if m["role"] == "tool"] == ["t0", "t1"]

    def test_breakpoint_advances_as_the_conversation_grows(self):
        short = to_openai_messages(self._turn(1))
        longer = to_openai_messages(self._turn(1) + [
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        ])
        assert short[-1]["role"] == "tool"
        assert longer[-1]["role"] == "assistant"  # marker moved with the tail
        assert "cache_control" in longer[-1]["content"][-1]


class TestTransportRetries:
    """The bounded-retry path must cover every transient transport fault.

    A long agentic run makes hundreds of requests over hours; a single
    unhandled socket fault ends the session and forfeits the run. Both arms of
    the snake TTL experiment died this way on `TimeoutError`, which is an
    OSError but *not* a `urllib.error.URLError`, so it escaped the retry loop.
    """

    def _provider(self, monkeypatch, failures, *, exc):
        """A provider whose transport raises ``exc`` for the first N calls."""
        calls = {"n": 0}

        def fake_urlopen(request, timeout=None):
            calls["n"] += 1
            if calls["n"] <= failures:
                raise exc
            raise AssertionError("should not reach a real response in this test")

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        monkeypatch.setattr("time.sleep", lambda _s: None)
        provider = OpenRouterCacheProvider(
            model="claude-sonnet-5", api_key="k", retries=3, backoff=0.0
        )
        return provider, calls

    def test_a_read_timeout_is_retried_not_fatal(self, monkeypatch):
        """The exact fault that killed both snake runs."""
        provider, calls = self._provider(
            monkeypatch, failures=99, exc=TimeoutError("The read operation timed out")
        )
        with pytest.raises(RuntimeError, match="unreachable after 3 attempts"):
            provider._post(b"{}")
        assert calls["n"] == 3, "timeout must be retried, not raised on the first try"

    def test_a_dropped_connection_is_retried(self, monkeypatch):
        provider, calls = self._provider(
            monkeypatch, failures=99, exc=ConnectionResetError("peer reset")
        )
        with pytest.raises(RuntimeError, match="unreachable"):
            provider._post(b"{}")
        assert calls["n"] == 3

    def test_the_final_failure_names_the_underlying_cause(self, monkeypatch):
        """A run that dies after its retries must say what actually broke."""
        provider, _ = self._provider(
            monkeypatch, failures=99, exc=TimeoutError("The read operation timed out")
        )
        with pytest.raises(RuntimeError, match="timed out"):
            provider._post(b"{}")
