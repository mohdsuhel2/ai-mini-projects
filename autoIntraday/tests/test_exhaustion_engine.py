"""Bidirectional Trend Exhaustion Engine — both sides, confluence, and the rules it must not break."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reversal_radar import Candle
from exhaustion_engine import (BEAR_STAGES, BULL_STAGES, BULL_WEIGHTS, BEAR_WEIGHTS,
                               EXPECTED_DIRECTIONS, FAMILY, MIN_FAMILIES_FOR_CONVICTION,
                               OPPORTUNITIES, analyse, analyse_bearish, analyse_bullish,
                               from_indicator_json, read_trend, summarise)


def _rally(n=12, start=100.0, step=1.5):
    """A steep advance ending in a big upper shadow and a weak close."""
    out = [Candle(f"{9 + i // 4:02d}:{(i * 15) % 60:02d}", start + step * i - 0.2,
                  start + step * i + 0.3, start + step * i - 0.3, start + step * i, 1000)
           for i in range(n - 1)]
    top = start + step * (n - 1)
    out.append(Candle("11:00", top - 0.2, top + 2.5, top - 0.4, top - 0.1, 5000))
    return out


def _selloff(n=12, start=130.0, step=1.5):
    """A steep decline ending in a big LOWER shadow and a close off the low — the mirror image."""
    out = [Candle(f"{9 + i // 4:02d}:{(i * 15) % 60:02d}", start - step * i + 0.2,
                  start - step * i + 0.3, start - step * i - 0.3, start - step * i, 1000)
           for i in range(n - 1)]
    bot = start - step * (n - 1)
    out.append(Candle("11:00", bot + 0.2, bot + 0.4, bot - 2.5, bot + 0.1, 5000))
    return out


def _flat(n=12, start=100.0):
    return [Candle(f"{9 + i // 4:02d}:{(i * 15) % 60:02d}", start, start + 0.3, start - 0.3,
                   start, 1000) for i in range(n)]


# ---- the contract -----------------------------------------------------------------------------
def test_it_never_issues_a_trading_decision():
    """The engine's whole safety rests on this: evidence for the decision engine, never an order."""
    out = analyse(_rally())
    flat = str(out)
    for forbidden in ("'action'", "'decision'", "'side'", "'entry'", "'stop'", "'target'",
                      "'recommendation'"):
        assert forbidden not in flat, f"leaked a decision field: {forbidden}"
    assert "never issues BUY or SELL" in out["note"]


def test_both_sides_are_evaluated_independently_every_time():
    """The old radar only ever measured the bullish side, so it could only suppress longs."""
    out = analyse(_selloff(), rsi_series=[28] * 8, rvol=2.4)
    assert "bullish_exhaustion" in out and "bearish_exhaustion" in out
    assert out["bearish_exhaustion"]["score"] > 0     # a downtrend is being measured at all


def test_the_two_stage_ladders_differ_only_where_the_mechanic_differs():
    assert BULL_STAGES[:4] == BEAR_STAGES[:4]
    assert BULL_STAGES[4] == "distribution" and BEAR_STAGES[4] == "accumulation"
    assert BULL_STAGES[-1] == BEAR_STAGES[-1] == "high_probability_reversal"
    assert len(BULL_STAGES) == len(BEAR_STAGES) == 6


def test_every_signal_belongs_to_a_known_family():
    for weights in (BULL_WEIGHTS, BEAR_WEIGHTS):
        for name, (weight, fam) in weights.items():
            assert fam in FAMILY, f"{name} has unknown family {fam}"
            assert weight > 0


# ---- the central rule: extremity is not direction ---------------------------------------------
def test_a_stock_at_new_highs_is_not_automatically_bullish():
    """The spec's core instruction. Direction comes from structure; a new high does not vote."""
    bars = _rally()
    # price at its high, but every EMA above it and sellers holding the DI spread
    trend, _, ev = read_trend(bars, ema20=bars[-1].c + 5, ema50=bars[-1].c + 8,
                              ema200=bars[-1].c + 12, adx=30, plus_di=15, minus_di=28)
    assert trend != "up", f"new highs alone made it bullish: {ev}"


