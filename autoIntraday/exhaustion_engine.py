"""Bidirectional Trend Exhaustion Engine — is THIS trend running out, whichever way it points?

Not a direction finder and not a decision maker. It answers one question twice, independently:

  1. Are BUYERS running out of conviction?   (bullish exhaustion -> potential SHORT)
  2. Are SELLERS running out of conviction?  (bearish exhaustion -> potential LONG)

**Why both sides.** `reversal_radar.py` only ever measured the first. Every signal in it — buying
climax, upper shadows, failed new high, bearish divergence — reads an exhausting UPtrend, so the
system could only ever suppress a long. It was structurally blind to the washed-out downtrend
about to bounce. That blindness is the gap this closes.

**The rule that shapes everything here:** a stock making new highs is NOT thereby bullish, and a
stock making new lows is NOT thereby bearish. Price extremity is evidence about EXHAUSTION, not
about direction. `current_trend` is therefore read from structure (EMA stack, DI spread, slope) —
never from "it printed a new high".

**Confluence, not signal-counting.** Signals are grouped into six independent FAMILIES (momentum,
extension, price action, volume, relative strength, smart money). Three RSI-flavoured signals are
one piece of evidence, not three, so confidence scales with how many DISTINCT families agree.
A high score from a single family is explicitly untrustworthy and cannot reach high conviction.

**Unknown is not negative.** Anything unevaluable is reported and contributes nothing to either
side. Scores normalise against the weight that was actually checkable, so a thin feed does not
read as a healthy trend just because nothing could be tested.

Deterministic and pure — same bars, same reading. It runs per candidate per cycle, so it must
stay fast, and it must be backtestable in a way an LLM pass is not.

NEVER returns BUY or SELL. It returns evidence for the decision engine, which is a separate thing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from reversal_radar import Candle, _body_frac, _slope, _upper_shadow, st_mean

# --- stages -----------------------------------------------------------------------------------
# Ordered youngest to most exhausted. The two ladders differ by one name on purpose: an ageing
# uptrend DISTRIBUTES (supply handed to late buyers), an ageing downtrend ACCUMULATES (supply
# absorbed from late sellers). Same mechanic, opposite side of the book.
BULL_STAGES = ("fresh_trend", "healthy_trend", "mature_trend", "early_exhaustion",
               "distribution", "high_probability_reversal")
BEAR_STAGES = ("fresh_trend", "healthy_trend", "mature_trend", "early_exhaustion",
               "accumulation", "high_probability_reversal")

# score -> stage, MEASURED not guessed. Calibrated 2026-08-01 against 61,018 point-in-time
# readings (34 symbols, 23 sessions, at both 5m and 15m) so each stage is actually reachable.
#
# The first cut set (72/58/42/28/14) was intuition and it was badly wrong: because the score
# normalises fired weight over ~24 checkable signals, even a thoroughly exhausted tape lands
# near 40. `high_probability_reversal` fired 2 times in 50,070 readings and `distribution` 65 —
# so the terminal stages, and every `high_conviction_*` classification that depends on them,
# were unreachable. The score distribution was near-identical at both timeframes
# (p50 ~15, p75 ~22, p90 ~30, p95 ~36, p98 ~44, p99 ~49), which is what makes these cuts safe
# to share across both.
STAGE_CUTS = (46.0,    # ~p98  high_probability_reversal
              36.0,    # ~p95  distribution / accumulation
              28.0,    # ~p89  early_exhaustion
              22.0,    # ~p75  mature_trend
              15.0)    # ~p50  healthy_trend; below this, fresh_trend

# --- signal families ---------------------------------------------------------------------------
# Confluence is measured across these, never within one. This is the difference between "six
# signals fired" and "six independent things agree".
FAMILY = "momentum extension price_action volume relative smart_money".split()

BULL_WEIGHTS: dict[str, tuple[float, str]] = {
    # momentum
    "rsi_overbought":                 (7.0, "momentum"),
    "rsi_bearish_divergence":        (12.0, "momentum"),
    "macd_weakening":                (10.0, "momentum"),
    "momentum_slowdown":              (8.0, "momentum"),
    # extension
    "far_above_vwap":                 (6.0, "extension"),
    "far_above_ema20":                (6.0, "extension"),
    "far_above_ema50":                (5.0, "extension"),
    "far_above_ema200":               (4.0, "extension"),
    "atr_extension_up":               (8.0, "extension"),
    "parabolic_move_up":             (10.0, "extension"),
    "bollinger_exhaustion_up":        (7.0, "extension"),
    # price action
    "consecutive_strong_bull_candles": (7.0, "price_action"),
    "large_upper_shadows":            (8.0, "price_action"),
    "failed_breakout":               (10.0, "price_action"),
    "higher_high_weaker_momentum":   (12.0, "price_action"),
    "multiple_failed_extensions":     (9.0, "price_action"),
    "premium_in_day_range":           (5.0, "price_action"),
    # volume
    "buying_climax":                  (9.0, "volume"),
    "volume_climax":                  (8.0, "volume"),
    "churn_no_progress":              (9.0, "volume"),
    # relative
    "sector_weakening":               (7.0, "relative"),
    "relative_weakness_near_highs":   (9.0, "relative"),
    # smart money
    "smart_money_distribution":      (11.0, "smart_money"),
    "trend_maturity":                 (6.0, "smart_money"),
}

BEAR_WEIGHTS: dict[str, tuple[float, str]] = {
    # momentum
    "rsi_oversold":                   (7.0, "momentum"),
    "rsi_bullish_divergence":        (12.0, "momentum"),
    "macd_strengthening":            (10.0, "momentum"),
    "momentum_improving":             (8.0, "momentum"),
    # extension
    "far_below_vwap":                 (6.0, "extension"),
    "far_below_ema20":                (6.0, "extension"),
    "far_below_ema50":                (5.0, "extension"),
    "far_below_ema200":               (4.0, "extension"),
    "atr_extension_down":             (8.0, "extension"),
    "capitulation_move_down":        (10.0, "extension"),
    "bollinger_exhaustion_down":      (7.0, "extension"),
    # price action
    "consecutive_bear_candles":       (7.0, "price_action"),
    "large_lower_shadows":            (8.0, "price_action"),
    "failed_breakdown":              (10.0, "price_action"),
    "lower_low_improving_momentum":  (12.0, "price_action"),
    "multiple_failed_breakdowns":     (9.0, "price_action"),
    "discount_in_day_range":          (5.0, "price_action"),
    # volume
    "selling_climax":                 (9.0, "volume"),
    "capitulation_volume":            (8.0, "volume"),
    "absorption":                     (9.0, "volume"),
    # relative
    "sector_strengthening":           (7.0, "relative"),
    "relative_strength_near_lows":    (9.0, "relative"),
    # smart money
    "smart_money_accumulation":      (11.0, "smart_money"),
    "trend_maturity":                 (6.0, "smart_money"),
}

# A reversal read from one family alone is a single indicator wearing a disguise. Below this many
# distinct families, the reversal probability is damped hard and high conviction is unreachable.
MIN_FAMILIES_FOR_CONVICTION = 3
MIN_BARS = 6

EXPECTED_DIRECTIONS = ("continuation_up", "continuation_down", "bullish_reversal",
                       "bearish_reversal", "no_clear_edge")
OPPORTUNITIES = ("strong_long_continuation", "strong_short_continuation",
                 "early_long_reversal_candidate", "high_conviction_long_reversal",
                 "early_short_reversal_candidate", "high_conviction_short_reversal", "no_trade")


@dataclass
class SideRead:
    """One side's exhaustion picture. `score` is how exhausted; `reversal_probability` is what
    that is worth after confluence is taken into account — they are deliberately different."""
    score: float
    stage: str
    reversal_probability: float
    signals: list = field(default_factory=list)
    unknown: list = field(default_factory=list)
    families: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 1), "stage": self.stage,
                "reversal_probability": round(self.reversal_probability, 1),
                "signals": self.signals, "unknown_signals": self.unknown,
                "families_fired": self.families, "family_count": len(self.families),
                "evidence": self.evidence}


def _stage_for(score: float, stages: Sequence[str]) -> str:
    for cut, name in zip(STAGE_CUTS, stages[::-1]):
        if score >= cut:
            return name
    return stages[0]


def _confluence_damp(families: int) -> float:
    """How much of the raw score survives as reversal probability.

    One family is one indicator — the premature-entry trap the spec calls out. Full weight needs
    genuinely independent agreement.
    """
    return {0: 0.0, 1: 0.35, 2: 0.65, 3: 0.85}.get(families, 1.0)


def _score(fired: list[tuple[float, str, str]], unknown: list[str],
           weights: dict[str, tuple[float, str]]) -> tuple[float, list[str]]:
    raw = sum(w for w, _, _ in fired)
    checkable = sum(w for n, (w, _) in weights.items() if n not in unknown)
    score = min(100.0, raw / checkable * 100.0) if checkable else 0.0
    fams = sorted({f for _, _, f in fired}, key=FAMILY.index)
    return score, fams


# --- bullish exhaustion (potential SHORT) -----------------------------------------------------
def analyse_bullish(candles: Sequence[Candle], *, rsi_series=None, macd_hist=None, vwap=None,
                    ema20=None, ema50=None, ema200=None, atr=None, bb_percent_b=None, rvol=None,
                    sector_change_pct=None, index_change_pct=None, stock_change_pct=None,
                    flow_estimate: Optional[str] = None,
                    bars_since_day_low: Optional[int] = None) -> SideRead:
    """Are BUYERS running out? High score = an uptrend near its end, whatever price is doing."""
    if len(candles) < MIN_BARS:
        return SideRead(0.0, BULL_STAGES[0], 0.0, [], ["insufficient_bars"], [],
                        {"bars": len(candles)})

    fired: list[tuple[float, str, str]] = []
    unknown: list[str] = []
    ev: dict[str, Any] = {}
    last, recent = candles[-1], candles[-6:]
    highs, lows, px = [c.h for c in candles], [c.l for c in candles], candles[-1].c

    def fire(name: str) -> None:
        w, fam = BULL_WEIGHTS[name]
        fired.append((w, name, fam))

    # --- momentum ---------------------------------------------------------------------------
    rs = [r for r in (rsi_series or []) if r is not None]
    if len(rs) >= 4:
        ev["rsi_last"] = round(rs[-1], 1)
        if rs[-1] >= 70:
            fire("rsi_overbought")
        if (_slope(rs[-4:]) or 0) < 0:
            fire("momentum_slowdown")
        if len(rs) >= 8 and len(candles) >= 8 and highs[-1] >= max(highs[-8:]) \
                and rs[-1] < max(rs[-8:-1]):
            fire("rsi_bearish_divergence")
            fire("higher_high_weaker_momentum")
    else:
        unknown += ["rsi_overbought", "momentum_slowdown", "rsi_bearish_divergence",
                    "higher_high_weaker_momentum"]

    mh = [m for m in (macd_hist or []) if m is not None]
    if len(mh) >= 4:
        ev["macd_hist_last"] = round(mh[-1], 4)
        if (_slope(mh[-4:]) or 0) < 0 or (mh[-1] < max(mh[-8:-1] or [mh[-1]])):
            fire("macd_weakening")
    else:
        unknown.append("macd_weakening")

    # --- extension --------------------------------------------------------------------------
    for name, ref, thresh in (("far_above_vwap", vwap, 1.5), ("far_above_ema20", ema20, 2.0),
                              ("far_above_ema50", ema50, 3.5), ("far_above_ema200", ema200, 6.0)):
        if ref:
            dist = (px - ref) / ref * 100
            ev[name.replace("far_above_", "dist_") + "_pct"] = round(dist, 2)
            if dist >= thresh:
                fire(name)
        else:
            unknown.append(name)

    if atr and ema20:
        stretch = (px - ema20) / atr
        ev["atr_stretch"] = round(stretch, 2)
        if stretch >= 2.0:
            fire("atr_extension_up")
    else:
        unknown.append("atr_extension_up")

    if bb_percent_b is not None:
        ev["bb_percent_b"] = round(bb_percent_b, 3)
        if bb_percent_b >= 0.95:
            fire("bollinger_exhaustion_up")
    else:
        unknown.append("bollinger_exhaustion_up")

    # parabolic: each of the last three bars advancing further than the one before it
    if len(recent) >= 4:
        adv = [recent[i].c - recent[i - 1].c for i in range(-3, 0)]
        ev["advance_steps"] = [round(a, 2) for a in adv]
        if all(a > 0 for a in adv) and adv[-1] > adv[0] and adv[-1] > adv[-2]:
            fire("parabolic_move_up")

    # --- price action -----------------------------------------------------------------------
    strong_ups = sum(1 for c in recent[-3:] if c.c > c.o and _body_frac(c) >= 0.5)
    ev["consecutive_strong_up"] = strong_ups
    if strong_ups == 3:
        fire("consecutive_strong_bull_candles")

    shadows = [_upper_shadow(c) for c in recent[-3:]]
    ev["max_upper_shadow"] = round(max(shadows), 2)
    if max(shadows) >= 0.45:
        fire("large_upper_shadows")

    rejections = sum(1 for c in recent if _upper_shadow(c) >= 0.4 and _body_frac(c) <= 0.4)
    ev["rejection_candles"] = rejections
    if rejections >= 2:
        fire("multiple_failed_extensions")

    prior_high = max(highs[-8:-1]) if len(highs) >= 8 else max(highs[:-1])
    ev["prior_high"] = round(prior_high, 2)
    if last.h > prior_high and last.c < prior_high:
        fire("failed_breakout")

    day_hi, day_lo = max(highs), min(lows)
    if day_hi > day_lo:
        pos = (px - day_lo) / (day_hi - day_lo) * 100
        ev["position_in_day_range_pct"] = round(pos, 1)
        if pos >= 85:
            fire("premium_in_day_range")

    # --- volume -----------------------------------------------------------------------------
    if rvol is not None:
        ev["rvol"] = round(rvol, 2)
        if rvol >= 2.0:
            fire("volume_climax")
            if last.c > last.o and _upper_shadow(last) >= 0.35:
                fire("buying_climax")
    else:
        unknown += ["volume_climax", "buying_climax"]

    vols = [c.v for c in recent if c.v]
    if len(vols) >= 4:
        span = max(c.h for c in recent) - min(c.l for c in recent)
        progress = abs(recent[-1].c - recent[0].c)
        ev["progress_vs_range"] = round(progress / span, 2) if span else None
        if span and progress / span < 0.25 and vols[-1] > st_mean(vols[:-1]):
            fire("churn_no_progress")
    else:
        unknown.append("churn_no_progress")

    # --- relative ---------------------------------------------------------------------------
    if sector_change_pct is not None and stock_change_pct is not None:
        ev["sector_change_pct"] = sector_change_pct
        if sector_change_pct < 0 <= stock_change_pct:
            fire("sector_weakening")
    else:
        unknown.append("sector_weakening")

    if index_change_pct is not None and stock_change_pct is not None:
        ev["index_change_pct"] = index_change_pct
        near_highs = day_hi > day_lo and (px - day_lo) / (day_hi - day_lo) >= 0.75
        # The spec's signal: still parked near the highs while quietly losing the race.
        if stock_change_pct < index_change_pct and near_highs:
            fire("relative_weakness_near_highs")
    else:
        unknown.append("relative_weakness_near_highs")

    # --- smart money ------------------------------------------------------------------------
    if flow_estimate:
        ev["flow_estimate"] = flow_estimate
        if "distribut" in flow_estimate.lower():
            fire("smart_money_distribution")
    else:
        unknown.append("smart_money_distribution")

    if bars_since_day_low is not None and len(candles):
        maturity = bars_since_day_low / max(len(candles), 1)
        ev["trend_maturity"] = round(maturity, 2)
        if maturity >= 0.6:
            fire("trend_maturity")
    else:
        unknown.append("trend_maturity")

    score, fams = _score(fired, unknown, BULL_WEIGHTS)
    stage = _stage_for(score, BULL_STAGES)
    prob = score * _confluence_damp(len(fams))
    return SideRead(score, stage, prob, [n for _, n, _ in sorted(fired, reverse=True)],
                    unknown, fams, ev)


# --- bearish exhaustion (potential LONG) -------------------------------------------------------
def analyse_bearish(candles: Sequence[Candle], *, rsi_series=None, macd_hist=None, vwap=None,
                    ema20=None, ema50=None, ema200=None, atr=None, bb_percent_b=None, rvol=None,
                    sector_change_pct=None, index_change_pct=None, stock_change_pct=None,
                    flow_estimate: Optional[str] = None,
                    bars_since_day_high: Optional[int] = None) -> SideRead:
    """Are SELLERS running out? High score = a downtrend near its end — the LONG the old
    one-directional radar could never see."""
    if len(candles) < MIN_BARS:
        return SideRead(0.0, BEAR_STAGES[0], 0.0, [], ["insufficient_bars"], [],
                        {"bars": len(candles)})

    fired: list[tuple[float, str, str]] = []
    unknown: list[str] = []
    ev: dict[str, Any] = {}
    last, recent = candles[-1], candles[-6:]
    highs, lows, px = [c.h for c in candles], [c.l for c in candles], candles[-1].c

    def fire(name: str) -> None:
        w, fam = BEAR_WEIGHTS[name]
        fired.append((w, name, fam))

    def lower_shadow(c: Candle) -> float:
        rng = c.h - c.l
        return ((min(c.o, c.c) - c.l) / rng) if rng > 0 else 0.0

    # --- momentum ---------------------------------------------------------------------------
    rs = [r for r in (rsi_series or []) if r is not None]
    if len(rs) >= 4:
        ev["rsi_last"] = round(rs[-1], 1)
        if rs[-1] <= 30:
            fire("rsi_oversold")
        if (_slope(rs[-4:]) or 0) > 0:
            fire("momentum_improving")
        # bullish divergence: price makes a LOWER low while RSI makes a HIGHER low
        if len(rs) >= 8 and len(candles) >= 8 and lows[-1] <= min(lows[-8:]) \
                and rs[-1] > min(rs[-8:-1]):
            fire("rsi_bullish_divergence")
            fire("lower_low_improving_momentum")
    else:
        unknown += ["rsi_oversold", "momentum_improving", "rsi_bullish_divergence",
                    "lower_low_improving_momentum"]

    mh = [m for m in (macd_hist or []) if m is not None]
    if len(mh) >= 4:
        ev["macd_hist_last"] = round(mh[-1], 4)
        if (_slope(mh[-4:]) or 0) > 0 or (mh[-1] > min(mh[-8:-1] or [mh[-1]])):
            fire("macd_strengthening")
    else:
        unknown.append("macd_strengthening")

    # --- extension (below) ------------------------------------------------------------------
    for name, ref, thresh in (("far_below_vwap", vwap, 1.5), ("far_below_ema20", ema20, 2.0),
                              ("far_below_ema50", ema50, 3.5), ("far_below_ema200", ema200, 6.0)):
        if ref:
            dist = (ref - px) / ref * 100          # positive when price is BELOW the reference
            ev[name.replace("far_below_", "dist_below_") + "_pct"] = round(dist, 2)
            if dist >= thresh:
                fire(name)
        else:
            unknown.append(name)

    if atr and ema20:
        stretch = (ema20 - px) / atr
        ev["atr_stretch_down"] = round(stretch, 2)
        if stretch >= 2.0:
            fire("atr_extension_down")
    else:
        unknown.append("atr_extension_down")

    if bb_percent_b is not None:
        ev["bb_percent_b"] = round(bb_percent_b, 3)
        if bb_percent_b <= 0.05:
            fire("bollinger_exhaustion_down")
    else:
        unknown.append("bollinger_exhaustion_down")

    if len(recent) >= 4:
        drop = [recent[i].c - recent[i - 1].c for i in range(-3, 0)]
        ev["decline_steps"] = [round(d, 2) for d in drop]
        if all(d < 0 for d in drop) and drop[-1] < drop[0] and drop[-1] < drop[-2]:
            fire("capitulation_move_down")

    # --- price action -----------------------------------------------------------------------
    downs = sum(1 for c in recent[-3:] if c.c < c.o and _body_frac(c) >= 0.5)
    ev["consecutive_strong_down"] = downs
    if downs == 3:
        fire("consecutive_bear_candles")

    shadows = [lower_shadow(c) for c in recent[-3:]]
    ev["max_lower_shadow"] = round(max(shadows), 2)
    if max(shadows) >= 0.45:
        fire("large_lower_shadows")

    supports = sum(1 for c in recent if lower_shadow(c) >= 0.4 and _body_frac(c) <= 0.4)
    ev["support_candles"] = supports
    if supports >= 2:
        fire("multiple_failed_breakdowns")

    prior_low = min(lows[-8:-1]) if len(lows) >= 8 else min(lows[:-1])
    ev["prior_low"] = round(prior_low, 2)
    if last.l < prior_low and last.c > prior_low:
        fire("failed_breakdown")

    day_hi, day_lo = max(highs), min(lows)
    if day_hi > day_lo:
        pos = (px - day_lo) / (day_hi - day_lo) * 100
        ev["position_in_day_range_pct"] = round(pos, 1)
        if pos <= 15:
            fire("discount_in_day_range")

    # --- volume -----------------------------------------------------------------------------
    if rvol is not None:
        ev["rvol"] = round(rvol, 2)
        if rvol >= 2.0:
            fire("capitulation_volume")
            if last.c < last.o and lower_shadow(last) >= 0.35:
                fire("selling_climax")
        # absorption: heavy volume, a new low probed, yet the bar closes in its upper half —
        # someone is taking everything the sellers have.
        if rvol >= 1.5 and last.l <= min(lows[-6:]) and _upper_half(last):
            fire("absorption")
    else:
        unknown += ["capitulation_volume", "selling_climax", "absorption"]

    # --- relative ---------------------------------------------------------------------------
    if sector_change_pct is not None and stock_change_pct is not None:
        ev["sector_change_pct"] = sector_change_pct
        if sector_change_pct > 0 >= stock_change_pct:
            fire("sector_strengthening")
    else:
        unknown.append("sector_strengthening")

    if index_change_pct is not None and stock_change_pct is not None:
        ev["index_change_pct"] = index_change_pct
        near_lows = day_hi > day_lo and (px - day_lo) / (day_hi - day_lo) <= 0.25
        if stock_change_pct > index_change_pct and near_lows:
            fire("relative_strength_near_lows")
    else:
        unknown.append("relative_strength_near_lows")

    # --- smart money ------------------------------------------------------------------------
    if flow_estimate:
        ev["flow_estimate"] = flow_estimate
        if "accumulat" in flow_estimate.lower():
            fire("smart_money_accumulation")
    else:
        unknown.append("smart_money_accumulation")

    if bars_since_day_high is not None and len(candles):
        maturity = bars_since_day_high / max(len(candles), 1)
        ev["trend_maturity"] = round(maturity, 2)
        if maturity >= 0.6:
            fire("trend_maturity")
    else:
        unknown.append("trend_maturity")

    score, fams = _score(fired, unknown, BEAR_WEIGHTS)
    stage = _stage_for(score, BEAR_STAGES)
    prob = score * _confluence_damp(len(fams))
    return SideRead(score, stage, prob, [n for _, n, _ in sorted(fired, reverse=True)],
                    unknown, fams, ev)


def _upper_half(c: Candle) -> bool:
    rng = c.h - c.l
    return rng > 0 and (c.c - c.l) / rng >= 0.5


# --- direction and trend strength --------------------------------------------------------------
def read_trend(candles: Sequence[Candle], *, ema20=None, ema50=None, ema200=None,
               adx=None, plus_di=None, minus_di=None) -> tuple[str, float, dict]:
    """Direction from STRUCTURE, never from price extremity.

    This is the spec's central rule made mechanical: a new high does not vote here. Only the EMA
    stack, the DI spread and the slope of the recent advance do. That is what stops a distributing
    stock at its highs from being labelled bullish simply because it is at its highs.
    """
    ev: dict[str, Any] = {}
    px = candles[-1].c
    votes = 0.0
    possible = 0.0        # the weight that was actually available to vote
    stack = [e for e in (ema20, ema50, ema200) if e]
    if len(stack) >= 2:
        above = sum(1 for e in stack if px > e)
        votes += (above - (len(stack) - above)) / len(stack)
        possible += 1.0
        ev["emas_above"] = f"{above}/{len(stack)}"
    if ema20 and ema50:
        votes += 0.5 if ema20 > ema50 else -0.5
        possible += 0.5
    if plus_di is not None and minus_di is not None:
        ev["di_spread"] = round(plus_di - minus_di, 1)
        votes += 0.5 if plus_di > minus_di else -0.5
        possible += 0.5
    first, lastc = candles[-6:][0].c, candles[-1].c
    ev["recent_slope_pct"] = round((lastc - first) / first * 100, 2) if first else 0.0
    votes += 0.5 if lastc > first else -0.5
    possible += 0.5

    # Normalise against what could actually vote. A fixed threshold meant that early in a session —
    # before ema50 has 30 bars, with no ADX — only the slope could vote, 0.5 could never reach
    # 0.75, and EVERY reading came back "sideways". That silently disabled the whole summary layer
    # for 10,948 straight readings in the 2026-08-01 study.
    ev["direction_votes"], ev["votes_possible"] = round(votes, 2), possible
    conviction = (votes / possible) if possible else 0.0
    ev["direction_conviction"] = round(conviction, 2)
    trend = "up" if conviction >= 0.5 else ("down" if conviction <= -0.5 else "sideways")

    # Strength: ADX is the honest measure; fall back to how cleanly the recent bars trend.
    if adx is not None:
        strength = max(0.0, min(100.0, (adx - 10) / 30 * 100))
        ev["adx"] = round(adx, 1)
    else:
        span = max(c.h for c in candles[-6:]) - min(c.l for c in candles[-6:])
        strength = min(100.0, abs(lastc - first) / span * 100) if span else 0.0
        ev["strength_basis"] = "bar structure (ADX unavailable)"
    return trend, round(strength, 1), ev


# --- summary ------------------------------------------------------------------------------------
def summarise(trend: str, strength: float, bull: SideRead, bear: SideRead) -> dict[str, Any]:
    """Fuse both sides into the decision engine's evidence packet. Still never a trade."""
    # The exhaustion that MATTERS is the one on the side the trend is actually running.
    if trend == "up":
        reversal = bull.reversal_probability
        rev_dir, cont_dir = "bearish_reversal", "continuation_up"
        families = len(bull.families)
    elif trend == "down":
        reversal = bear.reversal_probability
        rev_dir, cont_dir = "bullish_reversal", "continuation_down"
        families = len(bear.families)
    else:
        # No trend to exhaust. Whichever side is more exhausted still points somewhere, but a
        # sideways tape is where premature reversal calls are born, so it is damped further.
        if bull.reversal_probability >= bear.reversal_probability:
            reversal, rev_dir, families = bull.reversal_probability * 0.6, "bearish_reversal", \
                len(bull.families)
        else:
            reversal, rev_dir, families = bear.reversal_probability * 0.6, "bullish_reversal", \
                len(bear.families)
        cont_dir = "no_clear_edge"

    reversal = round(min(100.0, reversal), 1)
    continuation = round(100.0 - reversal, 1)
    expected = rev_dir if reversal >= 55 else (cont_dir if continuation >= 55 else "no_clear_edge")
    if trend == "sideways" and expected == "no_clear_edge":
        expected = "no_clear_edge"

    # Confidence is about the QUALITY of the confluence, not the size of the score: how many
    # independent families agree, and how much of the evidence we could actually check.
    checked = 1.0 - (len(bull.unknown) + len(bear.unknown)) / float(
        len(BULL_WEIGHTS) + len(BEAR_WEIGHTS))
    confidence = round(min(100.0, (min(families, 4) / 4 * 60) + max(0.0, checked) * 40), 1)

    return {"current_trend": trend, "trend_strength": strength,
            "bullish_exhaustion_score": round(bull.score, 1),
            "bearish_exhaustion_score": round(bear.score, 1),
            "continuation_probability": continuation, "reversal_probability": reversal,
            "expected_direction": expected,
            "opportunity": _classify(trend, strength, bull, bear, expected, reversal, confidence),
            "confluence_families": families, "confidence": confidence}


