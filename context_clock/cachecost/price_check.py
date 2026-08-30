"""Cross-check the price card against an independent published source.

The claim ledger's verdicts all rest on one price card. A card transcribed
from a single source is a single point of failure — and this project's rule is
that a published figure must trace to evidence, not to a number someone typed.

OpenRouter publishes per-model rates for all four billing buckets, including
``input_cache_read`` and ``input_cache_write``, on a public endpoint that needs
no key. Agreement between two independently maintained sources is meaningfully
stronger evidence than either alone.

Network-gated: nothing here runs in the default test suite.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from .pricing import PRICES, PriceCard

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

#: Our model ids are Anthropic's; OpenRouter namespaces and dots them.
OPENROUTER_SLUGS = {
    "claude-opus-5": "anthropic/claude-opus-5",
    "claude-sonnet-5": "anthropic/claude-sonnet-5",
    "claude-haiku-4-5": "anthropic/claude-haiku-4.5",
}

_PER_MTOK = 1_000_000


@dataclass(frozen=True)
class RateComparison:
    """One rate, as we hold it and as the external source publishes it."""

    model: str
    field: str
    ours: float
    theirs: float

    @property
    def agrees(self) -> bool:
        return abs(self.ours - self.theirs) < 1e-9


def fetch_openrouter_pricing(timeout: float = 60.0) -> dict[str, dict[str, float]]:
    """Published USD-per-million rates, keyed by our model id. Public, no key."""
    request = urllib.request.Request(OPENROUTER_MODELS_URL)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.load(response)

    by_slug = {m["id"]: m for m in body.get("data", [])}
    out: dict[str, dict[str, float]] = {}
    for model, slug in OPENROUTER_SLUGS.items():
        entry = by_slug.get(slug)
        if not entry:
            continue
        pricing = entry.get("pricing") or {}
        out[model] = {
            key: float(pricing[source]) * _PER_MTOK
            for key, source in (
                ("input_per_mtok", "prompt"),
                ("output_per_mtok", "completion"),
                ("cache_read_per_mtok", "input_cache_read"),
                ("cache_write_5m_per_mtok", "input_cache_write"),
                ("cache_write_1h_per_mtok", "input_cache_write_1h"),
            )
            if pricing.get(source) is not None
        }
    return out


def compare(external: dict[str, dict[str, float]]) -> list[RateComparison]:
    """Every rate we hold, paired with the external source's figure."""
    comparisons = []
    for model, card in PRICES.items():
        theirs = external.get(model)
        if not theirs:
            continue
        for field, ours in _card_rates(card).items():
            if field in theirs:
                comparisons.append(RateComparison(model, field, ours, theirs[field]))
    return comparisons


def _card_rates(card: PriceCard) -> dict[str, float]:
    return {
        "input_per_mtok": card.input_per_mtok,
        "output_per_mtok": card.output_per_mtok,
        "cache_read_per_mtok": card.cache_read_per_mtok,
        "cache_write_5m_per_mtok": card.cache_write_5m_per_mtok,
        "cache_write_1h_per_mtok": card.cache_write_1h_per_mtok,
    }


def main() -> int:
    """Fetch, compare, print. Non-zero exit on any disagreement."""
    comparisons = compare(fetch_openrouter_pricing())
    if not comparisons:
        print("No overlapping models found — cannot cross-check.")
        return 1

    print(f"Cross-check: context-clock price card vs OpenRouter ({OPENROUTER_MODELS_URL})\n")
    print(f"  {'model':<18} {'rate':<26} {'ours':>8} {'theirs':>8}   ")
    disagreements = 0
    for c in comparisons:
        mark = "ok" if c.agrees else "DIFFERS"
        if not c.agrees:
            disagreements += 1
        print(f"  {c.model:<18} {c.field:<26} {c.ours:>8.2f} {c.theirs:>8.2f}   {mark}")

    print(
        f"\n{len(comparisons)} rates compared across {len({c.model for c in comparisons})} "
        f"models · {disagreements} disagreements"
    )
    if not disagreements:
        print(
            "\nEvery rate agrees. Claims C1-C5 are confirmed by two independently\n"
            "maintained sources, not one transcription."
        )
    return 1 if disagreements else 0


if __name__ == "__main__":
    raise SystemExit(main())
