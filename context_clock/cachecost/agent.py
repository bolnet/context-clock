"""The agent loop — real tool use, instrumented per API request.

One user prompt becomes as many API requests as the model needs tool calls, so
this is where the talk's "6 prompts turned into 31 API requests" actually comes
from. Every request is recorded with its cache split, its gap from the previous
request, and how many content blocks the turn appended — the three quantities
that decide whether the next request hits or misses.

Timing is injected as a policy rather than hard-coded, so the identical session
can be replayed busy, idle, or bridged with heartbeats and the only difference
in the bill is *when* the requests happened.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..meter import TokenMeter
from .anthropic_provider import AnthropicProvider, ContextWindowExceeded, RequestRecord
from .tasks import SYSTEM_PROMPT, Task
from .tools import TOOL_SCHEMAS, Workspace, dispatch

#: Hard ceiling on tool-call rounds per user turn. A model that loops forever
#: would spend the budget without producing a measurement.
MAX_ROUNDS_PER_TURN = 25


@dataclass
class AgentRun:
    """Everything one session produced: the rows, the workspace, the outcome."""

    task: str
    model: str
    policy: str
    records: list[RequestRecord] = field(default_factory=list)
    completed: bool = False
    tests_passed: bool = False
    wall_clock: float = 0.0
    #: Set when the conversation outgrew the model's window mid-session. The
    #: rows already collected remain valid measurements.
    window_exhausted: bool = False

    @property
    def n_requests(self) -> int:
        return len(self.records)

    @property
    def n_turns(self) -> int:
        return len({r.turn for r in self.records})

    @property
    def peak_context(self) -> int:
        """Largest single prompt the model had to read — the context-meter number."""
        return max((r.context_tokens for r in self.records), default=0)

    @property
    def cumulative_tokens(self) -> int:
        """Every token ever spent this session. The curve context-clock measures."""
        return self.records[-1].cumulative_tokens if self.records else 0

    @property
    def measured_cost(self) -> float | None:
        """Real billed USD, when every request reported one. None if any did not."""
        if not self.records or any(r.cost is None for r in self.records):
            return None
        return sum(r.cost or 0.0 for r in self.records)


#: A timing policy is called before each request with (turn, round) and may
#: sleep, warm the cache, or do nothing. It returns nothing; its only effect is
#: on the clock, which is exactly the variable under test.
TimingPolicy = Callable[[int, int], None]


def _no_delay(turn: int, round_index: int) -> None:
    return None


def run_session(
    task: Task,
    provider: AnthropicProvider,
    workspace: Workspace,
    *,
    policy: TimingPolicy = _no_delay,
    policy_name: str = "busy",
    max_tokens: int = 8192,
    cache_ttl: str = "5m",
    on_record: Callable[[RequestRecord], None] | None = None,
    capture_dir: str | Path | None = None,
    turns: int | None = None,
) -> AgentRun:
    """Drive ``task`` to completion, recording the cache split of every request."""
    run = AgentRun(task=task.name, model=provider.model, policy=policy_name)
    system = [{"type": "text", "text": SYSTEM_PROMPT}]
    messages: list[dict] = []
    prompts = task.prompts(turns)

    session_start = time.monotonic()
    last_request_start: float | None = None
    request_index = 0
    meter = TokenMeter()
    capture = Path(capture_dir) if capture_dir else None
    if capture:
        capture.mkdir(parents=True, exist_ok=True)
        # Clear any previous run's rows. A shorter re-run would otherwise leave
        # stale high-index files behind that read as real data — silently
        # splicing two sessions into one apparently-continuous curve.
        for old_row in capture.glob("req-*.json"):
            old_row.unlink()

    for turn, prompt in enumerate(prompts):
        messages.append({"role": "user", "content": [{"type": "text", "text": prompt}]})

        for round_index in range(MAX_ROUNDS_PER_TURN):
            policy(turn, round_index)

            request_start = time.monotonic()
            gap = 0.0 if last_request_start is None else request_start - last_request_start
            last_request_start = request_start

            try:
                completion = provider.complete(
                    messages,
                    system=system,
                    tools=TOOL_SCHEMAS,
                    max_tokens=max_tokens,
                    cache_ttl=cache_ttl,
                )
            except ContextWindowExceeded:
                # The session outgrew the window. Stop cleanly and keep every
                # measurement taken up to this point.
                run.window_exhausted = True
                run.wall_clock = time.monotonic() - session_start
                run.tests_passed = workspace.tests_pass()
                return run

            tool_uses = completion.tool_uses()
            # The assistant's blocks plus one tool_result each are what the next
            # request must walk back over to find the cache entry.
            blocks_added = len(completion.content) + len(tool_uses)

            # The context as it stood for THIS request — measured from the
            # conversation actually sent, not reconstructed afterwards.
            shape = _context_shape(messages)
            meter.record(
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.output_tokens,
                cost=completion.cost,
            )

            record = RequestRecord(
                index=request_index,
                turn=turn,
                cache_creation=completion.cache_creation,
                cache_read=completion.cache_read,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                latency=completion.latency,
                gap=gap,
                blocks_added=blocks_added,
                stop_reason=completion.stop_reason,
                tool_calls=tuple(t.get("name", "?") for t in tool_uses),
                context_tokens=completion.prompt_tokens,
                cumulative_tokens=meter.cumulative_total,
                cost=completion.cost,
                cumulative_cost=meter.cumulative_cost,
                n_messages=shape["messages"],
                n_blocks=shape["blocks"],
                history_chars=shape["chars"],
                elapsed=request_start - session_start,
            )
            if capture:
                _capture_context(capture, request_index, messages, record)
            run.records.append(record)
            if on_record:
                on_record(record)
            request_index += 1

            messages.append({"role": "assistant", "content": list(completion.content)})

            if not tool_uses:
                break  # the model answered instead of calling a tool: turn over

            messages.append(
                {"role": "user", "content": [_result_block(workspace, t) for t in tool_uses]}
            )
        else:
            # Ran out of rounds. Recorded, not raised: a truncated turn is still
            # a valid measurement of the requests that did happen.
            run.completed = False

    run.wall_clock = time.monotonic() - session_start
    run.tests_passed = workspace.tests_pass()
    run.completed = True
    return run


def _result_block(workspace: Workspace, tool_use: dict) -> dict:
    """Execute one tool call and shape it as a tool_result block."""
    arguments = tool_use.get("input")
    if not isinstance(arguments, dict):
        # Tool inputs are model-generated JSON; a non-object is a malformed call.
        return {
            "type": "tool_result",
            "tool_use_id": tool_use.get("id", ""),
            "content": f"Malformed tool input: {json.dumps(arguments)[:200]}",
            "is_error": True,
        }
    result = dispatch(workspace, tool_use.get("name", ""), arguments)
    return {
        "type": "tool_result",
        "tool_use_id": tool_use.get("id", ""),
        "content": result.text,
        "is_error": result.is_error,
    }


def _context_shape(messages: list[dict]) -> dict[str, int]:
    """Measure the conversation being sent: messages, content blocks, characters.

    Token counts come from the provider; these are the structural dimensions it
    does not report — and ``blocks`` is what the 20-block cache lookback walks.
    """
    blocks = 0
    chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            blocks += 1
            chars += len(content)
        elif isinstance(content, list):
            blocks += len(content)
            chars += sum(len(json.dumps(b, default=str)) for b in content)
    return {"messages": len(messages), "blocks": blocks, "chars": chars}


def _capture_context(
    directory: Path, index: int, messages: list[dict], record: RequestRecord
) -> None:
    """Persist the exact conversation behind one datapoint.

    Opt-in, because it grows with the session. When enabled, every published
    figure can be traced back to the precise prefix that produced it — which is
    the only way a cache measurement is auditable after the fact.
    """
    payload = {
        "index": index,
        "turn": record.turn,
        "context_tokens": record.context_tokens,
        "cumulative_tokens": record.cumulative_tokens,
        "cost": record.cost,
        "n_messages": record.n_messages,
        "n_blocks": record.n_blocks,
        "messages": messages,
    }
    (directory / f"req-{index:04d}.json").write_text(json.dumps(payload, indent=2, default=str))