def _classify(trend: str, strength: float, bull: SideRead, bear: SideRead, expected: str,
              reversal: float, confidence: float) -> str:
    """Map the picture onto one of the seven opportunity labels.

    High conviction demands BOTH a terminal stage and independent confluence — the spec's "avoid
    premature entries based on a single indicator", enforced rather than hoped for.
    """
    if expected == "bearish_reversal":
        conviction = (bull.stage == "high_probability_reversal"
                      and len(bull.families) >= MIN_FAMILIES_FOR_CONVICTION and reversal >= 70)
        return "high_conviction_short_reversal" if conviction else \
            ("early_short_reversal_candidate" if bull.stage in
             ("early_exhaustion", "distribution", "high_probability_reversal") else "no_trade")
    if expected == "bullish_reversal":
        conviction = (bear.stage == "high_probability_reversal"
                      and len(bear.families) >= MIN_FAMILIES_FOR_CONVICTION and reversal >= 70)
        return "high_conviction_long_reversal" if conviction else \
            ("early_long_reversal_candidate" if bear.stage in
             ("early_exhaustion", "accumulation", "high_probability_reversal") else "no_trade")
    if expected == "continuation_up" and trend == "up" and strength >= 50 \
            and bull.stage in ("fresh_trend", "healthy_trend"):
        return "strong_long_continuation"
    if expected == "continuation_down" and trend == "down" and strength >= 50 \
            and bear.stage in ("fresh_trend", "healthy_trend"):
        return "strong_short_continuation"
    return "no_trade"


