"""Cost-vs-context scaling analysis, validated on synthetic runs with known answers.

The regression is both the C30 result and a self-check on the harness, so its
own arithmetic is pinned against sessions whose true slope is known by
construction.
"""

import pytest

from context_clock.cachecost.agent import AgentRun
from context_clock.cachecost.analysis import (
    cost_per_context,
    cumulative_by_turn,
    implied_read_rate,
    least_squares,
    quadratic_beats_linear,
    read_rate_agreement,
    summarize_scaling,
    warm_requests,
)
from context_clock.cachecost.anthropic_provider import RequestRecord
from context_clock.cachecost.pricing import price_card

SONNET = "claude-sonnet-5"


def _healthy_run(n=20, model=SONNET, tail=2_000, output=500):
    """A session billed exactly as a warm cache should bill it."""
    card = price_card(model)
    run = AgentRun(task="t", model=model, policy="busy")
    ctx = 10_000
    cum = 0.0
    for i in range(n):
        read = ctx
        cost = (
            read * card.cache_read_per_mtok
            + tail * card.cache_write_5m_per_mtok
            + output * card.output_per_mtok
        ) / 1e6
        cum += cost
        run.records.append(RequestRecord(
            index=i, turn=i // 4, cache_creation=tail, cache_read=read,
            input_tokens=0, output_tokens=output, latency=1.0, gap=5.0,
            blocks_added=3, stop_reason="tool_use",
            context_tokens=read + tail, cost=cost, cumulative_cost=cum,
        ))
        ctx += tail
    return run


class TestLeastSquares:
    def test_recovers_a_known_line(self):
        fit = least_squares([1, 2, 3, 4], [3, 5, 7, 9])  # y = 2x + 1
        assert fit.slope == pytest.approx(2.0)
        assert fit.intercept == pytest.approx(1.0)
        assert fit.r_squared == pytest.approx(1.0)

    def test_reports_a_poor_fit_honestly(self):
        fit = least_squares([1, 2, 3, 4], [5, 1, 6, 2])
        assert fit.r_squared < 0.3

    def test_rejects_too_few_points(self):
        with pytest.raises(ValueError, match="at least 2 points"):
            least_squares([1], [1])

    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            least_squares([1, 2], [1])

    def test_rejects_a_vertical_fit(self):
        with pytest.raises(ValueError, match="slope is undefined"):
            least_squares([2, 2, 2], [1, 2, 3])


class TestWarmSelection:
    def test_excludes_cold_and_missed_requests(self):
        run = _healthy_run(4)
        run.records[0] = RequestRecord(
            0, 0, 5_000, 0, 0, 10, 1.0, 0.0, 2, "tool_use", cost=0.01
        )
        assert len(warm_requests(run.records)) == 3

    def test_excludes_requests_with_no_billed_cost(self):
        run = _healthy_run(3)
        run.records[1] = RequestRecord(
            1, 0, 100, 5_000, 0, 10, 1.0, 5.0, 2, "tool_use", cost=None
        )
        assert len(warm_requests(run.records)) == 2


