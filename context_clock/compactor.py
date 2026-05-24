"""Compactor — the lossy summarization an agent runs when the window fills.

Folds a batch of older transcript messages into one short summary via a real
LLM call. It is intentionally *lossy*: the summary is capped, so some facts
get dropped — which is exactly the recall cost the benchmark measures.
"""

from __future__ import annotations

from .provider import OllamaProvider


def compact(provider: OllamaProvider, folded_messages: list[dict], max_tokens: int = 120) -> tuple[dict, int]:
    """Summarize ``folded_messages`` into one system message.

    Returns the summary message plus the token cost of producing it (the
    compaction overhead the benchmark charges against the session).
    """
    joined = "\n".join(m["content"] for m in folded_messages)
    prompt = [
        {
            "role": "user",
            "content": "Summarize the following memos as briefly as possible:\n\n" + joined,
        }
    ]
    result = provider.complete(prompt, max_tokens=max_tokens)
    summary = {
        "role": "system",
        "content": "[Compacted summary of earlier memos]: " + result.text,
    }
    return summary, result.prompt_tokens + result.completion_tokens
