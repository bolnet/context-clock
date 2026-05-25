"""OpenAI-compatible provider — OpenRouter (and any OpenAI-style endpoint).

Same Completion shape as OllamaProvider so the driver is provider-agnostic.
Tested with a mocked urlopen — no network, no API cost.
"""

import json
import urllib.error
import urllib.request

import pytest

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

    def test_requests_usage_accounting(self, monkeypatch):
        # OpenRouter only returns the billed cost when the request opts in via
        # ``usage: {include: true}`` — otherwise we'd have no real-money figure.
        cap = {}
        _mock_urlopen(monkeypatch, {"choices": [{"message": {"content": "x"}}], "usage": {}}, cap)
        p = OpenAICompatProvider(model="m", api_key="k")
        p.complete([{"role": "user", "content": "hi"}])
        body = json.loads(cap["req"].data)
        assert body["usage"] == {"include": True}

    def test_parses_billed_cost_from_usage(self, monkeypatch):
        # The real dollar amount OpenRouter charged for the call.
        _mock_urlopen(monkeypatch, {
            "choices": [{"message": {"content": "k44cb6"}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 4, "cost": 0.00013125},
        }, {})
        p = OpenAICompatProvider(model="openai/gpt-5.4-mini", api_key="sk-or-test")
        c = p.complete([{"role": "user", "content": "code?"}], max_tokens=16)
        assert c.cost == 0.00013125

    def test_missing_cost_is_none_not_zero(self, monkeypatch):
        # Absent cost must be None (unknown/unbilled), distinct from a real $0.
        _mock_urlopen(monkeypatch, {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }, {})
        p = OpenAICompatProvider(model="m", api_key="k")
        c = p.complete([{"role": "user", "content": "hi"}])
        assert c.cost is None


class TestOpenAICompatRetry:
    """OpenRouter rate-limits (429) and transient 5xx must be retried with
    backoff — a long benchmark run must not crash on a single throttle — while
    real client errors (other 4xx) fail fast.
    """

    def _seq_urlopen(self, monkeypatch, outcomes):
        calls = {"n": 0}

        def fake(req, timeout=None):  # noqa: ANN001
            i = calls["n"]
            calls["n"] += 1
            outcome = outcomes[min(i, len(outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(urllib.request, "urlopen", fake)
        return calls

    def test_retries_on_429_then_succeeds(self, monkeypatch):
        ok = _FakeResp({
            "choices": [{"message": {"content": "k1"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "cost": 0.0001},
        })
        err = urllib.error.HTTPError("http://x", 429, "rate limited", {}, None)
        calls = self._seq_urlopen(monkeypatch, [err, err, ok])
        p = OpenAICompatProvider(model="m", api_key="k", backoff=0.0)
        c = p.complete([{"role": "user", "content": "hi"}])
        assert c.text == "k1"
        assert calls["n"] == 3  # two 429s, then success

    def test_retries_on_500_then_succeeds(self, monkeypatch):
        ok = _FakeResp({"choices": [{"message": {"content": "ok"}}], "usage": {}})
        err = urllib.error.HTTPError("http://x", 503, "unavailable", {}, None)
        calls = self._seq_urlopen(monkeypatch, [err, ok])
        p = OpenAICompatProvider(model="m", api_key="k", backoff=0.0)
        c = p.complete([{"role": "user", "content": "hi"}])
        assert c.text == "ok"
        assert calls["n"] == 2

    def test_client_error_4xx_raises_immediately(self, monkeypatch):
        err = urllib.error.HTTPError("http://x", 400, "bad request", {}, None)
        calls = self._seq_urlopen(monkeypatch, [err])
        p = OpenAICompatProvider(model="m", api_key="k", backoff=0.0)
        with pytest.raises(urllib.error.HTTPError):
            p.complete([{"role": "user", "content": "hi"}])
        assert calls["n"] == 1  # no retry on a real client error