def test_a_stock_at_new_lows_is_not_automatically_bearish():
    bars = _selloff()
    trend, _, ev = read_trend(bars, ema20=bars[-1].c - 5, ema50=bars[-1].c - 8,
                              ema200=bars[-1].c - 12, adx=30, plus_di=28, minus_di=15)
    assert trend != "down", f"new lows alone made it bearish: {ev}"


def test_structure_not_price_extremity_sets_the_trend():
    bars = _rally()
    up, _, _ = read_trend(bars, ema20=bars[-1].c - 3, ema50=bars[-1].c - 6,
                          ema200=bars[-1].c - 10, adx=32, plus_di=30, minus_di=12)
    assert up == "up"


# ---- bullish exhaustion (the SHORT case) -------------------------------------------------------
def test_a_distributing_rally_scores_high_bullish_exhaustion():
    bars = _rally()
    r = analyse_bullish(bars, rsi_series=[85, 86, 84, 82, 80, 78, 76, 74], rvol=2.5,
                        vwap=bars[-1].c * 0.94, ema20=bars[-1].c - 6, ema50=bars[-1].c - 10,
                        ema200=bars[-1].c - 20, atr=1.0, bb_percent_b=0.98,
                        stock_change_pct=0.2, index_change_pct=1.4, sector_change_pct=-0.6,
                        flow_estimate="Likely Distribution", bars_since_day_low=10)
    assert r.stage in ("distribution", "high_probability_reversal")
    assert "rsi_bearish_divergence" in r.signals
    assert "smart_money_distribution" in r.signals
    assert "relative_weakness_near_highs" in r.signals
    assert len(r.families) >= 4


def test_the_named_bullish_signals_from_the_spec_all_fire():
    bars = _rally()
    r = analyse_bullish(bars, rsi_series=[85, 86, 84, 82, 80, 78, 76, 74], rvol=2.5,
                        vwap=bars[-1].c * 0.9, ema20=bars[-1].c - 8, ema50=bars[-1].c - 12,
                        ema200=bars[-1].c - 25, atr=1.0, bb_percent_b=0.99,
                        stock_change_pct=0.1, index_change_pct=2.0, sector_change_pct=-1.0,
                        flow_estimate="Likely Distribution", bars_since_day_low=11)
    for expected in ("rsi_overbought", "momentum_slowdown", "rsi_bearish_divergence",
                     "higher_high_weaker_momentum", "far_above_vwap", "far_above_ema20",
                     "far_above_ema50", "far_above_ema200", "atr_extension_up",
                     "bollinger_exhaustion_up", "large_upper_shadows", "buying_climax",
                     "volume_climax", "premium_in_day_range", "sector_weakening",
                     "relative_weakness_near_highs", "smart_money_distribution",
                     "trend_maturity"):
        assert expected in r.signals, f"{expected} did not fire"


def test_failed_breakout_needs_a_probe_above_that_closes_back_under():
    bars = _flat(10)
    hi = max(c.h for c in bars)
    bars.append(Candle("11:30", hi + 0.1, hi + 2.0, hi - 0.5, hi - 0.6, 3000))
    assert "failed_breakout" in analyse_bullish(bars).signals


# ---- bearish exhaustion (the LONG case — the new half) -----------------------------------------
def test_a_capitulating_selloff_scores_high_bearish_exhaustion():
    """The reading the one-directional radar could never produce."""
    bars = _selloff()
    r = analyse_bearish(bars, rsi_series=[15, 14, 16, 18, 20, 22, 24, 26], rvol=2.6,
                        vwap=bars[-1].c * 1.06, ema20=bars[-1].c + 6, ema50=bars[-1].c + 10,
                        ema200=bars[-1].c + 20, atr=1.0, bb_percent_b=0.02,
                        stock_change_pct=-0.2, index_change_pct=-1.4, sector_change_pct=0.6,
                        flow_estimate="Likely Accumulation", bars_since_day_high=10)
    assert r.stage in ("accumulation", "high_probability_reversal")
    assert "rsi_bullish_divergence" in r.signals
    assert "smart_money_accumulation" in r.signals
    assert len(r.families) >= 4


