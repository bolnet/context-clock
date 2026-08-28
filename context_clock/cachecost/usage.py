"""Per-request and per-session cost arithmetic over real token counts.

The four token buckets a cached request bills in are disjoint, and each has
its own rate. Conflating them is exactly the mistake that makes a 277k-token
conversation look like it should cost 55 cents when it actually costs $2:

* ``cache_read``   — prefix served from cache      (0.1x input)
* ``cache_write``  — prefix/tail written to cache  (1.25x input, 5m TTL)
* ``uncached_input`` — everything the cache didn't cover (1.0x input)
* ``output``       — generated tokens              (5x input, all three models)

Every object here is frozen; ``SessionUsage`` returns new instances rather
than accumulating in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .pricing import PriceCard, price_card

_PER_MTOK = 1_000_000


@dataclass(frozen=True)
class RequestUsage:
    """One API request's billable token counts, as the provider reports them."""

    cache_read: int = 0
    cache_write: int = 0
    uncached_input: int = 0
    output: int = 0
    ttl: str = "5m"
    #: True when this request had to rewrite a prefix that was already cached
    #: — the expiry cliff. Bookkeeping only; cost comes from the token counts.
    was_cache_miss: bool = False

    def __post_init__(self) -> None:
        for name in ("cache_read", "cache_write", "uncached_input", "output"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if self.ttl not in ("5m", "1h"):
            raise ValueError(f"unknown cache TTL {self.ttl!r}; expected '5m' or '1h'")

    @property
    def prompt_tokens(self) -> int:
        """Total input tokens the model read this request, however billed."""
        return self.cache_read + self.cache_write + self.uncached_input

    def cost(self, model: str | PriceCard) -> float:
        """Billed USD for this request under the given price card."""
        card = model if isinstance(model, PriceCard) else price_card(model)
        return (
            self.cache_read * card.cache_read_per_mtok
            + self.cache_write * card.cache_write_per_mtok(self.ttl)
            + self.uncached_input * card.input_per_mtok
            + self.output * card.output_per_mtok
        ) / _PER_MTOK

    def uncached_cost(self, model: str | PriceCard) -> float:
        """What this request would have cost with no prompt caching at all.

        The counterfactual: every input token billed at the full input rate,
        because a stateless API re-reads the whole conversation each turn.
        """
        card = model if isinstance(model, PriceCard) else price_card(model)
        return (
            self.prompt_tokens * card.input_per_mtok + self.output * card.output_per_mtok
        ) / _PER_MTOK


@dataclass(frozen=True)
class SessionUsage:
    """An ordered, immutable run of requests — one agent session."""

    model: str
    requests: tuple[RequestUsage, ...] = field(default_factory=tuple)

    def with_request(self, request: RequestUsage) -> "SessionUsage":
        """Return a new session with ``request`` appended. Never mutates."""
        return replace(self, requests=self.requests + (request,))

    @property
    def card(self) -> PriceCard:
        return price_card(self.model)

    @property
    def n_requests(self) -> int:
        return len(self.requests)

    @property
    def n_cache_misses(self) -> int:
        return sum(1 for r in self.requests if r.was_cache_miss)

    @property
    def cache_read_tokens(self) -> int:
        return sum(r.cache_read for r in self.requests)

    @property
    def cache_write_tokens(self) -> int:
        return sum(r.cache_write for r in self.requests)

    @property
    def uncached_input_tokens(self) -> int:
        return sum(r.uncached_input for r in self.requests)

    @property
    def output_tokens(self) -> int:
        return sum(r.output for r in self.requests)

    @property
    def prompt_tokens(self) -> int:
        return sum(r.prompt_tokens for r in self.requests)

    @property
    def final_context_tokens(self) -> int:
        """Size of the last prompt — what the context meter shows the user.

        The number people reason about, and the one that misleads: it is the
        size of *one* request, not the sum of everything billed.
        """
        return self.requests[-1].prompt_tokens if self.requests else 0

    @property
    def cost(self) -> float:
        """Total billed USD across the session."""
        card = self.card
        return sum(r.cost(card) for r in self.requests)

    @property
    def uncached_cost(self) -> float:
        """Counterfactual USD with prompt caching switched off entirely."""
        card = self.card
        return sum(r.uncached_cost(card) for r in self.requests)

    @property
    def naive_cost(self) -> float:
        """The intuitive-but-wrong estimate: final context x the input rate.

        This is what someone computes when they read '277k tokens' off the
        context meter and multiply by the advertised price.
        """
        return self.final_context_tokens * self.card.input_per_mtok / _PER_MTOK

    @property
    def cache_savings_multiple(self) -> float:
        """How many times more the session would cost with caching off."""
        return self.uncached_cost / self.cost if self.cost else 0.0

    @property
    def naive_underestimate_multiple(self) -> float:
        """How many times the naive context-meter estimate understates the bill."""
        return self.cost / self.naive_cost if self.naive_cost else 0.0

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of input tokens served from cache rather than (re)written."""
        total = self.prompt_tokens
        return self.cache_read_tokens / total if total else 0.0


def miss_penalty(context_tokens: int, model: str, ttl: str = "5m") -> float:
    """USD cost of expiring a cached prefix of ``context_tokens`` and rewriting it.

    The cliff: a hit bills the prefix at 0.1x, a miss rewrites it at 1.25x.
    """
    card = price_card(model)
    return context_tokens * card.cache_write_per_mtok(ttl) / _PER_MTOK


def hit_cost(context_tokens: int, model: str) -> float:
    """USD cost of reading a cached prefix of ``context_tokens`` — the happy path."""
    card = price_card(model)
    return context_tokens * card.cache_read_per_mtok / _PER_MTOK
