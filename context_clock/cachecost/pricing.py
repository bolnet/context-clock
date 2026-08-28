"""Published price cards — the only place list prices are written down.

Rates are USD per million tokens, from Anthropic's published pricing. The
cache multipliers are structural, not per-model:

* cache **read**  = 0.10x the base input rate
* cache **write** = 1.25x the base input rate for a 5-minute TTL
* cache **write** = 2.00x the base input rate for a 1-hour TTL

Everything downstream derives from these numbers, so a price change is a
one-line edit here and every claim verdict re-derives from it.
"""

from __future__ import annotations

from dataclasses import dataclass

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_WRITE_1H_MULTIPLIER = 2.00

TTL_5M_SECONDS = 300
TTL_1H_SECONDS = 3600

#: A breakpoint walks back at most this many content blocks looking for a
#: prior cache entry. A turn appending more than this silently misses.
LOOKBACK_BLOCKS = 20

#: Max ``cache_control`` breakpoints the API accepts on one request.
MAX_BREAKPOINTS = 4


@dataclass(frozen=True)
class PriceCard:
    """Per-million-token USD rates for one model."""

    model: str
    input_per_mtok: float
    output_per_mtok: float

    @property
    def cache_read_per_mtok(self) -> float:
        return self.input_per_mtok * CACHE_READ_MULTIPLIER

    @property
    def cache_write_5m_per_mtok(self) -> float:
        return self.input_per_mtok * CACHE_WRITE_5M_MULTIPLIER

    @property
    def cache_write_1h_per_mtok(self) -> float:
        return self.input_per_mtok * CACHE_WRITE_1H_MULTIPLIER

    def cache_write_per_mtok(self, ttl: str = "5m") -> float:
        if ttl == "5m":
            return self.cache_write_5m_per_mtok
        if ttl == "1h":
            return self.cache_write_1h_per_mtok
        raise ValueError(f"unknown cache TTL {ttl!r}; expected '5m' or '1h'")


PRICES: dict[str, PriceCard] = {
    "claude-opus-5": PriceCard("claude-opus-5", 5.00, 25.00),
    "claude-sonnet-5": PriceCard("claude-sonnet-5", 2.00, 10.00),
    "claude-haiku-4-5": PriceCard("claude-haiku-4-5", 1.00, 5.00),
}


def price_card(model: str) -> PriceCard:
    """Look up a price card, failing loudly on an unpriced model.

    Guessing a price would put a fabricated number into a published figure,
    so an unknown model is an error rather than a default.
    """
    try:
        return PRICES[model]
    except KeyError:
        known = ", ".join(sorted(PRICES))
        raise KeyError(f"no published price card for {model!r}; known: {known}") from None


def reads_per_write(ttl: str = "5m") -> float:
    """How many cache reads cost the same as one cache write of the same size.

    This is the break-even for any keep-warm ("heartbeat") strategy: bridging
    an idle gap with refresh reads is cheaper than eating the miss only while
    fewer than this many reads are needed.
    """
    write = CACHE_WRITE_5M_MULTIPLIER if ttl == "5m" else CACHE_WRITE_1H_MULTIPLIER
    return write / CACHE_READ_MULTIPLIER