class TestCostPerContext:
    def test_recovers_the_cache_read_rate_from_billing_alone(self):
        # The headline self-check: a warm session must slope at the read rate.
        assert implied_read_rate(_healthy_run(), SONNET) == pytest.approx(0.20, abs=0.01)

    def test_fit_is_tight_on_a_healthy_session(self):
        assert cost_per_context(_healthy_run(), SONNET).r_squared > 0.99

    @pytest.mark.parametrize("model", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"])
    def test_holds_on_every_priced_model(self, model):
        expected = price_card(model).cache_read_per_mtok
        assert implied_read_rate(_healthy_run(model=model), model) == pytest.approx(
            expected, rel=0.05
        )

    def test_output_variance_does_not_distort_the_slope(self):
        # Output swings two orders of magnitude in a real session; fitting raw
        # cost against context measured r-squared 0.07 on a live run.
        run = _healthy_run(20)
        card = price_card(SONNET)
        for i, r in enumerate(run.records):
            out = 45 if i % 2 else 8_000
            delta = (out - r.output_tokens) * card.output_per_mtok / 1e6
            run.records[i] = RequestRecord(
                **{**r.__dict__, "output_tokens": out, "cost": (r.cost or 0) + delta}
            )
        assert implied_read_rate(run, SONNET) == pytest.approx(0.20, abs=0.02)
        assert cost_per_context(run, SONNET).r_squared > 0.99

    def test_agreement_is_high_on_a_healthy_session(self):
        assert read_rate_agreement(_healthy_run(), SONNET) > 0.95

    def test_raises_when_there_is_nothing_warm_to_fit(self):
        run = AgentRun(task="t", model=SONNET, policy="busy")
        run.records = [RequestRecord(0, 0, 100, 0, 0, 10, 1.0, 0.0, 2, "x", cost=0.01)]
        with pytest.raises(ValueError, match="not enough warm requests"):
            cost_per_context(run, SONNET)


class TestBrokenSessionIsDetected:
    def _frozen_breakpoint_run(self, n=20):
        """The bug found live: read pinned within a turn, tail at full price."""
        card = price_card(SONNET)
        run = AgentRun(task="t", model=SONNET, policy="busy")
        ctx, frozen, cum = 20_000, 20_000, 0.0
        for i in range(n):
            if i % 5 == 0:
                frozen = ctx
            uncached = ctx - frozen
            cost = (frozen * card.cache_read_per_mtok
                    + uncached * card.input_per_mtok) / 1e6
            cum += cost
            run.records.append(RequestRecord(
                index=i, turn=i // 5, cache_creation=0, cache_read=frozen,
                input_tokens=uncached, output_tokens=0, latency=1.0, gap=5.0,
                blocks_added=3, stop_reason="tool_use",
                context_tokens=ctx, cost=cost, cumulative_cost=cum,
            ))
            ctx += 3_000
        return run

    def test_weak_fit_flags_the_piecewise_signature(self):
        assert cost_per_context(self._frozen_breakpoint_run(), SONNET).r_squared < 0.9

    def test_summary_warns_loudly(self):
        text = summarize_scaling(self._frozen_breakpoint_run(), SONNET)
        assert "WARNING" in text and "breakpoint" in text

    def test_healthy_session_produces_no_warning(self):
        assert "WARNING" not in summarize_scaling(_healthy_run(), SONNET)


class TestCumulativeCurve:
    def test_reports_cumulative_at_each_turn_boundary(self):
        points = cumulative_by_turn(_healthy_run(20))
        assert [t for t, _ in points] == [0, 1, 2, 3, 4]
        assert [c for _, c in points] == sorted(c for _, c in points)

    def test_compares_linear_against_quadratic(self):
        result = quadratic_beats_linear(_healthy_run(40))
        assert result is not None
        linear_r2, quad_r2 = result
        assert 0 <= linear_r2 <= 1 and 0 <= quad_r2 <= 1

    def test_too_few_turns_returns_none_rather_than_a_false_verdict(self):
        assert quadratic_beats_linear(_healthy_run(8)) is None


class TestDirectSelfChecks:
    """Breakpoint health is checked directly, not inferred from a weak fit."""

    def test_healthy_session_breakpoint_advances(self):
        from context_clock.cachecost.analysis import breakpoint_advances

        assert breakpoint_advances(_healthy_run(20)) is True

    def test_frozen_breakpoint_is_detected_directly(self):
        from context_clock.cachecost.analysis import breakpoint_advances

        run = AgentRun(task="t", model=SONNET, policy="busy")
        for i in range(20):
            run.records.append(RequestRecord(
                i, i // 5, 0, 20_000, i * 100, 0, 1.0, 5.0, 3, "tool_use",
                context_tokens=20_000 + i * 100, cost=0.01))
        assert breakpoint_advances(run) is False

    def test_short_run_does_not_cry_wolf(self):
        from context_clock.cachecost.analysis import breakpoint_advances

        assert breakpoint_advances(_healthy_run(2)) is True

    def test_real_scatter_no_longer_triggers_a_false_warning(self):
        # A live Sonnet session fitted r-squared 0.616 against context with a
        # perfectly healthy breakpoint — variable write sizes, not a bug.
        from context_clock.cachecost.analysis import summarize_scaling

        run = _healthy_run(20)
        card = price_card(SONNET)
        for i, r in enumerate(run.records):
            write = 60 if i % 3 else 4_400
            delta = (write - r.cache_creation) * card.cache_write_5m_per_mtok / 1e6
            run.records[i] = RequestRecord(
                **{**r.__dict__, "cache_creation": write, "cost": (r.cost or 0) + delta})
        assert "WARNING" not in summarize_scaling(run, SONNET)

    def test_read_rate_recovered_exactly_when_writes_are_controlled(self):
        from context_clock.cachecost.analysis import read_rate_from_billing

        fit = read_rate_from_billing(_healthy_run(20), SONNET)
        assert fit.slope * 1e6 == pytest.approx(0.20, abs=0.001)
        assert fit.r_squared == pytest.approx(1.0, abs=0.001)

    def test_price_card_reproduces_a_consistent_bill_exactly(self):
        from context_clock.cachecost.analysis import model_reproduces_billing

        assert model_reproduces_billing(_healthy_run(20), SONNET) < 1e-12

    def test_discrepancy_is_surfaced_when_the_bill_disagrees(self):
        from context_clock.cachecost.analysis import model_reproduces_billing

        run = _healthy_run(5)
        run.records[2] = RequestRecord(**{**run.records[2].__dict__, "cost": 99.0})
        assert model_reproduces_billing(run, SONNET) > 90
