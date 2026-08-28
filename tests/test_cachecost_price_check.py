"""Price-card cross-check against OpenRouter's published rates.

The offline tests pin the comparison logic. The live fetch is network-gated the
same way the Attestor tests are, so the default suite stays clean offline.
"""

import os

import pytest

from context_clock.cachecost.price_check import (
    OPENROUTER_SLUGS,
    RateComparison,
    compare,
    fetch_openrouter_pricing,
)
from context_clock.cachecost.pricing import PRICES

LIVE = os.environ.get("CONTEXT_CLOCK_LIVE_PRICING") == "1"


class TestComparison:
    def test_agreeing_rates_are_marked_as_such(self):
        assert RateComparison("m", "input_per_mtok", 2.0, 2.0).agrees is True

    def test_disagreeing_rates_are_flagged(self):
        assert RateComparison("m", "input_per_mtok", 2.0, 3.0).agrees is False

    def test_compares_every_rate_we_hold(self):
        external = {"claude-sonnet-5": {
            "input_per_mtok": 2.0, "output_per_mtok": 10.0,
            "cache_read_per_mtok": 0.2, "cache_write_5m_per_mtok": 2.5,
            "cache_write_1h_per_mtok": 4.0,
        }}
        result = compare(external)
        assert len(result) == 5
        assert all(c.agrees for c in result)

    def test_detects_a_drifted_rate(self):
        external = {"claude-sonnet-5": {"input_per_mtok": 2.50}}
        assert compare(external)[0].agrees is False

    def test_models_absent_upstream_are_skipped_not_failed(self):
        assert compare({}) == []

    def test_partial_upstream_data_compares_what_exists(self):
        result = compare({"claude-opus-5": {"cache_read_per_mtok": 0.50}})
        assert len(result) == 1

    def test_every_priced_model_has_a_slug(self):
        assert set(PRICES) == set(OPENROUTER_SLUGS)


@pytest.mark.skipif(not LIVE, reason="set CONTEXT_CLOCK_LIVE_PRICING=1 to hit the network")
class TestLivePricing:
    def test_published_rates_agree_with_our_card(self):
        comparisons = compare(fetch_openrouter_pricing())
        assert comparisons, "no overlapping models returned"
        disagreements = [c for c in comparisons if not c.agrees]
        assert not disagreements, f"price card drifted: {disagreements}"
