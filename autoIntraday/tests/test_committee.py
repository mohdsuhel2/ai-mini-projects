"""Committee aggregation — the veto, consensus thresholds, dissent, malformed verdicts."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from committee import (EXPERTS, MIN_BEARISH_FRACTION, MIN_VOTING_ANALYSTS, RISK_MANAGER,
                       CommitteeError, Verdict, aggregate, parse_verdict)


def _v(expert, verdict, conf=80, reason="because"):
    return Verdict(expert=expert, verdict=verdict, confidence=conf, reasoning=reason)


ANALYSTS = ("trend", "price_action", "volume", "smart_money", "options", "macro")


def _panel(bearish=6, rm="neutral", rm_conf=70, confs=None, bullish=0):
    """Six analysts plus a Risk Manager. `bearish` of them vote bearish, `bullish` against."""
    out, i = [], 0
    for e in ANALYSTS:
        if i < bearish:
            out.append(_v(e, "bearish", (confs[i] if confs else 80)))
        elif i < bearish + bullish:
            out.append(_v(e, "bullish", 75))
        else:
            out.append(_v(e, "neutral", 50))
        i += 1
    out.append(_v(RISK_MANAGER, rm, rm_conf))
    return out


def test_all_seven_experts_are_defined_with_briefs():
    assert len(EXPERTS) == 7
    for name in ("trend", "price_action", "volume", "smart_money", "options", "macro",
                 RISK_MANAGER):
        assert name in EXPERTS and len(EXPERTS[name]) > 40
    assert "VETO" in EXPERTS[RISK_MANAGER]


# ---- the veto -------------------------------------------------------------------------------
def test_risk_manager_veto_overrides_a_unanimous_bearish_committee():
    """The whole point: six experts screaming short cannot outvote the risk seat."""
    out = aggregate(_panel(bearish=6, rm="reject", rm_conf=90))
    assert out["recommendation"] == "REJECTED"
    assert out["vetoed"] is True
    assert out["consensus_confidence"] == 0.0
    assert "VETO" in out["rationale"] and "cannot override" in out["rationale"]


def test_a_missing_risk_manager_refuses_rather_than_assumes_safe():
    """An empty veto seat must not read as approval."""
    out = aggregate([_v(e, "bearish", 85) for e in ANALYSTS])
    assert out["recommendation"] == "REJECTED"
    assert "veto seat cannot be empty" in out["rationale"]


def test_risk_manager_bearish_or_neutral_does_not_block():
    for rm in ("neutral", "bearish", "bullish"):
        assert aggregate(_panel(bearish=5, rm=rm))["recommendation"] == "SHORT"


# ---- consensus ------------------------------------------------------------------------------
def test_a_split_committee_is_not_an_edge():
    """With all six voting, 2/3 means 4 — the same bar as before."""
    assert aggregate(_panel(bearish=3))["recommendation"] == "NO_TRADE"
    assert aggregate(_panel(bearish=4))["recommendation"] == "SHORT"


def test_consensus_is_the_mean_confidence_of_the_bearish_camp():
    out = aggregate(_panel(bearish=4, confs=[80, 90, 70, 60]))
    assert out["consensus_confidence"] == pytest.approx(75.0, abs=0.1)


def test_wide_disagreement_discounts_the_consensus():
    """A thesis resting on one loud expert is worth less than a tight one at the same mean."""
    tight = aggregate(_panel(bearish=4, confs=[75, 75, 75, 75]))["consensus_confidence"]
    wide = aggregate(_panel(bearish=4, confs=[95, 95, 55, 55]))["consensus_confidence"]
    assert tight == 75.0
    assert wide < tight                      # same mean, penalised for spread
    assert "discounted" in aggregate(_panel(bearish=4, confs=[95, 95, 55, 55]))["rationale"]


def test_each_bullish_dissenter_costs_the_consensus():
    none = aggregate(_panel(bearish=5, bullish=0))["consensus_confidence"]
    one = aggregate(_panel(bearish=5, bullish=1))["consensus_confidence"]
    assert one == none - 5.0
    assert "dissenter" in aggregate(_panel(bearish=5, bullish=1))["rationale"]


def test_votes_and_counts_are_reported_for_audit():
    out = aggregate(_panel(bearish=4, bullish=1))
    assert out["vote_counts"]["bearish"] == 4 and out["vote_counts"]["bullish"] == 1
    assert set(out["votes"]["bearish"]) <= set(ANALYSTS)
    assert len(out["expert_verdicts"]) == 7
    assert RISK_MANAGER not in out["votes"]["bearish"]      # the RM is not an analyst vote


def test_confidence_stays_in_range():
    out = aggregate(_panel(bearish=4, confs=[10, 10, 10, 10], bullish=2))
    assert 0.0 <= out["consensus_confidence"] <= 100.0


# ---- parsing --------------------------------------------------------------------------------
def test_parse_verdict_accepts_a_well_formed_reply():
    v = parse_verdict("trend", {"verdict": "Bearish", "confidence": 82, "reasoning": "MAs rolling"})
    assert v.verdict == "bearish" and v.confidence == 82.0 and v.reasoning == "MAs rolling"


def test_parse_verdict_refuses_anything_unusable():
    for bad in ({"verdict": "maybe", "confidence": 50},
                {"verdict": "bearish", "confidence": "high"},
                {"verdict": "bearish", "confidence": 150},
                {"verdict": "bearish"},
                ["not", "an", "object"]):
        with pytest.raises(CommitteeError):
            parse_verdict("trend", bad)


def test_duplicate_or_empty_verdicts_are_refused():
    with pytest.raises(CommitteeError, match="no verdicts"):
        aggregate([])
    with pytest.raises(CommitteeError, match="duplicate"):
        aggregate([_v("trend", "bearish"), _v("trend", "bearish"), _v(RISK_MANAGER, "neutral")])


def test_aggregation_is_deterministic():
    panel = _panel(bearish=5, confs=[80, 70, 90, 60, 75])
    a, b = aggregate(panel), aggregate(panel)
    assert a["recommendation"] == b["recommendation"]
    assert a["consensus_confidence"] == b["consensus_confidence"]


# ---- abstention-aware quorum (added after the 2026-08-01 backtest rejected 5/5 candidates) ----
def _panel_abstain(bearish, abstainers, rm="neutral"):
    out, i = [], 0
    for e in ANALYSTS:
        if e in abstainers:
            out.append(_v(e, "abstain", 0, "no data for my mandate"))
        elif i < bearish:
            out.append(_v(e, "bearish", 80)); i += 1
        else:
            out.append(_v(e, "neutral", 50))
    out.append(_v(RISK_MANAGER, rm, 70))
    return out


def test_abstainers_shrink_the_quorum_instead_of_voting_against():
    """The bug this fixes: with options+macro blind, a fixed 4-of-6 demanded unanimity from the
    remaining four. 3 of 4 voting is the same 2/3 bar and should pass."""
    out = aggregate(_panel_abstain(bearish=3, abstainers={"options", "macro"}))
    assert out["recommendation"] == "SHORT"
    assert "3/4 voting analysts bearish" in out["rationale"]
    assert "abstained" in out["rationale"]


def test_the_same_votes_as_neutrals_would_fail():
    """Proves abstain and neutral are genuinely different, not cosmetic."""
    assert aggregate(_panel(bearish=3))["recommendation"] == "NO_TRADE"


def test_the_bar_stays_two_thirds_of_whoever_voted():
    assert aggregate(_panel_abstain(bearish=2, abstainers={"options", "macro"}))["recommendation"] == "NO_TRADE"
    assert aggregate(_panel_abstain(bearish=3, abstainers={"options", "macro"}))["recommendation"] == "SHORT"


def test_too_few_voters_is_not_a_committee():
    out = aggregate(_panel_abstain(bearish=2, abstainers={"options", "macro", "volume", "trend"}))
    assert out["recommendation"] == "NO_TRADE"
    assert "not a committee" in out["rationale"]
    assert MIN_VOTING_ANALYSTS == 3


def test_abstain_is_a_valid_parsed_verdict():
    assert parse_verdict("options", {"verdict": "abstain", "confidence": 0}).verdict == "abstain"


def test_the_veto_still_beats_an_abstention_reduced_quorum():
    out = aggregate(_panel_abstain(bearish=3, abstainers={"options", "macro"}, rm="reject"))
    assert out["recommendation"] == "REJECTED" and out["vetoed"] is True
