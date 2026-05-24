"""Ollama provider — real integration (local, free, fast).

We test real behavior against a running Ollama, not a mock: the whole point
is that token counts are *real*. Skips cleanly if Ollama isn't up.
"""

import urllib.request

import pytest

from context_clock.provider import OllamaProvider, Completion


def _ollama_up() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/version", timeout=3)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _ollama_up(), reason="Ollama not running")


def test_complete_returns_text_and_real_token_counts():
    provider = OllamaProvider(model="llama3.2", num_ctx=2048)
    result = provider.complete(
        [{"role": "user", "content": "Reply with exactly one word: hello"}],
        max_tokens=10,
    )
    assert isinstance(result, Completion)
    assert result.text.strip() != ""
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0
