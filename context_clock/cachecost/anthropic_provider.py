"""Anthropic Messages API client — stdlib only, with the cache usage split.

Why not the SDK: the repo's providers are all `urllib`/`subprocess` (see
CLAUDE.md), and the one field this benchmark exists to read is a plain integer
in the JSON response. An SDK would add a dependency to fetch it.

The measurement is the four-way usage split the API returns per request:

    cache_creation_input_tokens  written to cache  (1.25x / 2x)
    cache_read_input_tokens      served from cache (0.1x)
    input_tokens                 neither           (1.0x)
    output_tokens                generated         (5x)

Nothing else in this module infers or reconstructs those numbers.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


def load_api_key(env_path: str | Path = ".env") -> str:
    """ANTHROPIC_API_KEY from env, else from a local gitignored .env. Never logged."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        env = Path(env_path)
        if env.exists():
            for line in env.read_text().splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY not found (env var or .env).\n"
            "The cache benchmark needs the Anthropic API directly: it is the only\n"
            "surface that reports cache writes and cache reads as separate counts,\n"
            "and that split is the measurement."
        )
    return key


@dataclass(frozen=True)
class CachedCompletion:
    """One API response, with the billing split intact."""

    content: tuple[dict, ...]
    stop_reason: str
    model: str
    cache_creation: int = 0
    cache_read: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: Wall-clock seconds this request took, request start to response.
    latency: float = 0.0
    #: Real billed USD, when the provider reports one (OpenRouter does).
    #: ``None`` means unknown, never $0 — the meter must not conflate them.
    cost: float | None = None
    #: False when the write count was inferred rather than reported.
    write_measured: bool = True

    @property
    def prompt_tokens(self) -> int:
        return self.cache_creation + self.cache_read + self.input_tokens

    @property
    def was_cache_miss(self) -> bool:
        """True when a prefix that should have been warm was rewritten instead.

        A request that reads nothing while writing a substantial prefix either
        started cold or lost its entry. The driver knows which, from timing.
        """
        return self.cache_read == 0 and self.cache_creation > 0

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class AnthropicProvider:
    """Minimal Messages API client with prompt caching and tool use."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = ANTHROPIC_BASE_URL,
        timeout: float = 900.0,
        retries: int = 5,
        backoff: float = 3.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for AnthropicProvider")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    def complete(
        self,
        messages: list[dict],
        *,
        system: list[dict] | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        cache_ttl: str = "5m",
    ) -> CachedCompletion:
        """One request. Caches the system prefix and the conversation tail.

        Two breakpoints, deliberately: one pinned to the end of the system
        prefix so the expensive shared part always has a read point, and one on
        the last content block so the growing conversation is cached too. That
        is the documented agent-loop layout, and it is what a real harness does.
        """
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": _mark_last_block(messages, cache_ttl),
        }
        if system:
            payload["system"] = _mark_last_block_of(system, cache_ttl)
        if tools:
            payload["tools"] = tools

        started = time.monotonic()
        body = self._post(json.dumps(payload).encode("utf-8"))
        latency = time.monotonic() - started

        usage = body.get("usage") or {}
        return CachedCompletion(
            content=tuple(body.get("content") or ()),
            stop_reason=body.get("stop_reason") or "",
            model=body.get("model") or self.model,
            cache_creation=usage.get("cache_creation_input_tokens") or 0,
            cache_read=usage.get("cache_read_input_tokens") or 0,
            input_tokens=usage.get("input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            latency=latency,
        )

    def warm(
        self,
        messages: list[dict],
        *,
        system: list[dict] | None = None,
        tools: list[dict] | None = None,
        cache_ttl: str = "5m",
    ) -> CachedCompletion:
        """A refresh read — the heartbeat.

        ``max_tokens: 0`` runs prefill and returns immediately with no content
        and no billed output, which re-reads the cached prefix and resets its
        timer. This is the cheapest possible keep-warm request.
        """
        return self.complete(
            messages, system=system, tools=tools, max_tokens=0, cache_ttl=cache_ttl
        )

    def _post(self, data: bytes) -> dict:
        """POST /messages, retrying only what is genuinely transient.

        429 and 5xx are retried with linear backoff; every other 4xx is a real
        client error and fails immediately rather than burning the budget.
        """
        request = urllib.request.Request(
            f"{self.base_url}/messages",
            data=data,
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": API_VERSION,
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
                    raise RuntimeError(f"Anthropic API {exc.code}: {detail}") from exc
                last = exc
            except urllib.error.URLError as exc:
                last = exc
            if attempt < self.retries - 1:
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"Anthropic API unreachable after {self.retries} attempts: {last}")


def _mark_last_block_of(blocks: list[dict], ttl: str) -> list[dict]:
    """Return a copy of ``blocks`` with cache_control on the last one."""
    if not blocks:
        return blocks
    marked = [dict(b) for b in blocks]
    marked[-1] = {**marked[-1], "cache_control": _cache_control(ttl)}
    return marked


def _mark_last_block(messages: list[dict], ttl: str) -> list[dict]:
    """Return a copy of ``messages`` with cache_control on the final content block.

    Never mutates the caller's history — the driver replays the same list under
    several timing policies, so an in-place marker would leak between runs.
    """
    if not messages:
        return messages
    copied = [dict(m) for m in messages]
    last = copied[-1]
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [
            {"type": "text", "text": content, "cache_control": _cache_control(ttl)}
        ]
    elif isinstance(content, list) and content:
        blocks = [dict(b) for b in content]
        blocks[-1] = {**blocks[-1], "cache_control": _cache_control(ttl)}
        last["content"] = blocks
    return copied


def _cache_control(ttl: str) -> dict:
    if ttl == "5m":
        return {"type": "ephemeral"}
    if ttl == "1h":
        return {"type": "ephemeral", "ttl": "1h"}
    raise ValueError(f"unknown cache TTL {ttl!r}; expected '5m' or '1h'")


@dataclass
class RequestRecord:
    """One row of the benchmark: what a single API request cost, and on how much context.

    Cost without context is half the picture. context-clock's existing result is
    that the prompt the model must read grows O(n^2) across a session; this row
    carries that growth (``context_tokens``, ``cumulative_tokens``) alongside
    what it billed, so a single run yields both curves and they can be plotted
    against each other.
    """

    index: int
    turn: int
    cache_creation: int
    cache_read: int
    input_tokens: int
    output_tokens: int
    latency: float
    #: Seconds between the previous request's start and this one's.
    gap: float
    blocks_added: int
    stop_reason: str
    tool_calls: tuple[str, ...] = field(default_factory=tuple)

    # --- context, captured per datapoint -------------------------------------
    #: Every token in the conversation the model had to read this request —
    #: the live context size, whatever bucket it billed in.
    context_tokens: int = 0
    #: Every token ever spent this session, prompt + completion. The O(n^2) curve.
    cumulative_tokens: int = 0
    #: Real billed USD for this request, when the provider reports one.
    cost: float | None = None
    #: Running billed USD across the session.
    cumulative_cost: float = 0.0
    #: Shape of the conversation behind this request.
    n_messages: int = 0
    n_blocks: int = 0
    history_chars: int = 0
    #: Seconds since the session started — the x-axis for the timing policies.
    elapsed: float = 0.0

    @property
    def prompt_tokens(self) -> int:
        return self.cache_creation + self.cache_read + self.input_tokens
