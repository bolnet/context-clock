"""OpenAI-compatible provider — OpenRouter (and any OpenAI-style endpoint).

Same Completion shape as OllamaProvider so the driver is provider-agnostic.
Tested with a mocked urlopen — no network, no API cost.
"""

import json
import urllib.request

from context_clock.openai_provider import OpenAICompatProvider


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_urlopen(monkeypatch, payload, captured):
    def fake(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        return _FakeResp(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake)


class TestOpenAICompatProvider:
    def test_parses_content_and_usage(self, monkeypatch):
        _mock_urlopen(monkeypatch, {
            "choices": [{"message": {"content": "k44cb6"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 4},
        }, {})
        p = OpenAICompatProvider(model="deepseek/deepseek-r1", api_key="sk-or-test")
        c = p.complete([{"role": "user", "content": "code?"}], max_tokens=16)
        assert c.text == "k44cb6"
        assert c.prompt_tokens == 120
        assert c.completion_tokens == 4

    def test_sends_bearer_auth_and_payload(self, monkeypatch):
        cap = {}
        _mock_urlopen(monkeypatch, {"choices": [{"message": {"content": "x"}}], "usage": {}}, cap)
        p = OpenAICompatProvider(model="m", api_key="sk-or-secret")
        p.complete([{"role": "user", "content": "hi"}], max_tokens=8)
        req = cap["req"]
        assert req.get_header("Authorization") == "Bearer sk-or-secret"
        assert req.full_url.endswith("/chat/completions")
        body = json.loads(req.data)
        assert body["model"] == "m"
        assert body["max_tokens"] == 8
        assert body["messages"][0]["content"] == "hi"

    def test_missing_usage_defaults_to_zero(self, monkeypatch):
        _mock_urlopen(monkeypatch, {"choices": [{"message": {"content": "ok"}}]}, {})
        p = OpenAICompatProvider(model="m", api_key="k")
        c = p.complete([{"role": "user", "content": "hi"}])
        assert c.prompt_tokens == 0
        assert c.completion_tokens == 0
        assert c.text == "ok"
