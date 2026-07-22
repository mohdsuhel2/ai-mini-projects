#!/usr/bin/env python3
"""
StockAnalayze short-swing — DAILY facts tuned for a 3-5 DAY hold (Indian market).

The horizon between intraday (square-off same day) and the swing-analyst tools (10-20% over
days-to-a-month). This produces the reference points a short-swing trader uses to decide
BUY / SELL / HOLD / WAIT / AVOID for the next ~3-5 trading days: short moving averages
(SMA5/10/20), fast momentum (RSI slope, MACD, 3/5/10-day ROC), the recent swing high/low that
act as near-term support/resistance, a daily-ATR expected-move band for 3-5 days, volume
confirmation and (optionally) short-window relative strength vs NIFTY.

It only FETCHES + COMPUTES and prints clean JSON — the action call, stop and target live in the
`shortswing-analyst` skill.

Data source (default `auto`): Alpha Vantage TIME_SERIES_DAILY first (1 call; same 25/day free-tier
budget/counter as the swing-analyst-v2 tool), falling back to Yahoo daily on budget-exhaustion OR
empty AV data. Modes: single stock (`-s`) and screen (`--screen "A,B,C"` -> JSON array).

All indicator math is reused from stock_analyze.py so numbers stay consistent across tools.

Educational output — NOT financial advice. Targets are realistic 3-5 day bands, not guarantees.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_analyze as v1  # noqa: E402
from stock_analyze import (  # noqa: E402
    OHLCVBar,
    fetch_yahoo_chart,
    macd_bollinger_pack,
    relative_vs_benchmark,
    rsi_simple,
    sma,
    wilder_atr,
)
import stock_analyze_av as av  # noqa: E402
from stock_analyze_av import (  # noqa: E402
    AlphaVantageError,
    DailyBudgetExhausted,
    DEFAULT_MIN_INTERVAL,
    av_get,
    resolve_apikey,
    resolve_av_symbol,
)
import bhav_screener  # noqa: E402  (NSE whole-market Stage-1 discovery)

LOG = logging.getLogger("stock_analyze_shortswing")

HOLD_DAYS = (3, 5)  # target horizon
DEFAULT_BENCHMARK = "NIFTYBEES.BSE"


# --------------------------------------------------------------------------------------
# Symbol routing
# --------------------------------------------------------------------------------------
def resolve_yahoo_symbol(code: str) -> str:
    """Map a user symbol to a Yahoo daily ticker (Indian listings -> `.NS`)."""
    c = (code or "").strip().upper()
    if not c:
        raise ValueError("empty symbol")
    if ":" in c:
        ex, sym = (p.strip() for p in c.split(":", 1))
        return f"{sym}.NS" if ex in ("NSE", "BSE") else sym
    if c.endswith((".NS", ".BO", ".BSE")):
        return c.rsplit(".", 1)[0] + ".NS"
    if "." in c:
        return c
    return f"{c}.NS"


def _round(x: Optional[float], n: int = 2) -> Optional[float]:
    return round(x, n) if isinstance(x, (int, float)) else None


# --------------------------------------------------------------------------------------
# Fetchers -> daily bars (ascending)
# --------------------------------------------------------------------------------------
def fetch_daily_av(av_symbol: str, apikey: str, min_interval: float) -> List[OHLCVBar]:
    payload = av_get(
        {"function": "TIME_SERIES_DAILY", "symbol": av_symbol, "outputsize": "compact"},
        apikey, min_interval,
    )
    return av.parse_daily(payload)  # raises AlphaVantageError on empty/uncovered


def fetch_daily_yahoo(yh_symbol: str) -> List[OHLCVBar]:
    bars = fetch_yahoo_chart(yh_symbol, "6mo", "1d")
    if len(bars) < 20:
        raise RuntimeError(f"Yahoo daily for {yh_symbol} returned too few bars ({len(bars)})")
    return bars


def fetch_benchmark(source: str, apikey: Optional[str], min_interval: float) -> Optional[List[OHLCVBar]]:
    try:
        if source == "alphavantage_daily" and apikey:
            payload = av_get(
                {"function": "TIME_SERIES_DAILY", "symbol": DEFAULT_BENCHMARK, "outputsize": "compact"},
                apikey, min_interval,
            )
            return av.parse_daily(payload)
        return fetch_yahoo_chart("^NSEI", "3mo", "1d")
    except Exception as e:  # benchmark is best-effort; never fail the whole report
        LOG.debug("benchmark fetch failed: %s", e)
        return None


# --------------------------------------------------------------------------------------
# Short-horizon computations
# --------------------------------------------------------------------------------------
def _window_high_low(bars: List[OHLCVBar], n: int) -> Dict[str, Optional[float]]:
    if len(bars) < 1:
        return {"high": None, "low": None}
    grp = bars[-n:]
    return {"high": _round(max(b.high for b in grp)), "low": _round(min(b.low for b in grp))}


def build_report(
    symbol: str, resolved: str, source: str, daily: List[OHLCVBar],
    bench_bars: Optional[List[OHLCVBar]], warnings: List[str],
) -> Dict[str, Any]:
    closes = [b.close for b in daily]
    last = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else None
    day_change = _round((last / prev - 1) * 100, 2) if prev else None

    sma5, sma10 = sma(closes, 5), sma(closes, 10)
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50) if len(closes) >= 50 else None

    rsi_now = rsi_simple(closes, 14) if len(closes) >= 15 else None
    rsi_prev = rsi_simple(closes[:-3], 14) if len(closes) >= 18 else None
    macd = macd_bollinger_pack(closes) if len(closes) >= 30 else {}
    atr = wilder_atr(daily, 14)
    atr_pct = _round(atr / last * 100, 2) if (atr and last) else None

    def roc(n: int) -> Optional[float]:
        return _round((last / closes[-1 - n] - 1) * 100, 2) if len(closes) > n else None

    # near-term structure: prior 5-day high/low EXCLUDING today -> breakout/breakdown reference
    prior5 = daily[-6:-1] if len(daily) >= 6 else daily[:-1]
    prior5_high = max((b.high for b in prior5), default=None) if prior5 else None
    prior5_low = min((b.low for b in prior5), default=None) if prior5 else None
    breakout_5d = bool(prior5_high and last > prior5_high)
    breakdown_5d = bool(prior5_low and last < prior5_low)

    r5, r10, r20 = _window_high_low(daily, 5), _window_high_low(daily, 10), _window_high_low(daily, 20)

    # volume confirmation: last vs 20-day average volume
    vols = [b.volume for b in daily[-21:-1] if b.volume]
    last_vol = daily[-1].volume
    surge = _round(float(last_vol) / (sum(vols) / len(vols)), 2) if (last_vol and vols) else None

    # ATR-realistic expected-move band for the 3-5 day hold
    move_lo = atr * math.sqrt(HOLD_DAYS[0]) if atr else None
    move_hi = atr * math.sqrt(HOLD_DAYS[1]) if atr else None

    # short trend classification
    above5 = last > sma5 if sma5 else None
    above10 = last > sma10 if sma10 else None
    above20 = last > sma20 if sma20 else None
    fast_bull = bool(sma5 and sma10 and sma5 > sma10)
    up_votes = sum(1 for x in (above5, above10, above20) if x)
    if up_votes >= 2 and fast_bull:
        trend = "up"
    elif up_votes <= 1 and not fast_bull:
        trend = "down"
    else:
        trend = "sideways"

    bench_ctx: Dict[str, Any] = {"note": "relative strength off (enable with --benchmark)"}
    regime: Dict[str, Any] = {}
    if bench_bars:
        try:
            bench_ctx = relative_vs_benchmark(daily[-11:], bench_bars, DEFAULT_BENCHMARK)  # ~10-day window
            bench_ctx["window"] = "~10 trading days"
        except Exception as e:
            bench_ctx = {"benchmark": DEFAULT_BENCHMARK, "note": str(e)}
        try:
            regime = v1.market_regime(bench_bars, DEFAULT_BENCHMARK)
        except Exception as e:
            regime = {"note": str(e)}

    # --- Entry-quality guard (3-5d): don't chase overbought-into-resistance on drying volume ---
    macd_hist = macd.get("macd_histogram")
    vol_trend_ratio = None
    _vols = [b.volume for b in daily if b.volume]
    if len(_vols) >= 25:
        _recent5 = sum(_vols[-5:]) / 5.0
        _prior20 = sum(_vols[-25:-5]) / 20.0
        if _prior20:
            vol_trend_ratio = _round(_recent5 / _prior20, 3)
    near_res = r10["high"] or r20["high"]
    headroom_pct = _round((near_res / last - 1) * 100, 2) if near_res else None
    volume_drying = bool(
        (vol_trend_ratio is not None and vol_trend_ratio < 0.9)
        or (surge is not None and surge < 1.0)
    )
    # Unusual volume SPIKE on an up-day in the last ~3 bars = accumulation / catalyst likely (BULLISH).
    vol_spike_up = False
    spike_ratio = None
    if len(daily) >= 25:
        _prior = [b.volume for b in daily[-23:-3] if b.volume]
        _avg = sum(_prior) / len(_prior) if _prior else None
        for b in daily[-3:]:
            if b.volume and _avg and b.volume >= 3 * _avg and b.open and (b.close / b.open - 1) * 100 >= 1.5:
                vol_spike_up = True
                spike_ratio = round(b.volume / _avg, 1)
    pos_in_range = (
        100.0 * (last - r20["low"]) / (r20["high"] - r20["low"])
        if (r20["high"] and r20["low"] and r20["high"] > r20["low"]) else None
    )
    extended = bool(pos_in_range is not None and pos_in_range > 92)
    momentum_rolling = bool(macd_hist is not None and macd_hist <= 0)
    into_resistance = bool(headroom_pct is not None and 0 <= headroom_pct < 1.5 and not breakout_5d)
    chase_into_resistance = bool(
        rsi_now is not None and rsi_now > 72 and headroom_pct is not None and headroom_pct < 2.0
    )
    distribution_risk = bool(extended and volume_drying and momentum_rolling)
    if distribution_risk:
        entry_grade = "distribution-risk"
    elif chase_into_resistance:
        entry_grade = "overbought-into-resistance"
    elif into_resistance:
        entry_grade = "into-resistance"
    elif extended and not (surge and surge >= 1.5):
        entry_grade = "extended-no-volume"
    elif trend == "up" and (surge is not None and surge >= 1.5):
        entry_grade = "constructive"
    else:
        entry_grade = "neutral"
    entry_quality = {
        "entry_grade": entry_grade,
        "extended_in_20d_range": extended,
        "volume_trend_ratio_5v20": vol_trend_ratio,
        "volume_drying": volume_drying,
        "volume_ok": (not volume_drying),             # 5v20 trend healthy — the ROBUST confirm (vs noisy 1-day surge)
        "atr_pct": atr_pct,                           # daily ATR % of price — the volatility regime
        "low_volatility_grinder": bool(atr_pct is not None and atr_pct < 2.5),  # ⚠️ calm name -> weak short-swing edge
        "volume_spike_up": vol_spike_up,              # ⭐ unusual accumulation / catalyst-likely -> WebSearch the news
        "volume_spike_ratio": spike_ratio,
        # ⭐ BUY-THE-DIP: a pullback (short-term down/breakdown) INSIDE a longer uptrend (above SMA50), while
        # the market is risk-on and the name is oversold (RSI 30-55) on a volatile stock. Backtested this
        # mean-reversion setup hit +3% in 5d ~40% of the time (avg +1.9%) — ~2x base. A SECOND buy path the
        # momentum rule (trend=="up") misses: it buys weakness in a strong name, not a confirmed up-trend.
        "dip_buy": bool(
            (trend == "down" or breakdown_5d)
            and sma50 and last > sma50
            and (regime or {}).get("regime") == "risk-on"
            and rsi_now is not None and 30 <= rsi_now <= 55
            and not (atr_pct is not None and atr_pct < 2.5)
        ),
        "headroom_to_resistance_pct": headroom_pct,   # to 10d/20d high; <1.5% = into resistance
        "into_resistance": into_resistance,
        "momentum_rolling_over": momentum_rolling,
        "distribution_risk": distribution_risk,        # ⛔ do not initiate a fresh long
        "chase_into_resistance": chase_into_resistance,  # ⛔ overbought into resistance, buy the dip only
    }

    return {
        "symbol": symbol,
        "resolved_ticker": resolved,
        "data_source": source,
        "horizon_days": list(HOLD_DAYS),
        "as_of": daily[-1].date,
        "meta": {"currency": "INR", "exchange": "NSE" if source == "yahoo_daily" else "BSE"},
        "price": {
            "last": _round(last),
            "prev_close": _round(prev),
            "day_change_pct": day_change,
            "high_5d": r5["high"], "low_5d": r5["low"],
            "high_10d": r10["high"], "low_10d": r10["low"],
            "high_20d": r20["high"], "low_20d": r20["low"],
            "position_in_20d_range_pct": _round(
                100.0 * (last - r20["low"]) / (r20["high"] - r20["low"]), 1
            ) if (r20["high"] and r20["low"] and r20["high"] > r20["low"]) else None,
        },
        "moving_averages": {
            "sma5": _round(sma5), "sma10": _round(sma10),
            "sma20": _round(sma20), "sma50": _round(sma50),
            "above_sma5": above5, "above_sma10": above10, "above_sma20": above20,
            "sma5_above_sma10": fast_bull,
        },
        "momentum": {
            "rsi14": _round(rsi_now, 2) if rsi_now is not None else None,
            "rsi_rising": (rsi_now > rsi_prev) if (rsi_now is not None and rsi_prev is not None) else None,
            "macd_line": macd.get("macd_line"),
            "macd_signal": macd.get("macd_signal"),
            "macd_histogram": macd.get("macd_histogram"),
            "bollinger_percent_b": macd.get("bollinger_percent_b"),
            "roc_3d_pct": roc(3), "roc_5d_pct": roc(5), "roc_10d_pct": roc(10),
        },
        "structure": {
            "prior_5d_high": _round(prior5_high), "prior_5d_low": _round(prior5_low),
            "breakout_5d": breakout_5d, "breakdown_5d": breakdown_5d,
            "dist_to_5d_high_pct": _round((r5["high"] / last - 1) * 100, 2) if r5["high"] else None,
            "dist_to_5d_low_pct": _round((r5["low"] / last - 1) * 100, 2) if r5["low"] else None,
        },
        "volatility": {
            "atr14": _round(atr, 2) if atr is not None else None,
            "atr_pct": atr_pct,
            "expected_move_3_5d_pts": [_round(move_lo, 2), _round(move_hi, 2)] if move_lo else None,
            "expected_move_3_5d_pct": [
                _round(move_lo / last * 100, 2), _round(move_hi / last * 100, 2)
            ] if move_lo else None,
            "basis": "daily ATR14 * sqrt(3..5) — realistic 3-5 day travel band",
        },
        "volume": {"surge_vs_20d_avg": surge, "last_volume": last_vol},
        "short_swing_signals": {
            "trend": trend,
            "fast_bull_sma5_gt_sma10": fast_bull,
            "breakout_5d": breakout_5d,
            "breakdown_5d": breakdown_5d,
            "volume_confirmed": bool(surge and surge >= 1.5),
            "rsi_zone": (
                "overbought" if (rsi_now and rsi_now > 70) else
                "oversold" if (rsi_now and rsi_now < 30) else
                "neutral" if rsi_now is not None else None
            ),
        },
        "entry_quality": entry_quality,
        "market_regime": regime,
        "benchmark": bench_ctx,
        "bars_used": len(daily),
        "news": [],  # skill fetches near-term catalysts via WebSearch
        "warnings": warnings,
    }


# --------------------------------------------------------------------------------------
# Orchestration: AV-first, Yahoo fallback
# --------------------------------------------------------------------------------------
def analyze_one(
    code: str, *, source: str, apikey: Optional[str], min_interval: float, want_benchmark: bool,
    asof: Optional[str] = None,
) -> Dict[str, Any]:
    warnings: List[str] = []
    av_symbol = resolve_av_symbol(code)
    yh_symbol = resolve_yahoo_symbol(code)

    def _yahoo(reason: Optional[str] = None) -> Dict[str, Any]:
        if reason:
            warnings.append(reason)
        try:
            # asof/backtest: pull 2y and truncate to <= asof (no future leakage).
            daily = (fetch_yahoo_chart(yh_symbol, "2y", "1d") if asof else fetch_daily_yahoo(yh_symbol))
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, KeyError, ValueError) as e:
            return {
                "symbol": av.clean_symbol(av_symbol), "resolved_ticker": yh_symbol,
                "error": f"no daily data (Yahoo: {e}). Verify the NSE code.", "warnings": warnings,
            }
        bench = fetch_benchmark("yahoo_daily", None, min_interval) if want_benchmark else None
        if asof:
            daily = [b for b in daily if b.date <= asof]
            if bench:
                bench = [b for b in bench if b.date <= asof]
            warnings.append(f"ASOF/BACKTEST mode — as of {daily[-1].date if daily else asof}; news omitted (look-ahead)")
            if len(daily) < 50:
                return {"symbol": av.clean_symbol(av_symbol), "resolved_ticker": yh_symbol,
                        "error": f"asof {asof}: too few bars ({len(daily)}) — date predates history.",
                        "warnings": warnings}
        return build_report(av.clean_symbol(av_symbol), yh_symbol, "yahoo_daily", daily, bench, warnings)

    if source == "yahoo" or asof:   # asof needs 2y history → Yahoo only (AV compact is too short)
        return _yahoo()
    if not apikey:
        if source == "av":
            return {"symbol": code, "error": "source=av but no Alpha Vantage API key found."}
        return _yahoo("no Alpha Vantage key — using Yahoo daily directly")

    try:
        daily = fetch_daily_av(av_symbol, apikey, min_interval)
        bench = fetch_benchmark("alphavantage_daily", apikey, min_interval) if want_benchmark else None
        if len(daily) < 30:
            warnings.append(f"only {len(daily)} daily bars — SMA50/short signals may be partial")
        return build_report(av.clean_symbol(av_symbol), av_symbol, "alphavantage_daily", daily, bench, warnings)
    except DailyBudgetExhausted as e:
        if source == "av":
            return {"symbol": code, "error": f"daily_budget_exhausted: {e}"}
        return _yahoo(f"AV daily budget exhausted ({e}) — fell back to Yahoo daily")
    except (AlphaVantageError, urllib.error.URLError, RuntimeError) as e:
        if source == "av":
            return {"symbol": code, "error": str(e)}
        return _yahoo(f"AV daily unavailable ({e}) — fell back to Yahoo daily")


def run_screen(codes: List[str], *, throttle: float = 0.0, **kw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    total = len(codes)
    for i, raw in enumerate(codes):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(analyze_one(raw, **kw))
        except Exception as e:  # keep the screen going
            LOG.warning("Screen: %s failed: %s", raw, e)
            out.append({"symbol": raw, "error": str(e)})
        if throttle and i < total - 1:
            time.sleep(throttle)
    return out


# --------------------------------------------------------------------------------------
# Universe screener (rank a fixed NSE list by short-swing setup quality)
# --------------------------------------------------------------------------------------
UNIVERSE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universes")


def load_universe(name: str) -> List[str]:
    path = name if os.path.isfile(name) else os.path.join(UNIVERSE_DIR, f"{name}.txt")
    if not os.path.isfile(path):
        raise SystemExit(f"universe not found: {name} (looked for {path})")
    syms: List[str] = []
    with open(path) as f:
        for line in f:
            s = line.split("#", 1)[0].strip()
            if s:
                syms.append(s)
    if not syms:
        raise SystemExit(f"universe {name} is empty")
    return syms


def shortswing_score(rep: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Bullish setup score from the computed signals. Higher = stronger BUY/UP candidate.
    Rank descending for UP candidates, ascending for DOWN/SELL candidates."""
    ss = rep.get("short_swing_signals", {})
    mo = rep.get("momentum", {})
    m = rep.get("moving_averages", {})
    vol = rep.get("volume", {})
    b = rep.get("benchmark", {})
    trend = ss.get("trend")
    rsi = mo.get("rsi14")
    surge = vol.get("surge_vs_20d_avg")
    excess = b.get("excess_return_vs_benchmark_pct")

    s = 0.0
    reasons: List[str] = []

    if trend == "up":
        s += 3; reasons.append("trend up")
    elif trend == "down":
        s -= 3
    for key, pts in (("above_sma5", 1), ("above_sma10", 1), ("above_sma20", 1), ("sma5_above_sma10", 1)):
        if m.get(key):
            s += pts
    if ss.get("breakout_5d"):
        s += 2; reasons.append("5d breakout")
    if ss.get("breakdown_5d"):
        s -= 2
    if isinstance(rsi, (int, float)):
        if 50 <= rsi <= 70:
            s += 2; reasons.append(f"RSI {rsi:.0f}")
        elif 45 <= rsi < 50:
            s += 0.5
        elif rsi > 72:
            s -= 1.5; reasons.append(f"RSI {rsi:.0f} overbought")
        elif rsi < 30:
            s -= 0.5
    if mo.get("rsi_rising") is True:
        s += 1; reasons.append("RSI rising")
    elif mo.get("rsi_rising") is False:
        s -= 0.5
    if isinstance(mo.get("macd_histogram"), (int, float)):
        s += 1 if mo["macd_histogram"] > 0 else -1
    if isinstance(mo.get("roc_5d_pct"), (int, float)):
        s += 1 if mo["roc_5d_pct"] > 0 else -1
    if isinstance(surge, (int, float)):
        if surge >= 1.5:
            s += 2; reasons.append(f"vol {surge:.1f}x")
        elif surge >= 1.2:
            s += 1; reasons.append(f"vol {surge:.1f}x")
        elif surge < 0.7:
            s -= 0.5
    if isinstance(excess, (int, float)):
        s += 1 if excess > 0 else -0.5

    return round(s, 2), reasons


