"""Adaptive scoring — weight tables, regime blending, renormalisation, explanations."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from adaptive_scoring import FACTORS, REGIMES, ScoringError, score, weights_for


def test_every_regime_vector_sums_to_one_and_uses_known_factors():
    """A vector that does not sum to 1 silently rescales the whole score."""
    for name, spec in REGIMES.items():
        total = sum(spec["weights"].values())
        assert abs(total - 1.0) < 1e-9, f"{name} sums to {total}"
        unknown = set(spec["weights"]) - set(FACTORS)
        assert not unknown, f"{name} references unknown factor(s) {unknown}"
        assert spec["why"], f"{name} has no rationale"


def test_all_ten_requested_regimes_exist():
    for r in ("strong_bull_trend", "strong_bear_trend", "sideways_range", "high_volatility",
              "low_volatility", "expiry_week", "event_driven", "earnings_heavy",
              "panic_selling", "momentum_rally"):
        assert r in REGIMES


def test_weights_differ_by_regime_that_is_the_whole_point():
    bear, _ = weights_for(["strong_bear_trend"])
    rng, _ = weights_for(["sideways_range"])
    assert bear["trend"] == 0.25
    assert rng.get("trend", 0) == 0                 # trend barely exists in a range
    assert rng["resistance_rejection"] == 0.25      # levels decide instead
    assert weights_for(["expiry_week"])[0]["options"] == 0.28   # positioning dominates


def test_regimes_blend_rather_than_one_winning():
    """Expiry week during high volatility is BOTH, not whichever was named first."""
    blended, why = weights_for(["expiry_week", "high_volatility"])
    assert abs(sum(blended.values()) - 1.0) < 1e-9
    assert blended["options"] == pytest.approx((0.28 + 0.10) / 2)
    assert blended["volatility"] == pytest.approx((0.05 + 0.20) / 2)
    assert len(why) == 2


def test_unknown_or_empty_regime_is_refused():
    with pytest.raises(ScoringError, match="unknown regime"):
        weights_for(["moon_phase"])
    with pytest.raises(ScoringError, match="at least one regime"):
        weights_for([])


def test_final_score_uses_the_regime_weights():
    out = score({"trend": 100, "price_action": 0, "volume": 0, "relative_weakness": 0,
                 "options": 0, "momentum": 0, "volatility": 0, "news": 0},
                ["strong_bear_trend"])
    assert out["final_score"] == 25.0                # trend alone carries 25% there


def test_the_same_scores_yield_a_different_result_under_a_different_regime():
    scores = {"trend": 100, "resistance_rejection": 0, "price_action": 50, "volume": 50,
              "options": 50, "momentum": 50, "news": 50, "mean_reversion": 50,
              "relative_weakness": 50, "volatility": 50}
    bear = score(scores, ["strong_bear_trend"])["final_score"]
    rng = score(scores, ["sideways_range"])["final_score"]
    assert bear > rng                                # trend is rewarded in a downtrend, ignored in a range


def test_unavailable_factors_are_renormalised_not_scored_zero():
    """The crux. Zero is a bearish signal; absent is not. A missing option chain must never
    silently become a short thesis."""
    all_fifty = {"trend": 50, "price_action": 50, "volume": 50, "relative_weakness": 50,
                 "options": 50, "momentum": 50, "volatility": 50, "news": 50}
    full = score(all_fifty, ["strong_bear_trend"])
    without = score({**all_fifty, "options": None}, ["strong_bear_trend"])
    assert full["final_score"] == 50.0
    assert without["final_score"] == 50.0            # unchanged, not dragged down
    assert without["renormalised"] is True
    assert without["dropped_unavailable"] == ["options"]
    # Effective weights sum to 1 across the survivors. Tolerance is 1e-3, not 1e-9: these are
    # rounded to 4dp for readable output, so ~8 of them accumulate up to 4e-4 of rounding error.
    # The underlying maths is exact — final_score above is precisely 50.0.
    assert abs(sum(c["weight_effective"] for c in without["contributions"]) - 1.0) < 1e-3


def test_scoring_zero_is_treated_as_genuinely_bearish_unlike_null():
    all_fifty = {"trend": 50, "price_action": 50, "volume": 50, "relative_weakness": 50,
                 "options": 50, "momentum": 50, "volatility": 50, "news": 50}
    zeroed = score({**all_fifty, "options": 0}, ["strong_bear_trend"])["final_score"]
    nulled = score({**all_fifty, "options": None}, ["strong_bear_trend"])["final_score"]
    assert zeroed < nulled


def test_a_weighted_factor_the_skill_never_scored_is_reported():
    out = score({"trend": 60, "price_action": 60}, ["strong_bear_trend"])
    assert "volume" in out["unscored_but_weighted"]
    assert out["renormalised"] is True


def test_explanation_names_the_regime_the_drivers_and_the_gaps():
    out = score({"trend": 90, "price_action": 80, "volume": 70, "relative_weakness": 60,
                 "options": None, "momentum": 50, "volatility": 50, "news": 50},
                ["strong_bear_trend"])
    e = out["explanation"]
    assert "strong_bear_trend" in e and "continuation beats cleverness" in e
    assert "Largest contributors" in e and "trend" in e
    assert "NOT scored zero" in e and "options" in e


def test_contributions_are_ordered_and_sum_to_the_final_score():
    out = score({"trend": 90, "price_action": 80, "volume": 70, "relative_weakness": 60,
                 "options": 40, "momentum": 50, "volatility": 50, "news": 50},
                ["strong_bear_trend"])
    cons = [c["contribution"] for c in out["contributions"]]
    assert cons == sorted(cons, reverse=True)
    assert sum(cons) == pytest.approx(out["final_score"], abs=0.1)


def test_unknown_factor_and_all_null_are_refused():
    with pytest.raises(ScoringError, match="unknown factor"):
        score({"vibes": 90}, ["strong_bear_trend"])
    with pytest.raises(ScoringError, match="no scored factor"):
        score({"trend": None, "price_action": None}, ["strong_bear_trend"])
