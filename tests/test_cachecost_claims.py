"""The claim ledger — every numeric verdict re-derives from the price card.

This is the regression guard on the validation itself: if a rate changes, or
someone edits a stated figure, the mismatch surfaces here rather than in a
published report.
"""

import pytest

from context_clock.cachecost.claims import (
    CLAIMS,
    CONFIRMED,
    CONSISTENT,
    MISSING,
    ORG_SPECIFIC,
    REFINED,
    UNVERIFIABLE,
    annual_miss_overpay,
)
from context_clock.cachecost.verify import main

_VERDICTS = {CONFIRMED, CONSISTENT, REFINED, ORG_SPECIFIC, UNVERIFIABLE, MISSING}


class TestLedgerIntegrity:
    def test_claim_ids_are_unique(self):
        ids = [c.id for c in CLAIMS]
        assert len(ids) == len(set(ids))

    def test_every_verdict_is_a_known_value(self):
        assert all(c.verdict in _VERDICTS for c in CLAIMS)

    def test_every_claim_carries_evidence(self):
        assert all(c.evidence.strip() for c in CLAIMS)

    def test_unverifiable_claims_carry_no_numeric_check(self):
        # Asserting a derived number for a claim we cannot check would be
        # exactly the fabrication the project forbids.
        for claim in CLAIMS:
            if claim.verdict in (UNVERIFIABLE, ORG_SPECIFIC, MISSING):
                assert claim.check is None, claim.id

    def test_confirmed_and_consistent_claims_are_mostly_checked(self):
        checkable = [c for c in CLAIMS if c.verdict in (CONFIRMED, CONSISTENT)]
        assert sum(1 for c in checkable if c.check) >= len(checkable) // 2


class TestClaimVerdicts:
    @pytest.mark.parametrize("claim", [c for c in CLAIMS if c.check], ids=lambda c: c.id)
    def test_derived_value_reproduces_the_stated_one(self, claim):
        derived, stated = claim.evaluate()
        assert claim.holds(), (
            f"{claim.id}: derived {derived} does not reproduce stated {stated}"
        )

    def test_claims_without_checks_hold_vacuously(self):
        assert all(c.holds() for c in CLAIMS if c.check is None)


class TestProjectionModel:
    def test_scales_linearly_with_headcount(self):
        args = dict(
            misses_per_engineer_per_day=10,
            avg_context_tokens=275_000,
            model="claude-sonnet-5",
        )
        one = annual_miss_overpay(engineers=100, **args)
        two = annual_miss_overpay(engineers=200, **args)
        assert two == pytest.approx(2 * one)

    def test_overpay_is_net_of_what_a_hit_would_have_cost(self):
        # One engineer, one miss, one day at 1M tokens: 1.25x - 0.1x = 1.15x.
        overpay = annual_miss_overpay(
            engineers=1,
            misses_per_engineer_per_day=1,
            avg_context_tokens=1_000_000,
            model="claude-sonnet-5",
            working_days=1,
        )
        assert overpay == pytest.approx(2.30)  # (2.50 - 0.20)

    def test_rejects_negative_inputs(self):
        with pytest.raises(ValueError, match="non-negative"):
            annual_miss_overpay(
                engineers=-1,
                misses_per_engineer_per_day=1,
                avg_context_tokens=1,
                model="claude-sonnet-5",
            )


class TestVerifyCli:
    def test_ledger_exits_clean(self, capsys):
        assert main([]) == 0
        assert "claim ledger" in capsys.readouterr().out

    def test_scenarios_flag_adds_derived_section(self, capsys):
        assert main(["--scenarios"]) == 0
        out = capsys.readouterr().out
        assert "Derived scenarios" in out
        assert "not measured" in out  # derived figures must be labelled
