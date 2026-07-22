#!/usr/bin/env python3
"""
StockAnalayze intraday CLASSIC — price-action / candlestick engine for the `intraday-analyst-v2` skill.

Where the v1 intraday tool is an institutional-indicator engine (VWAP/EMA/ADX/SuperTrend/multi-TF),
this v2 companion computes the CLASSIC technical-analysis / price-action toolkit taught in
Zerodha Varsity Module 2 (Technical Analysis) and Greg Capra's "Intra-Day Trading Techniques":

  • Candlestick patterns (single: Marubozu, Doji, Spinning Top, Hammer, Hanging Man, Shooting Star;
    multi: Engulfing, Harami, Piercing, Dark Cloud, Morning/Evening Star) — each with the Varsity
    stop-loss = the LOW (bullish) / HIGH (bearish) of the pattern.
  • Support & Resistance from recent swing highs/lows (+ prior-day pivots reused from v1).
  • Fibonacci retracements of the day's dominant swing (23.6/38.2/50/61.8/78.6).
  • Moving-average interpretation (SMA 20/40 slope + "price testing a rising/falling MA") and the
    Pristine Buy/Sell Setup (pullback-to-rising-MA reversal bar).
  • Price-action bar tags (narrow-range / wide-range / reversal bar) + volume confirmation (RVOL).
  • Higher-timeframe trend (Dow structure) reused from v1 for multiple-time-frame confirmation.

It FETCHES + COMPUTES and prints clean JSON. The trade decision (candlestick-at-level, retracement,
breakout-retest, Pristine setup) + the trade-summary (entry/stop/target/RR/holding) live in the skill.

Reuses fetch + shared helpers from stock_analyze_intraday.py so numbers stay consistent with v1.
Educational — NOT financial advice. Intraday is leveraged; square off by ~3:20 PM IST.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from statistics import mean
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock_analyze import OHLCVBar, sma, rsi_simple, ema_sequence, fetch_yahoo_chart  # noqa: E402
import stock_analyze_intraday as it  # noqa: E402
from stock_analyze_intraday import (  # noqa: E402
    _round, group_by_day, _time_of, session_vwap, opening_range, prior_day_levels,
    interval_to_minutes, session_progress, rvol, resolve_yahoo_symbol, fetch_intraday_yahoo,
    tf_trend, resample_bars,
)

LOG = logging.getLogger("stock_analyze_intraday_classic")


# --------------------------------------------------------------------------------------
# Candlestick anatomy + pattern detection (Zerodha Varsity Module 2)
# --------------------------------------------------------------------------------------
def _anatomy(b: OHLCVBar) -> Dict[str, float]:
    rng = b.high - b.low
    body = abs(b.close - b.open)
    upper = b.high - max(b.open, b.close)
    lower = min(b.open, b.close) - b.low
    return {"range": rng, "body": body, "upper": upper, "lower": lower,
            "bull": b.close > b.open, "midpoint": (b.open + b.close) / 2}


def single_candle(b: OHLCVBar, prior_trend: str) -> Optional[Dict[str, Any]]:
    """Classify a single candle. `prior_trend` (up/down/flat) sets bull/bear meaning for hammer vs
    hanging-man / shooting-star. Stop = low (bullish) or high (bearish) of the candle (Varsity rule)."""
    a = _anatomy(b)
    rng = a["range"] or 1e-9
    body_pct = a["body"] / rng
    up, lo = a["upper"], a["lower"]
    if body_pct >= 0.9:
        return {"pattern": "bullish_marubozu" if a["bull"] else "bearish_marubozu",
                "bias": "bullish" if a["bull"] else "bearish", "single": True}
    if body_pct <= 0.1:
        return {"pattern": "doji", "bias": "indecision", "single": True}
    # paper-umbrella family: long lower wick, small body near top, tiny upper wick
    if lo >= 2 * a["body"] and up <= a["body"] * 0.6:
        if prior_trend == "down":
            return {"pattern": "hammer", "bias": "bullish", "single": True}
        if prior_trend == "up":
            return {"pattern": "hanging_man", "bias": "bearish", "single": True}
        return {"pattern": "paper_umbrella", "bias": "reversal", "single": True}
    # shooting star: long upper wick, small body near bottom, in an uptrend
    if up >= 2 * a["body"] and lo <= a["body"] * 0.6:
        if prior_trend == "up":
            return {"pattern": "shooting_star", "bias": "bearish", "single": True}
        return {"pattern": "inverted_hammer", "bias": "bullish" if prior_trend == "down" else "reversal", "single": True}
    if body_pct <= 0.3 and up > a["body"] * 0.6 and lo > a["body"] * 0.6:
        return {"pattern": "spinning_top", "bias": "indecision", "single": True}
    return None


def multi_candle(prev: OHLCVBar, cur: OHLCVBar, prev2: Optional[OHLCVBar]) -> Optional[Dict[str, Any]]:
    """Two/three-candle patterns. Engulfing, Harami, Piercing, Dark Cloud, Morning/Evening Star."""
    pa, ca = _anatomy(prev), _anatomy(cur)
    p_bull, c_bull = pa["bull"], ca["bull"]
    p_top, p_bot = max(prev.open, prev.close), min(prev.open, prev.close)
    c_top, c_bot = max(cur.open, cur.close), min(cur.open, cur.close)
    # Engulfing
    if not p_bull and c_bull and c_bot <= p_bot and c_top >= p_top and ca["body"] > pa["body"]:
        return {"pattern": "bullish_engulfing", "bias": "bullish"}
    if p_bull and not c_bull and c_bot <= p_bot and c_top >= p_top and ca["body"] > pa["body"]:
        return {"pattern": "bearish_engulfing", "bias": "bearish"}
    # Harami (small body inside prior big body)
    if p_bull and not c_bull and c_top <= p_top and c_bot >= p_bot and pa["body"] > ca["body"] * 1.5:
        return {"pattern": "bearish_harami", "bias": "bearish"}
    if not p_bull and c_bull and c_top <= p_top and c_bot >= p_bot and pa["body"] > ca["body"] * 1.5:
        return {"pattern": "bullish_harami", "bias": "bullish"}
    # Piercing / Dark cloud (close beyond prior midpoint)
    if not p_bull and c_bull and cur.open < prev.close and cur.close > pa["midpoint"] and cur.close < prev.open:
        return {"pattern": "piercing", "bias": "bullish"}
    if p_bull and not c_bull and cur.open > prev.close and cur.close < pa["midpoint"] and cur.close > prev.open:
        return {"pattern": "dark_cloud_cover", "bias": "bearish"}
    # Morning / Evening star (3 candles)
    if prev2 is not None:
        p2a = _anatomy(prev2)
        star = pa["body"] <= p2a["body"] * 0.5  # small middle body
        if not p2a["bull"] and star and c_bull and cur.close > p2a["midpoint"]:
            return {"pattern": "morning_star", "bias": "bullish"}
        if p2a["bull"] and star and not c_bull and cur.close < p2a["midpoint"]:
            return {"pattern": "evening_star", "bias": "bearish"}
    return None


def bar_tags(bars: List[OHLCVBar]) -> Dict[str, Any]:
    """Pristine price-action tags for the last bar: narrow/wide-range, reversal bar."""
    if len(bars) < 6:
        return {}
    ranges = [b.high - b.low for b in bars[-11:]]
    avg_r = mean(ranges[:-1]) if len(ranges) > 1 else ranges[-1]
    last = bars[-1]
    lr = last.high - last.low
    tag = ("narrow_range" if avg_r and lr < 0.6 * avg_r else
           "wide_range" if avg_r and lr > 1.6 * avg_r else "normal")
    reds = sum(1 for b in bars[-4:-1] if b.close < b.open)
    greens = sum(1 for b in bars[-4:-1] if b.close > b.open)
    rev = ("bullish_reversal_bar" if reds >= 2 and last.close > last.open else
           "bearish_reversal_bar" if greens >= 2 and last.close < last.open else None)
    return {"range_tag": tag, "reversal_bar": rev}


# --------------------------------------------------------------------------------------
# Support/Resistance (swing), Fibonacci, Moving-average interpretation, Pristine setup
# --------------------------------------------------------------------------------------
def swing_levels(bars: List[OHLCVBar], left: int = 2, right: int = 2) -> Dict[str, Any]:
    """Fractal swing highs/lows over the given bars -> nearest support/resistance to last price."""
    highs, lows = [], []
    for i in range(left, len(bars) - right):
        wh = [b.high for b in bars[i - left:i + right + 1]]
        wl = [b.low for b in bars[i - left:i + right + 1]]
        if bars[i].high == max(wh):
            highs.append(bars[i].high)
        if bars[i].low == min(wl):
            lows.append(bars[i].low)
    last = bars[-1].close
    res = sorted(h for h in highs if h > last)
    sup = sorted((l for l in lows if l < last), reverse=True)
    return {
        "recent_swing_high": _round(max(highs)) if highs else None,
        "recent_swing_low": _round(min(lows)) if lows else None,
        "nearest_resistance": _round(res[0]) if res else None,
        "nearest_support": _round(sup[0]) if sup else None,
    }


def fibonacci(bars: List[OHLCVBar]) -> Dict[str, Any]:
    """Fib retracement of the day's dominant swing (low->high if up-day, high->low if down-day)."""
    hi = max(b.high for b in bars)
    lo = min(b.low for b in bars)
    hi_idx = max(range(len(bars)), key=lambda i: bars[i].high)
    lo_idx = min(range(len(bars)), key=lambda i: bars[i].low)
    up_move = hi_idx > lo_idx  # low came first -> up-swing -> retracements are supports
    diff = hi - lo
    if diff <= 0:
        return {}
    levels = {}
    for r in (0.236, 0.382, 0.5, 0.618, 0.786):
        levels[f"{r:.3f}"] = _round(hi - diff * r if up_move else lo + diff * r)
    return {
        "swing": "up (retracements = support, buy zone)" if up_move else "down (retracements = resistance, sell zone)",
        "swing_low": _round(lo), "swing_high": _round(hi), "levels": levels,
        "golden_zone": [levels["0.618"], levels["0.500"]],  # classic 50-61.8% entry band
    }


