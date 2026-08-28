"""OpenRouter provider — Anthropic prompt caching through the OpenAI-compat shape.

OpenRouter passes Anthropic's ``cache_control`` through and returns
``usage.cost``: **real billed dollars**, not a figure reconstructed from a price
card. For a cost benchmark that is the stronger measurement — the bill itself
rather than our arithmetic about the bill.

It costs one thing. The OpenAI-compat usage block reports cache *reads*
(``prompt_tokens_details.cached_tokens``) but has no field for cache *writes*.
Writes are recovered algebraically from the billed cost — see ``derive_writes``.

Two shapes have to be reconciled, so this module owns the translation and the
agent loop stays written against Anthropic content blocks:

    Anthropic                      OpenAI-compat
    assistant [tool_use]      <->  assistant .tool_calls[]
    user [tool_result]        <->  role="tool" messages
    tools[]                   <->  tools[{type:"function"}]
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .anthropic_provider import CachedCompletion, ContextWindowExceeded, _cache_control
from .pricing import price_card

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

#: Our model ids are Anthropic's; OpenRouter namespaces and dots them.
SLUGS = {
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
}


def to_openai_tools(tools: list[dict]) -> list[dict]:
    """Anthropic tool schemas -> OpenAI function schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def to_openai_messages(messages: list[dict], cache_ttl: str = "5m") -> list[dict]:
    """Anthropic message blocks -> OpenAI messages, with the tail cache-marked.

    Tool results become their own ``role="tool"`` messages, because the
    OpenAI shape carries one result per message rather than several in one
    user turn. The ordering is preserved, so the cached prefix still grows
    monotonically and the benchmark measures the same conversation.
    """
    out: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        blocks = content or []
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        texts = [b for b in blocks if b.get("type") == "text"]

        if tool_results:
            # A user turn made only of tool results fans out into one tool
            # message per result. Content is a list, not a bare string, so the
            # cache breakpoint can land here: in an agentic loop most turns end
            # on a tool result, and a marker that cannot attach to one leaves
            # the breakpoint frozen at the start of the turn while the tail
            # grows — every appended token then bills at full price, forever.
            for block in tool_results:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": [
                            {"type": "text", "text": str(block.get("content", ""))}
                        ],
                    }
                )
            continue

        if role == "assistant" and tool_uses:
            out.append(
                {
                    "role": "assistant",
                    "content": "".join(t.get("text", "") for t in texts) or None,
                    "tool_calls": [
                        {
                            "id": b.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": b.get("name", ""),
                                "arguments": json.dumps(b.get("input", {})),
                            },
                        }
                        for b in tool_uses
                    ],
                }
            )
            continue

        out.append(
            {
                "role": role,
                "content": [{"type": "text", "text": b.get("text", "")} for b in texts],
            }
        )

    return _mark_tail(out, cache_ttl)


def _mark_tail(messages: list[dict], ttl: str) -> list[dict]:
    """Put cache_control on the last markable content part.

    The marker must land on the **final** message of the conversation, so the
    cached prefix advances every request. If it walks backward instead, the
    breakpoint freezes and everything appended after it is billed uncached at
    the full input rate — measured on a live run before this was fixed: cache
    reads pinned at 77,354 tokens while the context grew past 133,000, with
    ~56,000 tokens paying full price on every request.
    """
    for message in reversed(messages):
        content = message.get("content")
        if isinstance(content, list) and content:
            marked = [dict(p) for p in content]
            marked[-1] = {**marked[-1], "cache_control": _cache_control(ttl)}
            message["content"] = marked
            return messages
    return messages


def from_openai_response(body: dict) -> tuple[tuple[dict, ...], str]:
    """OpenAI choice -> Anthropic content blocks + stop reason."""
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks: list[dict] = []

    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            # Model-generated JSON can be malformed; hand it to the loop as-is
            # so it becomes a tool error the model can correct, not a crash.
            arguments = raw
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", ""),
                "name": function.get("name", ""),
                "input": arguments,
            }
        )

    finish = choice.get("finish_reason") or ""
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else (
        "end_turn" if finish == "stop" else finish
    )
    return tuple(blocks), stop


@dataclass(frozen=True)
class UsageSplit:
    """The four billing buckets, recovered from an OpenAI-compat usage block."""

    cache_read: int
    cache_write: int
    uncached_input: int
    output: int
    cost: float | None
    derived_write: bool


def derive_writes(
    *,
    prompt_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    cost: float | None,
    model: str,
    ttl: str = "5m",
) -> UsageSplit:
    """Recover the cache-write count from the billed cost.

    The OpenAI-compat usage block reports reads but not writes. With the billed
    total known, writes fall out of two equations:

        W + U = prompt_tokens - cached_tokens
        cost  = C*r_read + W*r_write + U*r_in + O*r_out

    so ``W = (cost - C*r_read - O*r_out - (P-C)*r_in) / (r_write - r_in)``.

    Without a cost the split is unrecoverable, and everything uncached is
    reported as plain input rather than guessed at — an inferred write would be
    a fabricated number in a measured column.
    """
    card = price_card(model)
    remainder = max(prompt_tokens - cached_tokens, 0)

    if cost is None:
        return UsageSplit(cached_tokens, 0, remainder, output_tokens, None, False)

    read_cost = cached_tokens * card.cache_read_per_mtok / 1e6
    output_cost = output_tokens * card.output_per_mtok / 1e6
    input_cost = cost - read_cost - output_cost

    write_rate = card.cache_write_per_mtok(ttl)
    spread = (write_rate - card.input_per_mtok) / 1e6
    if spread <= 0:
        return UsageSplit(cached_tokens, 0, remainder, output_tokens, cost, False)

    writes = (input_cost - remainder * card.input_per_mtok / 1e6) / spread
    writes = int(round(min(max(writes, 0), remainder)))
    return UsageSplit(
        cache_read=cached_tokens,
        cache_write=writes,
        uncached_input=remainder - writes,
        output=output_tokens,
        cost=cost,
        derived_write=True,
    )