def _rank_reports(
    codes: List[str], top: int, direction: str,
    stage1: Optional[Dict[str, Dict[str, Any]]] = None, **kw: Any,
) -> Tuple[List[Dict[str, Any]], int]:
    """Deep-analyze `codes` (Stage 2), attach screen score/reason (+ optional Stage-1 metrics),
    rank, and return (top picks, skipped_count)."""
    reports = run_screen(codes, **kw)
    scored: List[Dict[str, Any]] = []
    errors = 0
    for rep in reports:
        if "error" in rep:
            errors += 1
            continue
        score, reasons = shortswing_score(rep)
        rep["screen_score"] = score
        rep["screen_reason"] = ", ".join(reasons) if reasons else "no notable bullish signals"
        if stage1 and rep.get("symbol") in stage1:
            rep["stage1_discovery"] = stage1[rep["symbol"]]
        scored.append(rep)
    scored.sort(key=lambda r: r["screen_score"], reverse=(direction == "up"))
    return scored[:top], errors


def run_universe(name: str, top: int, direction: str, **kw: Any) -> Dict[str, Any]:
    codes = load_universe(name)
    LOG.info("Universe %s: scanning %d symbols (source=%s, direction=%s)...",
             name, len(codes), kw.get("source"), direction)
    picks, errors = _rank_reports(codes, top, direction, **kw)
    LOG.info("Universe %s: returning top %d (%d skipped, no data).", name, len(picks), errors)
    return {
        "universe": name,
        "direction": direction,
        "scanned": len(codes),
        "scored": len(codes) - errors,
        "skipped_no_data": errors,
        "top_n": len(picks),
        "as_of": picks[0]["as_of"] if picks else None,
        "picks": picks,
    }