NOTE = ("EVIDENCE ONLY — this engine never issues BUY or SELL. It measures how close each side of "
        "the book is to exhaustion and hands that to the decision engine. New highs are not "
        "bullish evidence and new lows are not bearish evidence; both are evidence about "
        "exhaustion.")


def analyse(candles: Sequence[Candle], **kw) -> dict[str, Any]:
    """Full bidirectional reading. Keyword arguments are shared by both sides."""
    trend_kw = {k: kw.get(k) for k in ("ema20", "ema50", "ema200", "adx", "plus_di", "minus_di")}
    since_low = kw.pop("bars_since_day_low", None)
    since_high = kw.pop("bars_since_day_high", None)
    for k in ("adx", "plus_di", "minus_di"):
        kw.pop(k, None)
    if len(candles) < MIN_BARS:
        empty_b = SideRead(0.0, BULL_STAGES[0], 0.0, [], ["insufficient_bars"], [],
                           {"bars": len(candles)})
        empty_s = SideRead(0.0, BEAR_STAGES[0], 0.0, [], ["insufficient_bars"], [],
                           {"bars": len(candles)})
        return {"bullish_exhaustion": empty_b.as_dict(), "bearish_exhaustion": empty_s.as_dict(),
                "summary": {"current_trend": "sideways", "trend_strength": 0.0,
                            "bullish_exhaustion_score": 0.0, "bearish_exhaustion_score": 0.0,
                            "continuation_probability": 0.0, "reversal_probability": 0.0,
                            "expected_direction": "no_clear_edge", "opportunity": "no_trade",
                            "confluence_families": 0, "confidence": 0.0},
                "unknown": ["insufficient_bars"], "note": NOTE}

    bull = analyse_bullish(candles, bars_since_day_low=since_low, **kw)
    bear = analyse_bearish(candles, bars_since_day_high=since_high, **kw)
    trend, strength, tev = read_trend(candles, **trend_kw)
    out = summarise(trend, strength, bull, bear)
    out["trend_evidence"] = tev
    return {"bullish_exhaustion": bull.as_dict(), "bearish_exhaustion": bear.as_dict(),
            "summary": out, "note": NOTE}