def test_the_named_bearish_signals_from_the_spec_all_fire():
    bars = _selloff()
    r = analyse_bearish(bars, rsi_series=[15, 14, 16, 18, 20, 22, 24, 26], rvol=2.6,
                        vwap=bars[-1].c * 1.1, ema20=bars[-1].c + 8, ema50=bars[-1].c + 12,
                        ema200=bars[-1].c + 25, atr=1.0, bb_percent_b=0.01,
                        stock_change_pct=-0.1, index_change_pct=-2.0, sector_change_pct=1.0,
                        flow_estimate="Likely Accumulation", bars_since_day_high=11)
    for expected in ("rsi_oversold", "momentum_improving", "rsi_bullish_divergence",
                     "lower_low_improving_momentum", "far_below_vwap", "far_below_ema20",
                     "far_below_ema50", "far_below_ema200", "atr_extension_down",
                     "bollinger_exhaustion_down", "large_lower_shadows", "selling_climax",
                     "capitulation_volume", "discount_in_day_range", "sector_strengthening",
                     "relative_strength_near_lows", "smart_money_accumulation", "trend_maturity"):
        assert expected in r.signals, f"{expected} did not fire"


def test_rsi_bullish_divergence_is_a_lower_low_with_a_higher_rsi_low():
    bars = _selloff()
    diverging = analyse_bearish(bars, rsi_series=[12, 14, 16, 18, 20, 22, 24, 26])
    confirming = analyse_bearish(bars, rsi_series=[40, 36, 32, 28, 24, 20, 16, 12])
    assert "rsi_bullish_divergence" in diverging.signals
    assert "rsi_bullish_divergence" not in confirming.signals


def test_failed_breakdown_needs_a_probe_below_that_closes_back_above():
    bars = _flat(10)
    lo = min(c.l for c in bars)
    bars.append(Candle("11:30", lo - 0.1, lo + 0.5, lo - 2.0, lo + 0.6, 3000))
    assert "failed_breakdown" in analyse_bearish(bars).signals


def test_absorption_is_heavy_volume_making_a_new_low_but_closing_strong():
    bars = _selloff()
    lo = min(c.l for c in bars)
    bars.append(Candle("11:15", lo - 0.5, lo + 3.0, lo - 1.0, lo + 2.5, 9000))
    assert "absorption" in analyse_bearish(bars, rvol=2.0).signals
    assert "absorption" not in analyse_bearish(bars, rvol=1.0).signals


def test_the_two_sides_are_genuine_mirrors_not_the_same_signal_twice():
    """A rally should light the bullish side, a selloff the bearish side — not both."""
    kw = dict(rvol=2.5, atr=1.0)
    up = _rally()
    down = _selloff()
    assert analyse_bullish(up, rsi_series=[85, 86, 84, 82, 80, 78, 76, 74],
                           ema20=up[-1].c - 8, **kw).score > \
        analyse_bearish(up, rsi_series=[85, 86, 84, 82, 80, 78, 76, 74],
                        ema20=up[-1].c - 8, **kw).score
    assert analyse_bearish(down, rsi_series=[15, 14, 16, 18, 20, 22, 24, 26],
                           ema20=down[-1].c + 8, **kw).score > \
        analyse_bullish(down, rsi_series=[15, 14, 16, 18, 20, 22, 24, 26],
                        ema20=down[-1].c + 8, **kw).score


# ---- confluence -------------------------------------------------------------------------------
def test_reversal_probability_is_damped_when_only_one_family_fires():
    """'Avoid premature entries based on a single indicator', enforced rather than hoped for."""
    from exhaustion_engine import _confluence_damp
    assert _confluence_damp(0) == 0.0
    assert _confluence_damp(1) < _confluence_damp(2) < _confluence_damp(3) < _confluence_damp(4)
    assert _confluence_damp(4) == 1.0
    assert _confluence_damp(1) <= 0.4          # one family is one indicator in disguise