def ma_interpretation(bars: List[OHLCVBar]) -> Dict[str, Any]:
    """SMA20/40 + slope + whether price is TESTING a rising/falling MA (Pristine retracement zone)."""
    closes = [b.close for b in bars]
    if len(closes) < 40:
        return {"note": f"only {len(closes)} bars"}
    s20, s40 = sma(closes, 20), sma(closes, 40)
    s20_prev = sma(closes[:-3], 20)
    slope = "rising" if (s20 and s20_prev and s20 > s20_prev) else "falling" if (s20 and s20_prev and s20 < s20_prev) else "flat"
    last = closes[-1]
    near20 = abs(last / s20 - 1) * 100 if s20 else None
    testing = None
    if s20 and near20 is not None and near20 <= 0.4:
        testing = ("testing_rising_20MA (dynamic support / buy zone)" if slope == "rising"
                   else "testing_falling_20MA (dynamic resistance / sell zone)" if slope == "falling" else "at_flat_20MA")
    return {"sma20": _round(s20), "sma40": _round(s40), "sma20_slope": slope,
            "price_vs_sma20_pct": _round(near20, 2) if near20 is not None else None,
            "ma_test": testing, "above_sma20": (last > s20) if s20 else None}


def pristine_setup(bars: List[OHLCVBar], ma: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pristine Buy/Sell Setup: 3+ pullback bars into a rising 20MA, then a reversal/narrow-range bar
    -> buy the break of that bar's high (mirror for sell). Returns the trigger + stop."""
    if len(bars) < 6 or not ma.get("sma20"):
        return None
    last3 = bars[-4:-1]
    reds = sum(1 for b in last3 if b.close < b.open)
    greens = sum(1 for b in last3 if b.close > b.open)
    sig = bars[-1]
    if ma["sma20_slope"] == "rising" and reds >= 2 and sig.close > sig.open:
        return {"setup": "pristine_buy", "trigger": f"break above ₹{_round(sig.high)} (signal-bar high)",
                "stop": _round(sig.low), "bias": "bullish"}
    if ma["sma20_slope"] == "falling" and greens >= 2 and sig.close < sig.open:
        return {"setup": "pristine_sell", "trigger": f"break below ₹{_round(sig.low)} (signal-bar low)",
                "stop": _round(sig.high), "bias": "bearish"}
    return None


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------
def build_classic(symbol: str, resolved: str, bars: List[OHLCVBar], interval: str,
                  daily_bars: Optional[List[OHLCVBar]], warnings: List[str]) -> Dict[str, Any]:
    interval_min = interval_to_minutes(interval)
    days = group_by_day(bars)
    today = next(reversed(days))
    tb = days[today]
    last = tb[-1].close

    # prior-trend context for single-candle meaning (last ~6 bars direction)
    recent = [b.close for b in tb[-7:]]
    prior_trend = ("up" if len(recent) >= 2 and recent[-1] > recent[0] else
                   "down" if len(recent) >= 2 and recent[-1] < recent[0] else "flat")

    # candlesticks on the last COMPLETED bar (index -1 is the forming bar intraday; use -2 as signal,
    # but keep -1 too). Report both the last-completed and the pattern on the most recent bars.
    sc = single_candle(tb[-1], prior_trend)
    mc = multi_candle(tb[-2], tb[-1], tb[-3] if len(tb) >= 3 else None) if len(tb) >= 2 else None
    daily_pattern = None
    if daily_bars and len(daily_bars) >= 3:
        dtrend = "up" if daily_bars[-1].close > daily_bars[-5].close else "down"
        daily_pattern = (multi_candle(daily_bars[-2], daily_bars[-1], daily_bars[-3])
                         or single_candle(daily_bars[-1], dtrend))

    vwap = session_vwap(tb)
    orng = opening_range(tb, interval_min)
    pdl = prior_day_levels(days, today)
    prog = session_progress(tb[-1], interval_min)
    ma = ma_interpretation(bars)
    swings = swing_levels(tb)
    fib = fibonacci(tb)
    tags = bar_tags(tb)
    pset = pristine_setup(bars, ma)
    rsi = rsi_simple([b.close for b in bars][-60:], 14) if len(bars) >= 15 else None

    htf = {"hour_1": tf_trend(resample_bars(bars, max(1, 60 // interval_min))).get("trend"),
           "min_15": tf_trend(bars).get("trend"),
           "daily": tf_trend(daily_bars).get("trend") if daily_bars else "not_fetched"}

    return {
        "symbol": symbol, "resolved_ticker": resolved, "data_source": "yahoo_intraday_classic",
        "interval": interval, "as_of": tb[-1].date, "as_of_note": "IST clock",
        "method": "classic price-action (Zerodha Varsity TA + Pristine/Capra intraday)",
        "price": {"last": _round(last), "day_open": _round(tb[0].open),
                  "day_high": _round(max(b.high for b in tb)), "day_low": _round(min(b.low for b in tb)),
                  "prior_trend_intraday": prior_trend},
        "candlesticks": {
            "last_bar_single": sc, "last_two_bar": mc, "daily_bar": daily_pattern,
            "bar_tags": tags,
            "note": "candlestick signals are only actionable AT a support/resistance level with volume "
                    "confirmation; stop = low (bullish) / high (bearish) of the signal candle (Varsity).",
        },
        "support_resistance": {**swings,
                               "prior_day": {k: pdl.get(k) for k in ("PDH", "PDL", "PDC", "pivot", "R1", "R2", "S1", "S2")},
                               "vwap": _round(vwap), "opening_range": orng},
        "fibonacci": fib,
        "moving_averages": ma,
        "pristine_setup": pset,
        "higher_timeframe": htf,
        "momentum": {"rsi14": _round(rsi, 1) if rsi is not None else None},
        "volume": {"rvol_vs_prior_days": rvol(days, today)},
        "session": prog,
        "bars_today": len(tb), "bars_total": len(bars),
        "news": [],
        "warnings": warnings,
    }


def analyze_classic(code: str, *, interval: str = "15min") -> Dict[str, Any]:
    warnings: List[str] = []
    yh = resolve_yahoo_symbol(code)
    yint = interval if interval.endswith("m") else interval.replace("min", "m")
    try:
        bars = fetch_intraday_yahoo(yh, yint, "1mo")  # depth for SMA40 / swings
    except Exception as e:
        return {"symbol": code, "resolved_ticker": yh,
                "error": f"no intraday data (Yahoo: {e}). Verify the NSE code.", "warnings": warnings}
    daily = None
    try:
        daily = fetch_yahoo_chart(yh, "3mo", "1d")
    except Exception as e:
        warnings.append(f"daily timeframe unavailable ({e})")
    return build_classic(code.upper().split(":")[-1], yh, bars, interval, daily, warnings)


def main() -> None:
    p = argparse.ArgumentParser(description="Intraday CLASSIC price-action facts (candlesticks/S-R/Fib/MA).")
    p.add_argument("-s", "--symbol", required=True, help="e.g. RELIANCE, NSE:TCS, TATAMOTORS")
    p.add_argument("--interval", default="15min")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "WARNING"),
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = p.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING),
                        format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    report = analyze_classic(args.symbol, interval=args.interval)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if "error" not in report else 1)


if __name__ == "__main__":
    main()
