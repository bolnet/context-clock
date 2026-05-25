"""Claude via the Claude Code CLI (`claude -p`) — subscription auth, NO API key.

Shells out to ``claude -p <prompt> --model <model> --output-format json`` and
parses the result, returning the same ``Completion`` shape as the other providers.
Lets the benchmark run Claude models (e.g. haiku) on a Claude Code subscription
with no Anthropic API key.

CAVEAT: each call runs inside the full Claude Code agent harness (a large system
preamble + tool definitions, ~tens of K cached tokens) and frames the model as a
coding agent — so this is "Claude-as-agent," not a bare model endpoint. The rot
mechanism still holds (our needle isn't in that preamble), but results carry the
harness overhead and aren't 1:1 with the clean Ollama / OpenRouter runs.
"""

from __future__ import annotations

import json
import subprocess
import time

from .provider import Completion


class ClaudeCliProvider:
    def __init__(self, model: str = "haiku", timeout: float = 180.0, cli: str = "claude",
                 num_ctx: int | None = None, retries: int = 3, backoff: float = 3.0) -> None:
        self.model = model
        self.timeout = timeout
        self.cli = cli
        self.num_ctx = num_ctx  # interface parity; ignored
        self.retries = retries
        self.backoff = backoff  # seconds, multiplied by attempt number

    def complete(self, messages: list[dict], max_tokens: int = 256) -> Completion:
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        body = "\n\n".join(
            f'{m["role"]}: {m["content"]}' for m in messages if m.get("role") != "system"
        )
        cmd = [self.cli, "-p", body, "--model", self.model, "--output-format", "json"]
        if system:
            cmd += ["--append-system-prompt", system]

        # Retry transient failures (rate limits during a burst return exit 1 / empty output).
        last = ""
        for attempt in range(self.retries):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            if proc.returncode == 0 and proc.stdout.strip():
                break
            last = (proc.stderr or proc.stdout or "").strip()
            time.sleep(self.backoff * (attempt + 1))
        else:
            raise RuntimeError(f"claude CLI failed after {self.retries} tries: {last[:300]}")
        data = json.loads(proc.stdout)
        usage = data.get("usage") or {}
        return Completion(
            text=data.get("result") or "",
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
        )
