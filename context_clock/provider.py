"""Ollama provider — native /api/chat for exact num_ctx + real token counts.

The OpenAI-compat layer doesn't reliably honor ``num_ctx`` (our imposed
context limit), and the native endpoint returns the real prompt/completion
token counts (``prompt_eval_count`` / ``eval_count``). Stdlib only — no deps.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int


class OllamaProvider:
    def __init__(
        self,
        model: str,
        num_ctx: int,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.num_ctx = num_ctx
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout

    def complete(self, messages: list[dict], max_tokens: int = 256) -> Completion:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature,
                "num_predict": max_tokens,
            },
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.load(response)

        return Completion(
            text=body.get("message", {}).get("content", ""),
            prompt_tokens=body.get("prompt_eval_count", 0),
            completion_tokens=body.get("eval_count", 0),
        )