def test_score_and_reversal_probability_are_deliberately_different_numbers():
    bars = _rally()
    r = analyse_bullish(bars, rsi_series=[85, 84, 83, 82, 81, 80, 79, 78])   # momentum only-ish
    assert r.reversal_probability <= r.score


def test_high_conviction_needs_independent_confluence_not_just_a_big_score():
    from exhaustion_engine import SideRead, _classify
    terminal = lambda fams: SideRead(90.0, "high_probability_reversal", 90.0, [], [], fams, {})
    quiet = SideRead(5.0, "fresh_trend", 5.0, [], [], [], {})
    one = _classify("up", 60, terminal(["momentum"]), quiet, "bearish_reversal", 90.0, 50.0)
    many = _classify("up", 60, terminal(["momentum", "volume", "extension"]), quiet,
                     "bearish_reversal", 90.0, 80.0)
    assert one == "early_short_reversal_candidate"
    assert many == "high_conviction_short_reversal"
    assert MIN_FAMILIES_FOR_CONVICTION == 3


def test_confidence_reflects_confluence_quality_and_how_much_was_checkable():
    thin = analyse(_rally())                                    # almost nothing evaluable
    rich = analyse(_rally(), rsi_series=[85, 86, 84, 82, 80, 78, 76, 74], macd_hist=[2, 1, .5, .1],
                   vwap=90.0, ema20=95.0, ema50=90.0, ema200=80.0, atr=1.0, bb_percent_b=0.98,
                   rvol=2.5, stock_change_pct=0.2, index_change_pct=1.4, sector_change_pct=-0.5,
                   flow_estimate="Likely Distribution", bars_since_day_low=10,
                   bars_since_day_high=1, adx=30, plus_di=28, minus_di=12)
    assert rich["summary"]["confidence"] > thin["summary"]["confidence"]


# ---- the summary ------------------------------------------------------------------------------
def test_summary_carries_every_field_the_spec_asks_for():
    out = analyse(_rally(), rsi_series=[85, 84, 83, 82, 81, 80, 79, 78], rvol=2.5, ema20=95.0,
                  atr=1.0, adx=28, plus_di=25, minus_di=14)["summary"]
    for k in ("current_trend", "trend_strength", "bullish_exhaustion_score",
              "bearish_exhaustion_score", "continuation_probability", "reversal_probability",
              "expected_direction", "opportunity"):
        assert k in out, f"missing {k}"
    assert out["expected_direction"] in EXPECTED_DIRECTIONS
    assert out["opportunity"] in OPPORTUNITIES
    assert abs(out["continuation_probability"] + out["reversal_probability"] - 100.0) < 0.15


def test_the_exhausting_side_is_the_one_the_trend_is_running_on():
    """A stock in an uptrend is judged on BULLISH exhaustion; the bearish score is irrelevant to
    its reversal probability."""
    from exhaustion_engine import SideRead
    hot_bull = SideRead(80.0, "high_probability_reversal", 80.0, [], [], FAMILY[:4], {})
    cold_bear = SideRead(2.0, "fresh_trend", 2.0, [], [], [], {})
    up = summarise("up", 70.0, hot_bull, cold_bear)
    assert up["reversal_probability"] == 80.0 and up["expected_direction"] == "bearish_reversal"
    down = summarise("down", 70.0, hot_bull, cold_bear)
    assert down["reversal_probability"] == 2.0 and down["expected_direction"] == "continuation_down"


def test_a_sideways_tape_is_damped_because_it_is_where_premature_calls_are_born():
    from exhaustion_engine import SideRead
    hot = SideRead(80.0, "high_probability_reversal", 80.0, [], [], FAMILY[:4], {})
    cold = SideRead(2.0, "fresh_trend", 2.0, [], [], [], {})
    assert summarise("sideways", 10.0, hot, cold)["reversal_probability"] < \
        summarise("up", 70.0, hot, cold)["reversal_probability"]


