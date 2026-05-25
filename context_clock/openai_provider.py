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
import time
import urllib.error
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
        *,
        retries: int = 8,
        backoff: float = 3.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required for OpenAICompatProvider")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.num_ctx = num_ctx  # interface parity only; ignored
        self.retries = retries
        self.backoff = backoff

    def complete(self, messages: list[dict], max_tokens: int = 256) -> Completion:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            # Opt into usage accounting so OpenRouter returns the real billed
            # cost (``usage.cost``) — the report's $ figures must be measured,
            # not recomputed from list price.
            "usage": {"include": True},
        }
        data = json.dumps(payload).encode("utf-8")
        body = self._post_with_retry(data)

        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}
        # ``cost`` is absent unless the endpoint reports it → None (unknown),
        # which the meter treats as $0 without conflating it with a real $0 bill.
        return Completion(
            text=message.get("content") or "",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cost=usage.get("cost"),
        )

    def _post_with_retry(self, data: bytes) -> dict:
        """POST to /chat/completions, retrying transient failures.

        OpenRouter throttles (HTTP 429) and occasionally 5xx, especially under
        concurrent load. A long benchmark run must absorb these rather than
        crash, so 429/5xx are retried with linear backoff; other 4xx (real
        client errors) raise immediately. The last exception propagates if all
        attempts are exhausted — nothing is swallowed.
        """
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - trusted endpoint
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise  # real client error — not transient
                last_exc = exc
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                last_exc = exc
            if attempt < self.retries - 1:
                time.sleep(self.backoff * (attempt + 1))
        assert last_exc is not None
        raise last_exc