def parse_usage(body: dict, model: str, ttl: str = "5m") -> UsageSplit:
    """Read an OpenRouter usage block into the four buckets.

    Measured against the live endpoint (2026-08-28): OpenRouter reports
    ``cache_write_tokens`` alongside ``cached_tokens``, so the write count is
    read directly rather than inferred. ``prompt_tokens`` is the sum of all
    three input buckets.

    ``derive_writes`` remains the fallback for a response that omits the field,
    and doubles as a cross-check — see ``writes_disagree_by``.
    """
    usage = body.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    prompt_tokens = usage.get("prompt_tokens") or 0
    cached = details.get("cached_tokens") or 0
    output = usage.get("completion_tokens") or 0
    cost = usage.get("cost")

    reported_write = details.get("cache_write_tokens")
    if reported_write is None:
        return derive_writes(
            prompt_tokens=prompt_tokens, cached_tokens=cached,
            output_tokens=output, cost=cost, model=model, ttl=ttl,
        )

    write = int(reported_write)
    return UsageSplit(
        cache_read=cached,
        cache_write=write,
        uncached_input=max(prompt_tokens - cached - write, 0),
        output=output,
        cost=cost,
        derived_write=False,
    )


def writes_disagree_by(body: dict, model: str, ttl: str = "5m") -> int | None:
    """Gap between the reported write count and the one implied by the bill.

    Two independent paths to the same number: the provider's own field, and
    the algebra over the billed total. A non-zero gap means one of them is
    wrong, and a benchmark that publishes cost figures should notice rather
    than average them. ``None`` when either path is unavailable.
    """
    usage = body.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    reported = details.get("cache_write_tokens")
    if reported is None or usage.get("cost") is None:
        return None
    derived = derive_writes(
        prompt_tokens=usage.get("prompt_tokens") or 0,
        cached_tokens=details.get("cached_tokens") or 0,
        output_tokens=usage.get("completion_tokens") or 0,
        cost=usage.get("cost"), model=model, ttl=ttl,
    )
    return int(reported) - derived.cache_write


class OpenRouterCacheProvider:
    """Cache-aware client for Anthropic models routed through OpenRouter.

    Returns the same ``CachedCompletion`` the native provider does, so the
    agent loop and the pricing bridge are unchanged. The one difference is
    recorded honestly: ``cache_creation`` here is derived from the billed cost
    rather than reported directly, and ``last_usage`` keeps the raw block so a
    run can always be audited against what the provider actually said.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = OPENROUTER_URL,
        timeout: float = 900.0,
        retries: int = 5,
        backoff: float = 3.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for OpenRouterCacheProvider")
        if model not in SLUGS:
            known = ", ".join(sorted(SLUGS))
            raise KeyError(f"no OpenRouter slug for {model!r}; known: {known}")
        self.model = model
        self.slug = SLUGS[model]
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.last_usage: dict | None = None

    def complete(
        self,
        messages: list[dict],
        *,
        system: list[dict] | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        cache_ttl: str = "5m",
    ) -> CachedCompletion:
        import time

        payload: dict = {
            "model": self.slug,
            "max_tokens": max_tokens,
            "messages": self._messages(messages, system, cache_ttl),
            # Real billed dollars — the field the write count is recovered from.
            "usage": {"include": True},
        }
        if tools:
            payload["tools"] = to_openai_tools(tools)

        started = time.monotonic()
        body = self._post(json.dumps(payload).encode("utf-8"))
        latency = time.monotonic() - started

        self.last_usage = body.get("usage")
        content, stop = from_openai_response(body)
        split = parse_usage(body, self.model, cache_ttl)

        return CachedCompletion(
            content=content,
            stop_reason=stop,
            model=self.model,
            cache_creation=split.cache_write,
            cache_read=split.cache_read,
            input_tokens=split.uncached_input,
            output_tokens=split.output,
            latency=latency,
            cost=split.cost,
            write_measured=not split.derived_write,
        )

    def warm(self, messages, *, system=None, tools=None, cache_ttl="5m") -> CachedCompletion:
        """A refresh read. OpenRouter rejects max_tokens 0, so ask for one token."""
        return self.complete(
            messages, system=system, tools=tools, max_tokens=1, cache_ttl=cache_ttl
        )

    def _messages(self, messages, system, cache_ttl) -> list[dict]:
        converted = to_openai_messages(messages, cache_ttl)
        if system:
            preamble = {
                "role": "system",
                "content": [
                    {"type": "text", "text": b.get("text", ""),
                     "cache_control": _cache_control(cache_ttl)}
                    for b in system
                ],
            }
            return [preamble, *converted]
        return converted

    def _post(self, data: bytes) -> dict:
        import time
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            self.base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    detail = exc.read().decode("utf-8", "replace")[:400]
                    if "prompt is too long" in detail or "context" in detail.lower():
                        raise ContextWindowExceeded(detail) from exc
                    raise RuntimeError(f"OpenRouter {exc.code}: {detail}") from exc
                last = exc
            except urllib.error.URLError as exc:
                last = exc
            if attempt < self.retries - 1:
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"OpenRouter unreachable after {self.retries} attempts: {last}")