def test_a_strong_clean_uptrend_reads_as_long_continuation_not_a_short():
    out = analyse(_rally(6, 100.0, 0.4), rsi_series=[55, 57, 58, 60, 61, 62, 63, 64],
                  ema20=98.0, ema50=96.0, ema200=90.0, adx=32, plus_di=30, minus_di=12,
                  rvol=1.1, atr=1.0, bb_percent_b=0.6)["summary"]
    assert out["current_trend"] == "up"
    assert out["opportunity"] != "high_conviction_short_reversal"


def test_the_long_opportunity_the_old_radar_could_never_surface():
    """A washed-out downtrend with confluence — this is the whole point of the bearish half."""
    bars = _selloff()
    out = analyse(bars, rsi_series=[15, 14, 16, 18, 20, 22, 24, 26], rvol=2.6,
                  vwap=bars[-1].c * 1.1, ema20=bars[-1].c + 8, ema50=bars[-1].c + 12,
                  ema200=bars[-1].c + 25, atr=1.0, bb_percent_b=0.01,
                  stock_change_pct=-0.1, index_change_pct=-2.0, sector_change_pct=1.0,
                  flow_estimate="Likely Accumulation", bars_since_day_high=11,
                  bars_since_day_low=1, adx=30, plus_di=12, minus_di=28)["summary"]
    assert out["current_trend"] == "down"
    assert out["expected_direction"] == "bullish_reversal"
    assert out["opportunity"] in ("early_long_reversal_candidate", "high_conviction_long_reversal")


# ---- unknown handling -------------------------------------------------------------------------
def test_unknown_is_reported_and_counts_for_neither_side():
    out = analyse(_rally())                    # no rsi/macd/vwap/atr/bb/rvol/flow
    assert out["bullish_exhaustion"]["unknown_signals"]
    assert out["bearish_exhaustion"]["unknown_signals"]
    # a thin feed must not read as a confident anything
    assert out["summary"]["confidence"] < 60


def test_too_few_bars_returns_a_no_trade_with_an_explicit_unknown():
    out = analyse(_flat(3))
    assert out["summary"]["opportunity"] == "no_trade"
    assert out["summary"]["expected_direction"] == "no_clear_edge"
    assert "insufficient_bars" in out["bullish_exhaustion"]["unknown_signals"]


def test_a_sector_strength_string_stays_unknown_rather_than_becoming_zero():
    """The v2 payload usually carries 'est. — WebSearch sector index' here. Coercing that to 0.0
    would silently fire (or silently suppress) the sector signals on made-up data."""
    from exhaustion_engine import _sector_pct
    assert _sector_pct({"sector": {"strength": "est. — WebSearch sector index (Layer 4)"}}) is None
    assert _sector_pct({"sector": {"strength": -1.2}}) == -1.2
    assert _sector_pct({}) is None


# ---- payload wiring ---------------------------------------------------------------------------
def test_it_reads_a_real_v2_payload_shape():
    payload = {
        "recent_bars": [{"t": f"09:{i:02d}", "o": 100 + i, "h": 101 + i, "l": 99 + i,
                         "c": 100.5 + i, "v": 1000} for i in range(12)],
        "indicators": {"rsi_series": [70] * 10, "macd_hist_series": [1.0] * 10,
                       "atr14_intraday": 1.5},
        "vwap": {"vwap": 105.0},
        "institutional": {"ema20": 104.0, "ema50": 100.0, "ema200": 95.0,
                          "bollinger": {"percent_b": 0.9},
                          "adx": {"adx": 30.0, "plus_di": 28.0, "minus_di": 12.0}},
        "volume": {"rvol_vs_prior_days": 1.8},
        "intraday_structure": {"bars_since_day_low": 9, "bars_since_day_high": 1},
        "institutional_desk": {"relative_strength": {"stock_day_pct": 1.2, "nifty_day_pct": 0.3},
                               "sector": {"strength": "est."},
                               "institutional_flow_est": {"estimate": "Likely Accumulation"}},
    }
    out = from_indicator_json(payload)
    assert out["summary"]["current_trend"] == "up"
    assert "insufficient_bars" not in out["bullish_exhaustion"]["unknown_signals"]
    # the numeric fields were actually found, not silently defaulted to unknown
    assert "rsi_last" in out["bullish_exhaustion"]["evidence"]
    assert "dist_ema20_pct" in out["bullish_exhaustion"]["evidence"]
    assert "adx" in out["summary"]["trend_evidence"]


