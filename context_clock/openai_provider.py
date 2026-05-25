"""OpenAI-compatible chat provider — OpenRouter and any OpenAI-style endpoint.

Returns the same ``Completion`` shape as ``OllamaProvider``, so the driver is
provider-agnostic. Stdlib only (urllib) — no SDK dependency. The API key is
passed in by the caller (read from the environment, never hard-coded).

``num_ctx`` is accepted for interface parity with ``OllamaProvider`` but ignored:
hosted API models fix their own context window, so the truncation-at-N experiment
doesn't apply — use the native-window workload instead.
"""

from __future__ import annotations

import json
import urllib.request

from .provider import Completion

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenAICompatProvider:
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = OPENROUTER_BASE_URL,
        temperature: float = 0.0,
        timeout: float = 120.0,
        num_ctx: int | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for OpenAICompatProvider")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.num_ctx = num_ctx  # interface parity only; ignored

    def complete(self, messages: list[dict], max_tokens: int = 256) -> Completion:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.load(response)

        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}
        return Completion(
            text=message.get("content") or "",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
