"""Early Reversal Prediction Engine — stages, signals, and the rules it must never break."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reversal_radar import STAGES, WEIGHTS, Candle, analyse


def _bars(n=12, start=100.0, step=0.0, up=True):
    """A quiet series; `step` drifts it."""
    out = []
    for i in range(n):
        p = start + step * i
        o = p - (0.2 if up else -0.2)
        out.append(Candle(f"{9 + i // 4:02d}:{(i * 15) % 60:02d}", o, p + 0.3, p - 0.3, p, 1000))
    return out


def _parabolic(n=12, start=100.0, step=1.5):
    """A steep run with the last candle printing a big upper shadow and closing weak."""
    out = _bars(n - 1, start, step)
    top = start + step * (n - 1)
    out.append(Candle("11:00", top - 0.2, top + 2.5, top - 0.4, top - 0.1, 5000))
    return out


# ---- contract -------------------------------------------------------------------------------
def test_it_never_returns_a_trading_decision():
    """Its whole safety rests on this: an estimate, never an instruction."""
    out = analyse(_parabolic()).as_dict()
    for forbidden in ("action", "decision", "side", "entry", "stop", "target", "recommendation"):
        assert forbidden not in out, f"radar leaked a decision field: {forbidden}"
    assert "reversal_probability" in out and "reversal_stage" in out
    assert "never" in out["note"].lower()


def test_all_five_stages_exist_and_are_ordered():
    assert STAGES == ("healthy_trend", "late_trend", "early_exhaustion", "distribution",
                      "high_probability_reversal")


def test_probability_is_bounded_and_stage_matches_it():
    for bars in (_bars(), _parabolic(), _bars(step=-1.0)):
        r = analyse(bars, rsi_series=[70] * 8, macd_hist=[1] * 8, vwap=100, ema20=100,
                    ema50=100, atr=1.0, bb_percent_b=0.5, rvol=1.0)
        assert 0 <= r.probability <= 100
        assert r.stage in STAGES


# ---- the VWAP rule --------------------------------------------------------------------------
def test_the_binary_above_below_vwap_gate_is_ignored_but_distance_is_used():
    """The spec's point: the above/below flag is the lagging gate this engine pre-empts. Extension
    away from VWAP is still evidence."""
    bars = _parabolic()
    near = analyse(bars, vwap=bars[-1].c * 0.999, ema20=bars[-1].c, ema50=bars[-1].c,
                   atr=1.0, bb_percent_b=0.5, rvol=1.0)
    far = analyse(bars, vwap=bars[-1].c * 0.95, ema20=bars[-1].c, ema50=bars[-1].c,
                  atr=1.0, bb_percent_b=0.5, rvol=1.0)
    assert "far_from_vwap" not in near.signals
    assert "far_from_vwap" in far.signals
    assert far.probability > near.probability


# ---- individual signals ---------------------------------------------------------------------
def test_rsi_divergence_fires_on_a_higher_high_with_weaker_momentum():
    bars = _parabolic()                                   # price at its high
    rsi = [80, 82, 84, 83, 81, 79, 77, 75]                # momentum rolling over
    r = analyse(bars, rsi_series=rsi)
    assert "rsi_divergence" in r.signals
    assert "new_high_weak_momentum" in r.signals
    assert "momentum_slowing" in r.signals


def test_no_divergence_when_momentum_confirms_the_high():
    bars = _parabolic()
    r = analyse(bars, rsi_series=[60, 63, 66, 69, 72, 75, 78, 81])   # rising into the high
    assert "rsi_divergence" not in r.signals


def test_failed_new_high_and_resistance_rejection_fire_together():
    bars = _bars(10, 100.0, 0.5)
    prior_high = max(c.h for c in bars[-8:-1] + bars[:1])
    bars.append(Candle("11:30", prior_high + 0.1, prior_high + 2.0, prior_high - 0.5,
                       prior_high - 0.6, 3000))          # probed above, closed back under
    r = analyse(bars)
    assert "failed_new_high" in r.signals and "resistance_rejection" in r.signals


def test_climax_needs_volume_and_a_weak_close():
    bars = _parabolic()
    hot = analyse(bars, rvol=2.5)
    cool = analyse(bars, rvol=1.0)
    assert "volume_climax" in hot.signals and "buying_climax" in hot.signals
    assert "volume_climax" not in cool.signals


def test_atr_overextension_and_bollinger_exhaustion():
    bars = _parabolic()
    r = analyse(bars, ema20=bars[-1].c - 5.0, atr=1.0, bb_percent_b=0.97)
    assert "atr_overextended" in r.signals and "bollinger_exhaustion" in r.signals


def test_premium_position_in_the_day_range():
    assert "premium_in_day_range" in analyse(_parabolic()).signals
    assert "premium_in_day_range" not in analyse(_bars(12, 100.0, -0.5)).signals


def test_relative_weakness_when_the_stock_lags_the_index():
    lagging = analyse(_bars(), stock_change_pct=0.2, index_change_pct=1.4)
    leading = analyse(_bars(), stock_change_pct=1.8, index_change_pct=0.3)
    assert "relative_weakness" in lagging.signals
    assert "relative_weakness" not in leading.signals


def test_sector_weakening_while_the_stock_holds_up():
    r = analyse(_bars(), sector_change_pct=-0.8, stock_change_pct=1.2)
    assert "sector_weakening" in r.signals


# ---- staging --------------------------------------------------------------------------------
def test_a_quiet_trend_reads_healthy_and_a_parabolic_top_reads_exhausted():
    quiet = analyse(_bars(), rsi_series=[55] * 8, macd_hist=[0.1] * 8, vwap=100.0, ema20=100.0,
                    ema50=100.0, atr=1.0, bb_percent_b=0.5, rvol=1.0,
                    stock_change_pct=0.4, index_change_pct=0.3, sector_change_pct=0.3)
    bars = _parabolic()
    hot = analyse(bars, rsi_series=[85, 86, 84, 82, 80, 78, 76, 74], macd_hist=[2, 2.2, 1.8, 1.4, 1.0, .6, .3, .1],
                  vwap=bars[-1].c * 0.94, ema20=bars[-1].c - 6, ema50=bars[-1].c - 10,
                  atr=1.0, bb_percent_b=0.98, rvol=2.6,
                  stock_change_pct=3.0, index_change_pct=0.2, sector_change_pct=-0.5)
    assert quiet.probability < hot.probability
    assert quiet.stage in ("healthy_trend", "late_trend")
    assert hot.stage in ("distribution", "high_probability_reversal")


# ---- unknown handling -----------------------------------------------------------------------
def test_unavailable_signals_are_reported_and_do_not_count_against_the_trend():
    """Absent evidence is not bearish evidence — the rule the committee got wrong on 2026-08-01.
    A thin feed must not read as a healthy trend either, so scoring normalises to what was
    actually checkable."""
    thin = analyse(_parabolic())                       # no rsi/macd/vwap/atr/bb/rvol/index
    assert thin.unknown, "unevaluable signals must be reported"
    assert "rsi_divergence" in thin.unknown
    # the extension signals it COULD check still register
    assert thin.probability > 0


def test_too_few_bars_returns_healthy_with_an_explicit_unknown():
    r = analyse(_bars(3))
    assert r.stage == "healthy_trend" and r.probability == 0.0
    assert "insufficient_bars" in r.unknown


def test_every_weight_maps_to_a_known_signal_name():
    r = analyse(_parabolic(), rsi_series=[85, 84, 83, 82, 81, 80, 79, 78], rvol=2.5,
                ema20=90.0, atr=1.0, bb_percent_b=0.99, vwap=90.0, ema50=85.0,
                stock_change_pct=1.0, index_change_pct=2.0, sector_change_pct=-1.0)
    for s in r.signals:
        assert s in WEIGHTS, f"fired an unweighted signal: {s}"


def test_analysis_is_deterministic():
    bars = _parabolic()
    a = analyse(bars, rsi_series=[80] * 8, rvol=2.1, ema20=95.0, atr=1.0)
    b = analyse(bars, rsi_series=[80] * 8, rvol=2.1, ema20=95.0, atr=1.0)
    assert a.as_dict() == b.as_dict()