def test_an_empty_payload_does_not_explode():
    out = from_indicator_json({})
    assert out["summary"]["opportunity"] == "no_trade"


def test_analysis_is_deterministic():
    kw = dict(rsi_series=[80] * 8, rvol=2.1, ema20=95.0, atr=1.0, adx=25, plus_di=22, minus_di=14)
    assert analyse(_rally(), **kw) == analyse(_rally(), **kw)


def test_the_stage_ladder_is_monotonic_and_climbs_with_exhaustion():
    """The reversed zip in `_stage_for` is easy to get backwards — a fresh trend and a terminal
    one would swap silently, and every downstream classification with them."""
    from exhaustion_engine import _stage_for
    for stages in (BULL_STAGES, BEAR_STAGES):
        seen = [_stage_for(s, stages) for s in range(0, 101)]
        idx = [stages.index(s) for s in seen]
        assert idx == sorted(idx), f"{stages} ladder is not monotonic"
        assert seen[0] == "fresh_trend" and seen[100] == "high_probability_reversal"
        assert set(seen) == set(stages), "a stage is unreachable"


def test_direction_is_normalised_against_the_voters_that_were_available():
    """A fixed vote threshold made every reading 'sideways' whenever ADX and ema50 were missing —
    early in a session, always. That silently disabled the summary layer for 10,948 consecutive
    readings in the 2026-08-01 study before anyone noticed."""
    bars = _rally(8, 100.0, 1.0)
    # only the recent-slope voter is available: it must still be able to reach a verdict
    trend, _, ev = read_trend(bars)
    assert ev["votes_possible"] == 0.5
    assert trend == "up", f"a one-voter reading collapsed to sideways: {ev}"

    down, _, _ = read_trend(_selloff(8, 130.0, 1.0))
    assert down == "down"


def test_conflicting_voters_still_read_sideways():
    """Normalising must not turn thin evidence into false conviction — genuine disagreement is
    still no trend."""
    bars = _rally(8, 100.0, 1.0)                       # slope votes up
    trend, _, ev = read_trend(bars, ema20=bars[-1].c + 5, ema50=bars[-1].c + 8,
                              plus_di=12, minus_di=28)  # everything else votes down
    assert trend != "up"


def test_the_summary_layer_actually_reaches_a_verdict_on_a_normal_tape():
    """The regression test for the silent-sideways bug: a plain trending day must classify."""
    out = analyse(_rally(12, 100.0, 0.8), rsi_series=[55, 57, 59, 60, 61, 62, 63, 64],
                  ema20=100.0, atr=1.0)["summary"]
    assert out["current_trend"] != "sideways"
    assert out["expected_direction"] != "no_clear_edge"


def test_every_stage_is_actually_reachable_on_real_data():
    """The first cut set made the top two stages unreachable — high_probability_reversal fired 2
    times in 50,070 readings, so every high_conviction_* classification was dead code. Cuts are
    now calibrated to the measured score distribution."""
    from exhaustion_engine import STAGE_CUTS
    assert STAGE_CUTS == tuple(sorted(STAGE_CUTS, reverse=True)), "cuts must descend"
    assert STAGE_CUTS[0] <= 50, "terminal stage sits above the observed p99 (~49) — unreachable"
    assert len(STAGE_CUTS) == len(BULL_STAGES) - 1
