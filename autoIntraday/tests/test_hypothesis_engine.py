"""Competing Hypothesis Engine — no trend bias, objective-only veto, unknown != negative."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from hypothesis_engine import (HYPOTHESES, KNOWN_NEGATIVE, KNOWN_POSITIVE, MIN_EDGE,
                               MIN_REVERSAL_PROB, RISK_MANAGER_BRIEF, SPECIALISTS, UNKNOWN,
                               Hypothesis, HypothesisError, RiskAssessment, count_unknowns,
                               decide)


def _h(name, p, **kw):
    return Hypothesis(name, p, kw.get("supporting", []), kw.get("weaknesses", []), kw.get("s", ""))


def _risk(level="low", veto=False, reason=None, size=100.0):
    return RiskAssessment(risk_level=level, veto=veto, veto_reason=reason, position_size_pct=size)


# ---- structure ------------------------------------------------------------------------------
def test_specialists_have_disjoint_domains_and_supply_evidence_not_verdicts():
    assert set(SPECIALISTS) == {"trend", "price_action", "volume", "smart_money", "options",
                                "macro"}
    for name, brief in SPECIALISTS.items():
        assert "Nothing else" in brief, f"{name} does not fence its domain"
    assert "never guess" in SPECIALISTS["options"].lower()


def test_two_hypotheses_argue_opposite_cases():
    assert set(HYPOTHESES) == {"continuation", "reversal"}
    assert "CONTINUES" in HYPOTHESES["continuation"]
    assert "ENDING" in HYPOTHESES["reversal"]
    # the anti-bias instruction that the old committee lacked
    assert "BEFORE obvious trend deterioration" in HYPOTHESES["reversal"]


def test_risk_manager_is_forbidden_from_judging_direction():
    b = RISK_MANAGER_BRIEF
    assert "NEVER judge market direction" in b
    assert "countertrend" in b and "may NOT reject" in b
    assert "MISSING DATA IS NOT A VETO" in b


# ---- the bias fix ---------------------------------------------------------------------------
def test_a_reversal_wins_on_probability_not_on_votes():
    """INDUSINDBK's failure: three lagging experts outvoted one correct reversal read. Here the
    reversal case wins on strength of evidence alone."""
    out = decide(_h("continuation", 35), _h("reversal", 72), _risk("low"))
    assert out["decision"] == "SHORT"
    assert out["reversal_probability"] == 72 and out["continuation_probability"] == 35
    assert out["edge"] == 37


def test_a_strong_continuation_case_blocks_the_short():
    out = decide(_h("continuation", 75), _h("reversal", 60), _risk("low"))
    assert out["decision"] == "NO_TRADE" and "does not beat" in out["rationale"]


def test_a_weak_reversal_case_is_refused_even_with_an_edge():
    out = decide(_h("continuation", 20), _h("reversal", 45), _risk("low"))
    assert out["decision"] == "NO_TRADE" and "too weak on its own" in out["rationale"]
    assert MIN_REVERSAL_PROB == 55.0


def test_hypotheses_need_not_sum_to_one_hundred():
    """They are independent teams, not a partition of one probability. Both may be confident
    (a genuinely ambiguous tape) or both unsure — the engine must accept either without
    normalising, and judge on the EDGE between them."""
    both_high = decide(_h("continuation", 80), _h("reversal", 85), _risk("low"))
    assert both_high["continuation_probability"] == 80      # not rescaled to complement
    assert both_high["reversal_probability"] == 85
    assert both_high["edge"] == 5
    assert both_high["decision"] == "NO_TRADE"              # 5 < MIN_EDGE: too close to separate

    both_low = decide(_h("continuation", 20), _h("reversal", 30), _risk("low"))
    assert both_low["continuation_probability"] == 20 and both_low["reversal_probability"] == 30
    assert both_low["decision"] == "NO_TRADE"               # reversal too weak on its own


def test_edge_threshold_separates_close_calls():
    assert decide(_h("c", 50), _h("reversal", 59), _risk("low"))["decision"] == "NO_TRADE"
    assert decide(_h("c", 50), _h("reversal", 61), _risk("low"))["decision"] == "SHORT"
    assert MIN_EDGE == 10.0


# ---- risk: objective veto only ---------------------------------------------------------------
def test_an_objective_veto_rejects_and_says_direction_was_not_the_issue():
    out = decide(_h("continuation", 20), _h("reversal", 90),
                 _risk(veto=True, reason="earnings tomorrow"))
    assert out["decision"] == "REJECTED"
    assert "earnings tomorrow" in out["rationale"]
    assert "Direction was not the issue" in out["rationale"]
    assert out["position_size_pct"] == 0.0


def test_risk_level_scales_position_size_rather_than_blocking():
    """High risk shrinks the position; it does not kill the trade. The old design only had a veto."""
    low = decide(_h("c", 30), _h("reversal", 75), _risk("low"))
    med = decide(_h("c", 30), _h("reversal", 75), _risk("medium"))
    high = decide(_h("c", 30), _h("reversal", 75), _risk("high"))
    assert low["decision"] == med["decision"] == high["decision"] == "SHORT"
    assert low["position_size_pct"] > med["position_size_pct"] > high["position_size_pct"]


def test_risk_manager_may_cap_size_below_its_level_default():
    out = decide(_h("c", 30), _h("reversal", 75), _risk("low", size=25.0))
    assert out["position_size_pct"] == 25.0


def test_an_invalid_risk_level_is_refused():
    with pytest.raises(HypothesisError, match="risk_level"):
        decide(_h("c", 30), _h("reversal", 75), _risk("catastrophic"))


# ---- unknown is not negative -----------------------------------------------------------------
def test_unknown_findings_trim_confidence_but_never_reject():
    """Every veto in the 2026-08-01 backtest ended with 'event risk cannot be cleared'. Unknown
    must cost a little confidence and nothing more."""
    findings = [{"state": UNKNOWN}] * 6 + [{"state": KNOWN_POSITIVE}] * 4
    out = decide(_h("c", 30), _h("reversal", 75), _risk("low"), findings)
    assert out["decision"] == "SHORT"                 # still trades
    assert out["confidence"] < 75                     # but less certain
    assert out["unknown_findings"] == 6 and out["total_findings"] == 10
    assert "unknown is not negative evidence" in out["rationale"]


def test_the_unknown_penalty_is_capped():
    many = [{"state": UNKNOWN}] * 50
    out = decide(_h("c", 30), _h("reversal", 90), _risk("low"), many)
    assert out["decision"] == "SHORT"
    assert out["confidence"] >= 90 - 20               # MAX_UNKNOWN_COST


def test_known_negative_does_not_trim_confidence_the_way_unknown_does():
    neg = [{"state": KNOWN_NEGATIVE}] * 6
    unk = [{"state": UNKNOWN}] * 6
    assert (decide(_h("c", 30), _h("reversal", 75), _risk("low"), neg)["confidence"]
            > decide(_h("c", 30), _h("reversal", 75), _risk("low"), unk)["confidence"])


def test_count_unknowns():
    assert count_unknowns([{"state": UNKNOWN}, {"state": KNOWN_POSITIVE}]) == (1, 2)
    assert count_unknowns([]) == (0, 0)
    assert count_unknowns(None) == (0, 0)


# ---- expected value --------------------------------------------------------------------------
def test_negative_expected_value_blocks_the_trade():
    """A 56% reversal case at 0.5:1 loses money even though it wins more often than not."""
    out = decide(_h("c", 40), _h("reversal", 56), _risk("low"), rr=0.5)
    assert out["decision"] == "NO_TRADE" and "expected value" in out["rationale"].lower()


def test_expected_value_is_reported_in_r_multiples():
    out = decide(_h("c", 30), _h("reversal", 60), _risk("low"), rr=2.0)
    assert out["expected_value_r"] == pytest.approx(0.6 * 2 - 0.4, abs=0.01)


def test_output_carries_every_required_field():
    out = decide(_h("continuation", 30, supporting=["a"]), _h("reversal", 75, weaknesses=["b"]),
                 _risk("medium"), [{"state": UNKNOWN}])
    for k in ("decision", "continuation_probability", "reversal_probability", "risk_score",
              "expected_value_r", "confidence", "position_size_pct", "rationale",
              "continuation_case", "reversal_case", "risk"):
        assert k in out, f"missing {k}"
    assert out["continuation_case"]["supporting"] == ["a"]
    assert out["reversal_case"]["weaknesses"] == ["b"]


def test_probabilities_outside_range_are_refused():
    with pytest.raises(HypothesisError):
        decide(_h("c", 30), _h("reversal", 150), _risk("low"))


def test_decide_is_deterministic():
    args = (_h("c", 32), _h("reversal", 71), _risk("medium"), [{"state": UNKNOWN}])
    a, b = decide(*args), decide(*args)
    assert a == b