def run_discover(
    top: int, direction: str, *, pool: int, min_turnover_cr: float, bhav_days: int, **kw: Any,
) -> Dict[str, Any]:
    """Whole-market discovery: NSE bhavcopy Stage-1 shortlist -> Yahoo Stage-2 deep-dive + ranking."""
    disc = bhav_screener.discover(pool=pool, min_turnover_cr=min_turnover_cr, days=bhav_days)
    stage1 = {c["symbol"]: c for c in disc["candidates"]}
    codes = list(stage1.keys())
    LOG.info("Discovery(bhav): %d whole-market candidates -> Stage-2 deep-dive (direction=%s)...",
             len(codes), direction)
    picks, errors = _rank_reports(codes, top, direction, stage1=stage1, **kw)
    return {
        "discovery": disc["discovery"],
        "direction": direction,
        "bhav_files": disc["files_used"],
        "market_universe_size": disc["universe_size"],
        "passed_liquidity": disc["passed_liquidity"],
        "stage1_pool": len(codes),
        "min_turnover_cr": min_turnover_cr,
        "scored": len(codes) - errors,
        "skipped_no_data": errors,
        "top_n": len(picks),
        "as_of": picks[0]["as_of"] if picks else None,
        "picks": picks,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Short-swing (3-5 day) daily facts. AV first, Yahoo fallback.")
    p.add_argument("-s", "--symbol", help="e.g. RELIANCE, NSE:TCS, TATAMOTORS")
    p.add_argument("--screen", help="Comma-separated symbols -> JSON array")
    p.add_argument("--universe", help="Rank a bundled NSE list by setup quality (e.g. nifty100) or a path")
    p.add_argument("--discover", choices=("bhav",),
                   help="Whole-market discovery: NSE bhavcopy Stage-1 shortlist -> Yahoo Stage-2 ranking")
    p.add_argument("--pool", type=int, default=40, help="discover mode: Stage-1 shortlist size (default 40)")
    p.add_argument("--min-turnover-cr", type=float, default=10.0,
                   help="discover mode: min avg daily turnover in Rs cr for liquidity (default 10)")
    p.add_argument("--bhav-days", type=int, default=7, help="discover mode: recent EOD files to use (default 7)")
    p.add_argument("--top", type=int, default=10, help="universe/discover: how many top picks to return (default 10)")
    p.add_argument("--direction", choices=("up", "down"), default="up",
                   help="universe ranking: 'up' = best BUY/long candidates (default), 'down' = best SELL/short")
    p.add_argument("--throttle", type=float, default=0.3,
                   help="seconds between Yahoo calls in screen/universe mode (default 0.3; be polite on big scans)")
    p.add_argument("--source", choices=("auto", "av", "yahoo"), default="auto",
                   help="auto (AV then Yahoo; default) | av | yahoo. Universe mode forces yahoo unless overridden.")
    p.add_argument("--benchmark", action="store_true", help="short-window relative strength vs NIFTY (extra fetch)")
    p.add_argument("--asof", default=None,
                   help="BACKTEST: compute as of a past date YYYY-MM-DD (Yahoo 2y, truncated; news omitted). "
                        "Single-symbol only.")
    p.add_argument("--apikey", default=None, help="Alpha Vantage key (else env / ~/.alphavantage_key)")
    p.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL,
                   help=f"seconds between AV calls (default {DEFAULT_MIN_INTERVAL}; free tier 5/min)")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"),
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = p.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S",
        stream=sys.stderr, force=True,
    )

    apikey: Optional[str] = None
    if args.source in ("auto", "av"):
        try:
            apikey = resolve_apikey(args.apikey)
        except SystemExit:
            if args.source == "av":
                raise
            LOG.info("No AV key; auto mode will use Yahoo daily.")

    kw = dict(source=args.source, apikey=apikey, min_interval=args.min_interval,
              want_benchmark=args.benchmark)

    if args.discover:
        # Stage-2 hits Yahoo per-symbol; force yahoo unless the user overrode source.
        dkw = dict(kw, source="yahoo" if args.source == "auto" else args.source)
        result = run_discover(args.top, args.direction, pool=args.pool,
                              min_turnover_cr=args.min_turnover_cr, bhav_days=args.bhav_days,
                              throttle=args.throttle, **dkw)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.universe:
        # Universe scans hit Yahoo per-symbol; default to yahoo to avoid burning the AV budget.
        ukw = dict(kw, source="yahoo" if args.source == "auto" else args.source)
        result = run_universe(args.universe, args.top, args.direction, throttle=args.throttle, **ukw)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    if args.screen:
        codes = [c for c in args.screen.split(",") if c.strip()]
        LOG.info("Short-swing screen: %d symbols (source=%s).", len(codes), args.source)
        print(json.dumps(run_screen(codes, throttle=args.throttle, **kw), ensure_ascii=False, indent=2, default=str))
        return

    if not args.symbol:
        p.error("one of -s/--symbol, --screen, or --universe is required")

    report = analyze_one(args.symbol, asof=args.asof, **kw)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if "error" not in report else 1)


if __name__ == "__main__":
    main()
