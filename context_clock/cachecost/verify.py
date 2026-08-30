"""CLI — re-derive every claim verdict from the price card and print the ledger.

    python -m context_clock.cachecost.verify
    python -m context_clock.cachecost.verify --scenarios

Nothing here is cached or copied from a previous run: every number is computed
at print time from ``pricing``, so a price change shows up as a changed verdict
rather than a stale table.
"""

from __future__ import annotations

import argparse

from .claims import CLAIMS, CONSISTENT, Claim, annual_miss_overpay
from .pricing import PRICES, reads_per_write
from .scenarios import continuous, heartbeat_bridge, sawtooth
from .usage import hit_cost, miss_penalty

_ORDER = ("CONFIRMED", CONSISTENT, "REFINED", "ORG_SPECIFIC", "UNVERIFIABLE", "MISSING")


def format_claim(claim: Claim) -> str:
    lines = [f"[{claim.verdict:12s}] {claim.id}  {claim.topic}"]
    lines.append(f'    "{claim.quote}"')
    result = claim.evaluate()
    if result is not None:
        derived, stated = result
        mark = "ok" if claim.holds() else "MISMATCH"
        lines.append(f"    derived {derived:,.4f}  vs stated {stated:,.4f}   [{mark}]")
    lines.append(f"    {claim.evidence}")
    return "\n".join(lines)


def print_ledger() -> int:
    """Print every claim grouped by verdict. Returns the count of failed checks."""
    print("=" * 78)
    print("cachecost — prompt-cache claim ledger")
    print("=" * 78)
    print("\nPrice card (USD per million tokens)")
    print(f"  {'model':<20} {'input':>8} {'output':>8} {'cache rd':>9} "
          f"{'wr 5m':>8} {'wr 1h':>8}")
    for card in PRICES.values():
        print(
            f"  {card.model:<20} {card.input_per_mtok:>8.2f} {card.output_per_mtok:>8.2f} "
            f"{card.cache_read_per_mtok:>9.2f} {card.cache_write_5m_per_mtok:>8.2f} "
            f"{card.cache_write_1h_per_mtok:>8.2f}"
        )

    failures = 0
    for verdict in _ORDER:
        group = [c for c in CLAIMS if c.verdict == verdict]
        if not group:
            continue
        print(f"\n--- {verdict}  ({len(group)}) " + "-" * (54 - len(verdict)))
        for claim in group:
            print(format_claim(claim))
            if not claim.holds():
                failures += 1
            print()

    checked = sum(1 for c in CLAIMS if c.check)
    print("=" * 78)
    print(f"{len(CLAIMS)} claims · {checked} numerically checked · {failures} mismatched")
    return failures


def print_scenarios() -> None:
    """Derived comparisons — labelled as derived, never as measured."""
    print("\n" + "=" * 78)
    print("Derived scenarios (simulator, not measured — see CACHE_CLAIMS.md)")
    print("=" * 78)

    for model in ("claude-sonnet-5", "claude-opus-5"):
        busy = continuous(31, model)
        saw = sawtooth(31, model)
        print(f"\n{model} · 31 requests, 38k preamble, 2.4k new tokens/turn")
        print(
            f"  continuous (no misses)  ${busy.cost:7.4f}   "
            f"final context {busy.final_context_tokens:>7,}   "
            f"hit rate {busy.cache_hit_rate:6.1%}"
        )
        print(
            f"  sawtooth  (all missed)  ${saw.cost:7.4f}   "
            f"misses {saw.n_cache_misses:>2}/{saw.n_requests}"
            f"                 hit rate {saw.cache_hit_rate:6.1%}"
        )
        print(f"  penalty for idling past the TTL every turn: {saw.cost / busy.cost:.1f}x")
        print(
            f"  caching off entirely would be ${busy.uncached_cost:.4f} "
            f"({busy.cache_savings_multiple:.1f}x the cached bill)"
        )

    print("\nHeartbeat break-even (claude-sonnet-5, 275k context, 4-minute beats)")
    print(f"  one miss costs        ${miss_penalty(275_000, 'claude-sonnet-5'):.4f}")
    print(f"  one refresh read      ${hit_cost(275_000, 'claude-sonnet-5'):.4f}")
    print(f"  break-even            {reads_per_write('5m'):.1f} reads "
          f"= {reads_per_write('5m') * 4:.0f} minutes of keep-warm")
    for gap_minutes in (10, 30, 50, 70):
        bridge = heartbeat_bridge(275_000, gap_minutes * 60, "claude-sonnet-5")
        miss = miss_penalty(275_000, "claude-sonnet-5")
        verdict = "heartbeat" if bridge.cost < miss else "take the miss"
        print(
            f"  {gap_minutes:>3}-minute gap: heartbeat ${bridge.cost:.4f} "
            f"({bridge.n_requests} beats) vs miss ${miss:.4f}  ->  {verdict}"
        )

    print("\nOrg projection model (C24) — substitute your own telemetry")
    for engineers in (100, 500, 1000):
        total = annual_miss_overpay(
            engineers=engineers,
            misses_per_engineer_per_day=10,
            avg_context_tokens=275_000,
            model="claude-sonnet-5",
        )
        print(f"  {engineers:>5} engineers x 10 misses/day x 275k ctx -> ${total:,.0f}/yr")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m context_clock.cachecost.verify",
        description="Re-derive the prompt-cache claim ledger from the price card.",
    )
    parser.add_argument(
        "--scenarios",
        action="store_true",
        help="also print derived scenario comparisons",
    )
    args = parser.parse_args(argv)

    failures = print_ledger()
    if args.scenarios:
        print_scenarios()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