def from_indicator_json(payload: dict) -> dict[str, Any]:
    """Read a v2 indicator payload (stock_analyze_intraday_2.py) directly.

    Field paths verified against real output — the v2 blocks are `indicators`, `institutional`,
    `intraday_structure` and `institutional_desk`. Guessing them would silently produce an
    all-unknown reading that scores 0 and looks like a fresh trend.
    """
    p = payload or {}
    ind, inst = p.get("indicators") or {}, p.get("institutional") or {}
    desk = p.get("institutional_desk") or {}
    struct = p.get("intraday_structure") or {}
    bars = [Candle(t=str(b.get("t", "")), o=float(b["o"]), h=float(b["h"]), l=float(b["l"]),
                   c=float(b["c"]), v=float(b.get("v") or 0))
            for b in (p.get("recent_bars") or [])
            if all(b.get(k) is not None for k in ("o", "h", "l", "c"))]
    rs = desk.get("relative_strength") or {}
    return analyse(
        bars,
        rsi_series=ind.get("rsi_series"),
        macd_hist=ind.get("macd_hist_series"),
        vwap=(p.get("vwap") or {}).get("vwap"),
        ema20=inst.get("ema20"), ema50=inst.get("ema50"), ema200=inst.get("ema200"),
        atr=ind.get("atr14_intraday"),
        bb_percent_b=(inst.get("bollinger") or {}).get("percent_b"),
        rvol=(p.get("volume") or {}).get("rvol_vs_prior_days"),
        adx=(inst.get("adx") or {}).get("adx"),
        plus_di=(inst.get("adx") or {}).get("plus_di"),
        minus_di=(inst.get("adx") or {}).get("minus_di"),
        stock_change_pct=rs.get("stock_day_pct"),
        index_change_pct=rs.get("nifty_day_pct"),
        sector_change_pct=_sector_pct(desk),
        flow_estimate=(desk.get("institutional_flow_est") or {}).get("estimate"),
        bars_since_day_low=struct.get("bars_since_day_low"),
        bars_since_day_high=struct.get("bars_since_day_high"),
    )


def _sector_pct(desk: dict) -> Optional[float]:
    """Sector strength is usually the string 'est. — WebSearch sector index', not a number. That
    is genuinely unknown and must stay unknown rather than being coerced to 0.0 (which would read
    as a flat sector and quietly fire, or fail to fire, the sector signals)."""
    raw = (desk.get("sector") or {}).get("strength")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
