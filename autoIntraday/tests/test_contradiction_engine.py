"""Contradiction engine — every rule, the penalty maths, the 3-major reject, blind spots."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from contradiction_engine import (MAJOR_PENALTY, MINOR_PENALTY, REJECT_AT_MAJOR_COUNT, RULES,
                                  ContradictionError, analyse)

# A context in which NOTHING contradicts — every rule evaluable and every one clean.
CLEAN = {
    "pattern_strength": 85, "rvol": 2.2, "sector_making_highs": False, "pcr_oi": 0.8,
    "vix_change_pct": 2.0, "positive_announcement": False, "trend_bearish": True,
    "rsi_rising": False, "near_support": False, "rsi14": 48, "has_catalyst": True,
    "bullish_divergence": False, "max_pain": 95.0, "spot": 100.0, "pct_below_20d_high": 4.0,
}


def _ctx(**over):
    c = dict(CLEAN)
    c.update(over)
    return c


def test_a_clean_thesis_keeps_its_confidence():
    out = analyse(84, CLEAN)
    assert out["contradictions_found"] == []
    assert out["confidence_penalty"] == 0
    assert out["final_adjusted_confidence"] == 84.0
    assert out["rejected"] is False
    assert out["unchecked_rules"] == []          # every rule was actually evaluable
    assert "No contradictions found" in out["verdict"]


def test_every_rule_has_a_severity_penalty_and_inputs():
    for r in RULES:
        assert r.severity in ("major", "minor")
        assert r.needs and r.describe


@pytest.mark.parametrize("over,rule_id", [
    ({"rvol": 1.1}, "bearish_candle_low_rvol"),
    ({"sector_making_highs": True}, "breakdown_vs_sector_highs"),
    ({"pcr_oi": 1.4}, "heavy_put_writing"),
    ({"vix_change_pct": -8.0}, "vix_collapsing"),
    ({"positive_announcement": True}, "positive_announcement"),
    ({"rsi_rising": True}, "momentum_improving"),
    ({"near_support": True}, "near_support_not_resistance"),
    ({"rsi14": 26, "has_catalyst": False}, "oversold_no_catalyst"),
    ({"bullish_divergence": True}, "bullish_divergence"),
    ({"max_pain": 110.0}, "max_pain_above_spot"),
    ({"pct_below_20d_high": 20.0}, "already_extended_down"),
])
def test_each_contradiction_is_detected_in_isolation(over, rule_id):
    """Every example from the brief, one at a time, against an otherwise clean thesis."""
    out = analyse(84, _ctx(**over))
    assert rule_id in [c["id"] for c in out["contradictions_found"]]
    assert out["confidence_penalty"] > 0
    assert out["final_adjusted_confidence"] < 84


def test_penalties_are_severity_weighted():
    major = analyse(84, _ctx(near_support=True))          # major
    minor = analyse(84, _ctx(vix_change_pct=-8.0))        # minor
    assert major["confidence_penalty"] == MAJOR_PENALTY
    assert minor["confidence_penalty"] == MINOR_PENALTY
    assert MAJOR_PENALTY > MINOR_PENALTY


def test_penalties_accumulate():
    out = analyse(90, _ctx(vix_change_pct=-8.0, rsi_rising=True))   # two minors
    assert out["minor_count"] == 2 and out["major_count"] == 0
    assert out["confidence_penalty"] == 2 * MINOR_PENALTY
    assert out["final_adjusted_confidence"] == 90 - 2 * MINOR_PENALTY


def test_three_majors_rejects_outright_whatever_the_score():
    """The core rule: a high score does not survive three major contradictions."""
    out = analyse(95, _ctx(near_support=True, sector_making_highs=True,
                           positive_announcement=True))
    assert out["major_count"] >= REJECT_AT_MAJOR_COUNT
    assert out["rejected"] is True
    assert out["final_adjusted_confidence"] == 0.0
    assert "REJECTED" in out["verdict"]


def test_two_majors_penalise_but_do_not_reject():
    out = analyse(95, _ctx(near_support=True, sector_making_highs=True))
    assert out["major_count"] == 2 and out["rejected"] is False
    assert out["final_adjusted_confidence"] == 95 - 2 * MAJOR_PENALTY


def test_confidence_never_goes_negative():
    out = analyse(10, _ctx(near_support=True, vix_change_pct=-8.0))
    assert out["final_adjusted_confidence"] >= 0.0


def test_unevaluable_rules_are_reported_as_blind_spots_not_passes():
    """'We could not look' must never read as 'we looked and it was fine' — otherwise a thin
    context silently produces a clean bill of health."""
    out = analyse(84, {"rvol": 2.0, "pattern_strength": 80})     # almost everything missing
    assert out["contradictions_found"] == []
    assert out["unchecked_rules"]                                 # ...but far from clean
    ids = [u["id"] for u in out["unchecked_rules"]]
    assert "near_support_not_resistance" in ids and "heavy_put_writing" in ids
    assert "NOT checked" in out["verdict"] and "blind spots" in out["verdict"]


def test_a_missing_input_does_not_fire_its_rule():
    out = analyse(84, _ctx(pcr_oi=None))                          # options unavailable
    assert "heavy_put_writing" not in [c["id"] for c in out["contradictions_found"]]
    assert "heavy_put_writing" in [u["id"] for u in out["unchecked_rules"]]


def test_oversold_with_a_catalyst_is_not_a_contradiction():
    """RSI 26 is only damning when nothing new is driving it."""
    assert analyse(84, _ctx(rsi14=26, has_catalyst=True))["contradictions_found"] == []
    assert analyse(84, _ctx(rsi14=26, has_catalyst=False))["major_count"] == 1


def test_bad_confidence_is_refused():
    for bad in (-1, 101, "high", None):
        with pytest.raises(ContradictionError):
            analyse(bad, CLEAN)
