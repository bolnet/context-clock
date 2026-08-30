"""Price card — the published rates every downstream figure derives from.

If these drift, every claim verdict is wrong, so they are pinned explicitly.
"""

import pytest

from context_clock.cachecost.pricing import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_1H_MULTIPLIER,
    CACHE_WRITE_5M_MULTIPLIER,
    LOOKBACK_BLOCKS,
    MAX_BREAKPOINTS,
    price_card,
    reads_per_write,
)


class TestPriceCard:
    @pytest.mark.parametrize(
        "model,input_rate,output_rate",
        [
            ("claude-opus-5", 5.00, 25.00),
            ("claude-sonnet-5", 2.00, 10.00),
            ("claude-haiku-4-5", 1.00, 5.00),
        ],
    )
    def test_published_rates(self, model, input_rate, output_rate):
        card = price_card(model)
        assert card.input_per_mtok == input_rate
        assert card.output_per_mtok == output_rate

    @pytest.mark.parametrize(
        "model", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
    )
    def test_output_is_exactly_five_times_input(self, model):
        # The talk said "about 5x"; it is exactly 5x on every current model.
        card = price_card(model)
        assert card.output_per_mtok == 5 * card.input_per_mtok

    def test_cache_read_is_a_tenth_of_input(self):
        assert price_card("claude-sonnet-5").cache_read_per_mtok == 0.20

    def test_opus_five_minute_write_is_six_twenty_five(self):
        assert price_card("claude-opus-5").cache_write_5m_per_mtok == 6.25

    def test_one_hour_write_is_double_input(self):
        assert price_card("claude-sonnet-5").cache_write_1h_per_mtok == 4.00

    def test_write_rate_selected_by_ttl(self):
        card = price_card("claude-opus-5")
        assert card.cache_write_per_mtok("5m") == 6.25
        assert card.cache_write_per_mtok("1h") == 10.00

    def test_unknown_ttl_rejected(self):
        with pytest.raises(ValueError, match="unknown cache TTL"):
            price_card("claude-opus-5").cache_write_per_mtok("30m")

    def test_unpriced_model_raises_rather_than_guessing(self):
        # Defaulting would put a fabricated price into a published figure.
        with pytest.raises(KeyError, match="no published price card"):
            price_card("gpt-5.4")

    def test_card_is_immutable(self):
        with pytest.raises(Exception):
            price_card("claude-opus-5").input_per_mtok = 99.0


class TestMultipliers:
    def test_documented_multipliers(self):
        assert CACHE_READ_MULTIPLIER == 0.10
        assert CACHE_WRITE_5M_MULTIPLIER == 1.25
        assert CACHE_WRITE_1H_MULTIPLIER == 2.00

    def test_break_even_is_twelve_and_a_half_reads(self):
        # The meeting estimated "10ish reads for every cache write".
        assert reads_per_write("5m") == 12.5

    def test_one_hour_ttl_break_even_is_twenty_reads(self):
        assert reads_per_write("1h") == 20.0

    def test_documented_structural_limits(self):
        assert MAX_BREAKPOINTS == 4
        assert LOOKBACK_BLOCKS == 20
