#!/usr/bin/env python3
"""
StockAnalayze intraday — 15-minute intraday facts for a FULL-DAY intraday trade (Indian market).

Companion to stock_analyze.py (swing/Yahoo) and stock_analyze_av.py (swing/Alpha Vantage). This
one fetches INTRADAY 15-min bars and computes the reference points an intraday day-trader actually
uses to judge "does this go up or down for the rest of today, where's my stop, how much to expect":
session VWAP, opening range, prior-day levels + floor pivots, gap, RVOL, 15-min RSI/MACD/ATR, and
time-of-day / session-progress. It only FETCHES + COMPUTES and prints clean JSON — the reasoning
(UP / DOWN / NO-TRADE, stop, target) lives in the `intraday-analyst` skill.

Data source (default `auto`):
  1) Alpha Vantage TIME_SERIES_INTRADAY (1 call; same 25/day free-tier budget + counter as v2).
  2) Fall back to Yahoo intraday chart API when AV budget is exhausted OR AV returns empty/too-few
     intraday bars (AV intraday for `.BSE` listings is unreliable, so Yahoo is often the workhorse).

All indicator math is reused verbatim from stock_analyze.py so numbers stay consistent across tools.

Educational output — NOT financial advice. Intraday is leveraged and high-risk. Feeds may be ~15
min delayed; the JSON always states the exact last-bar timestamp. Square off by ~3:20 PM IST.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_analyze as v1  # noqa: E402
from stock_analyze import (  # noqa: E402
    OHLCVBar, sma, wilder_atr, rsi_simple, macd_bollinger_pack, ema_sequence, fetch_yahoo_chart,
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

LOG = logging.getLogger("stock_analyze_intraday")

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN = (9, 15)     # NSE/BSE regular session open (IST)
SESSION_CLOSE = (15, 30)   # regular session close (IST)
SQUAREOFF = (15, 20)       # sensible intraday square-off (IST)
SESSION_MINUTES = 375      # 09:15 -> 15:30
OPENING_RANGE_MIN = 30     # first 30 min = opening range


# --------------------------------------------------------------------------------------
# Symbol routing
# --------------------------------------------------------------------------------------
def resolve_yahoo_symbol(code: str) -> str:
    """Map a user symbol to a Yahoo intraday ticker. Indian listings route to `.NS` (NSE intraday
    on Yahoo is reliable, unlike AV). RELIANCE -> RELIANCE.NS | NSE:TCS -> TCS.NS | INFY.BSE -> INFY.NS
    """
    c = (code or "").strip().upper()
    if not c:
        raise ValueError("empty symbol")
    if ":" in c:
        ex, sym = (p.strip() for p in c.split(":", 1))
        if ex in ("NSE", "BSE"):
            return f"{sym}.NS"
        return sym
    if c.endswith((".NS", ".BO", ".BSE")):
        return c.rsplit(".", 1)[0] + ".NS"
    if "." in c:
        return c
    return f"{c}.NS"


# --------------------------------------------------------------------------------------
# Fetchers -> List[OHLCVBar] with bar.date = "YYYY-MM-DD HH:MM:SS" (IST for Yahoo)
# --------------------------------------------------------------------------------------
def fetch_intraday_av(
    av_symbol: str, interval: str, apikey: str, min_interval: float, outputsize: str = "full"
) -> List[OHLCVBar]:
    payload = av_get(
        {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": av_symbol,
            "interval": interval,
            "outputsize": outputsize,
            "extended_hours": "false",
        },
        apikey,
        min_interval,
    )
    ts = payload.get(f"Time Series ({interval})")
    if not ts:
        raise AlphaVantageError(
            f"no 'Time Series ({interval})' in response (AV intraday often absent for .BSE)"
        )
    bars: List[OHLCVBar] = []
    for stamp in sorted(ts.keys()):  # ascending
        row = ts[stamp]
        try:
            bars.append(
                OHLCVBar(
                    date=stamp,  # AV intraday stamp, naive local exchange time
                    open=float(row["1. open"]),
                    high=float(row["2. high"]),
                    low=float(row["3. low"]),
                    close=float(row["4. close"]),
                    volume=float(row["5. volume"]) if row.get("5. volume") not in (None, "") else None,
                )
            )
        except (KeyError, ValueError) as e:
            LOG.debug("skip bad AV bar %s: %s", stamp, e)
    if len(bars) < 2:
        raise AlphaVantageError("AV intraday series present but too few bars after parse")
    return bars


def fetch_intraday_yahoo(yh_symbol: str, interval: str = "15m", rng: str = "5d") -> List[OHLCVBar]:
    """Yahoo chart API intraday. Epoch timestamps (UTC) -> IST clock strings. Regular session only."""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(yh_symbol, safe="")
        + f"?interval={urllib.parse.quote(interval)}&range={urllib.parse.quote(rng)}"
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "StockAnalayze-intraday/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    res = (data.get("chart") or {}).get("result")
    if not res:
        raise RuntimeError(f"Yahoo returned no intraday result for {yh_symbol}")
    result = res[0]
    stamps = result.get("timestamp") or []
    quote = (result.get("indicators") or {}).get("quote") or [{}]
    quote = quote[0]
    opens, highs = quote.get("open") or [], quote.get("high") or []
    lows, closes, vols = quote.get("low") or [], quote.get("close") or [], quote.get("volume") or []

    bars: List[OHLCVBar] = []
    for i, ts in enumerate(stamps):
        c = closes[i] if i < len(closes) else None
        if c is None:  # forming/empty bar
            continue
        dt_ist = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(IST)
        bars.append(
            OHLCVBar(
                date=dt_ist.strftime("%Y-%m-%d %H:%M:%S"),
                open=float(opens[i]) if i < len(opens) and opens[i] is not None else float(c),
                high=float(highs[i]) if i < len(highs) and highs[i] is not None else float(c),
                low=float(lows[i]) if i < len(lows) and lows[i] is not None else float(c),
                close=float(c),
                volume=float(vols[i]) if i < len(vols) and vols[i] is not None else None,
            )
        )
    if len(bars) < 2:
        raise RuntimeError(f"Yahoo intraday for {yh_symbol} returned too few bars")
    return bars


# --------------------------------------------------------------------------------------
# Bar helpers
# --------------------------------------------------------------------------------------
def _day_of(bar: OHLCVBar) -> str:
    return bar.date.split(" ")[0]


def _time_of(bar: OHLCVBar) -> str:
    parts = bar.date.split(" ")
    return parts[1] if len(parts) > 1 else "00:00:00"


def group_by_day(bars: List[OHLCVBar]) -> "OrderedDict[str, List[OHLCVBar]]":
    out: "OrderedDict[str, List[OHLCVBar]]" = OrderedDict()
    for b in bars:
        out.setdefault(_day_of(b), []).append(b)
    return out


def _round(x: Optional[float], n: int = 2) -> Optional[float]:
    return round(x, n) if isinstance(x, (int, float)) else None


# --------------------------------------------------------------------------------------
# Intraday computations
# --------------------------------------------------------------------------------------
def session_vwap(today_bars: List[OHLCVBar]) -> Optional[float]:
    num = den = 0.0
    for b in today_bars:
        if b.volume is None or b.volume <= 0:
            continue
        typ = (b.high + b.low + b.close) / 3.0
        num += typ * b.volume
        den += b.volume
    return num / den if den else None


def opening_range(today_bars: List[OHLCVBar], interval_min: int) -> Dict[str, Any]:
    n = max(1, OPENING_RANGE_MIN // interval_min)
    formed = len(today_bars) >= n
    grp = today_bars[:n] if today_bars else []
    if not grp:
        return {"high": None, "low": None, "formed": False, "bars_used": 0}
    return {
        "high": _round(max(b.high for b in grp)),
        "low": _round(min(b.low for b in grp)),
        "formed": formed,
        "bars_used": len(grp),
    }


def prior_day_levels(days: "OrderedDict[str, List[OHLCVBar]]", today: str) -> Dict[str, Any]:
    prior_keys = [d for d in days.keys() if d < today]
    if not prior_keys:
        return {}
    pk = prior_keys[-1]
    grp = days[pk]
    pdh = max(b.high for b in grp)
    pdl = min(b.low for b in grp)
    pdc = grp[-1].close
    p = (pdh + pdl + pdc) / 3.0
    rng = pdh - pdl
    return {
        "prior_day": pk,
        "PDH": _round(pdh),
        "PDL": _round(pdl),
        "PDC": _round(pdc),
        "pivot": _round(p),
        "R1": _round(2 * p - pdl),
        "S1": _round(2 * p - pdh),
        "R2": _round(p + rng),
        "S2": _round(p - rng),
    }


def interval_to_minutes(interval: str) -> int:
    digits = "".join(ch for ch in interval if ch.isdigit())
    return int(digits) if digits else 15


def session_progress(last_bar: OHLCVBar, interval_min: int) -> Dict[str, Any]:
    """Minutes elapsed / remaining from the last bar's IST clock time. AV stamps (naive) are read
    the same way; if they aren't true IST this is approximate (flagged by the skill)."""
    t = _time_of(last_bar)
    try:
        hh, mm = int(t[:2]), int(t[3:5])
    except ValueError:
        hh, mm = 12, 0
    now_min = hh * 60 + mm
    open_min = SESSION_OPEN[0] * 60 + SESSION_OPEN[1]
    sq_min = SQUAREOFF[0] * 60 + SQUAREOFF[1]
    elapsed = max(0, min(SESSION_MINUTES, now_min - open_min))
    to_squareoff = max(0, sq_min - now_min)
    return {
        "last_bar_time_ist": t[:5],
        "minutes_elapsed": elapsed,
        "minutes_to_squareoff": to_squareoff,
        "session_progress_pct": _round(100.0 * elapsed / SESSION_MINUTES, 1),
        "bars_remaining": int(round(to_squareoff / interval_min)),
    }


def rvol(days: "OrderedDict[str, List[OHLCVBar]]", today: str) -> Optional[float]:
    """Today's cumulative volume vs the average cumulative volume of prior days at the same bar count."""
    today_bars = days.get(today, [])
    n = len(today_bars)
    if n == 0:
        return None
    today_cum = sum(b.volume for b in today_bars if b.volume)
    prior_cums: List[float] = []
    for d, grp in days.items():
        if d >= today:
            continue
        cum = sum(b.volume for b in grp[:n] if b.volume)
        if cum > 0:
            prior_cums.append(cum)
    if not prior_cums or not today_cum:
        return None
    avg = sum(prior_cums) / len(prior_cums)
    return _round(today_cum / avg, 2) if avg else None


def intraday_structure(
    today_bars: List[OHLCVBar], last: float, day_high: float, day_low: float,
    rsi: Optional[float], macd_hist: Optional[float], rvol: Optional[float],
    vwap: Optional[float] = None,
) -> Dict[str, Any]:
    """Detect today's intraday trend STRUCTURE (higher-highs vs lower-highs) + exhaustion/distribution
    flags, and emit a SYMMETRIC directional bias (long / short / short-on-breakdown / neutral). This is
    the guard that stops "buy the VWAP dip" firing on a lower-high reversal (the SIKA failure) AND turns
    that same distribution into an actionable SHORT rather than a passive NO-TRADE."""
    n = len(today_bars)
    highs = [b.high for b in today_bars]
    lows = [b.low for b in today_bars]
    hi_idx = max(range(n), key=lambda i: highs[i])
    lo_idx = min(range(n), key=lambda i: lows[i])
    bars_since_high = n - 1 - hi_idx
    bars_since_low = n - 1 - lo_idx
    pct_off_high = (last / day_high - 1) * 100 if day_high else None
    pct_off_low = (last / day_low - 1) * 100 if day_low else None

    def swing_trend(seq: List[float]) -> str:
        if len(seq) < 6:
            return "n/a"
        recent, prior = max(seq[-3:]), max(seq[-6:-3])
        return "rising" if recent > prior else "falling" if recent < prior else "flat"

    highs_trend, lows_trend = swing_trend(highs), swing_trend(lows)
    lows_min_trend = ("rising" if (len(lows) >= 6 and min(lows[-3:]) > min(lows[-6:-3]))
                      else "falling" if (len(lows) >= 6 and min(lows[-3:]) < min(lows[-6:-3])) else "flat")

    if bars_since_high <= 1 and highs_trend != "falling":
        structure = "at/near highs (fresh)"
    elif highs_trend == "rising" and lows_min_trend in ("rising", "flat"):
        structure = "uptrend (higher highs/lows)"
    elif highs_trend == "falling" and lows_min_trend in ("falling", "flat"):
        structure = "downtrend (lower highs/lows)"
    else:
        structure = "range / mixed"

    # Per-bar volume CLIMAX (blow-off) — catches distribution that day-CUMULATIVE rvol misses: MEESHO
    # printed a 2.96M-share bar into its ₹194 top while cumulative RVOL was only 0.58. Compare the
    # recent peak bar to the day's average bar volume; a fresh climax still at/near the high = a top forming.
    vols = [b.volume or 0 for b in today_bars]
    climax_ratio = None
    blowoff_top = False
    _base = [v for v in vols[:-3] if v]
    _recent = [v for v in vols[-3:] if v]
    if len(_base) >= 3 and _recent:
        avg_base = sum(_base) / len(_base)
        if avg_base:
            climax_ratio = round(max(_recent) / avg_base, 2)
            # blow-off top FORMING: a big climax bar still at/near the high
            blowoff_top = climax_ratio >= 2.2 and pct_off_high is not None and pct_off_high >= -1.0

    flags: List[str] = []
    # climax REVERSAL: a big PER-BAR volume climax that has since given back >=1% from the high — a
    # spike-and-reverse top. Driven by the per-bar climax (NOT cumulative rvol, which false-fires on any
    # high-volume day — DALMIASUG @11:15 shorted a coil because cumulative RVOL was 9× while the per-bar
    # climax was 0.32×), and NOT gated on highs=='falling' (a one-bar spike reads as 'rising' highs, so
    # PARAS 1401->1376 and DALMIASUG 390->383 slipped through the old requirement and stayed 'long' at the top).
    if (climax_ratio and climax_ratio >= 2.2
            and pct_off_high is not None and pct_off_high <= -1.0):
        flags.append("climax_reversal")
    if (rsi is not None and rsi >= 72 and pct_off_high is not None and pct_off_high <= -1.0
            and (macd_hist is None or macd_hist <= 0)):
        flags.append("overbought_fade")            # overbought + faded from high + momentum gone
    if bars_since_high >= 4 and pct_off_high is not None and pct_off_high <= -1.0 and highs_trend == "falling":
        flags.append("faded_from_high")            # multiple lower highs since the day high

    dist = (last / vwap - 1) * 100 if vwap else None
    below_vwap = dist is not None and dist < -0.15
    losing_vwap = dist is not None and -0.6 <= dist <= 0.6 and highs_trend == "falling"  # hovering, weakening
    momentum_down = (macd_hist is not None and macd_hist <= 0) or highs_trend == "falling"
    momentum_up = (macd_hist is None or macd_hist >= 0) and highs_trend != "falling"

    # A pullback LONG is only "buyable" if structure is still up and momentum isn't rolling over.
    pullback_long_ok = (
        structure in ("uptrend (higher highs/lows)", "at/near highs (fresh)")
        and not flags and momentum_up
    )
    # A SHORT is set up when structure/exhaustion is bearish AND momentum is down.
    short_setup = (structure.startswith("down") or bool(flags)) and momentum_down

    if pullback_long_ok:
        bias = "long"
    elif short_setup and below_vwap:
        bias = "short"                 # broken down already — actionable short now / on pullback to VWAP
    elif short_setup and (losing_vwap or flags):
        bias = "short-on-breakdown"    # exhausted but still ~VWAP — short the loss of VWAP/OR-low (SIKA @ 11:06)
    else:
        bias = "neutral"               # genuinely sideways / no edge -> NO TRADE

    # Guard: don't INITIATE a fresh short at extreme-oversold RSI unless momentum strongly confirms.
    # An exhausted down-move (RSI<22) with flat MACD is bounce-prone (OLAELEC/SUZLON @14); a strong
    # -momentum breakdown (SIKA @14: MACD ~ -0.84% of price) is a real, continuing short and stays.
    if bias == "short" and rsi is not None and rsi < 22:
        macd_pct = (macd_hist / last * 100) if (macd_hist is not None and last) else 0.0
        if macd_pct > -0.4:            # not strongly negative -> exhausted, don't chase the low
            bias = "short-on-breakdown"

    # Guard: don't short AT intraday support — short the BREAK of it. If a "short" is sitting within
    # ~0.5% of the day-low, it's shorting into support (bounce-prone: BLSE @12 drifted to its open/low
    # and V-reversed +5%). Downgrade to short-on-breakdown (only short a decisive break of the low).
    # Real distributions sit well above their day-low (SIKA @12 was +3% off its low) and stay `short`.
    if bias == "short" and pct_off_low is not None and pct_off_low < 0.5:
        bias = "short-on-breakdown"

    return {
        "structure": structure,
        "recent_highs": highs_trend, "recent_lows": lows_min_trend,
        "bars_since_day_high": bars_since_high, "pct_off_day_high": _round(pct_off_high, 2),
        "bars_since_day_low": bars_since_low, "pct_off_day_low": _round(pct_off_low, 2),
        "exhaustion_flags": flags,
        "volume_climax_ratio": climax_ratio,
        "blowoff_top": blowoff_top,
        "vwap_distance_pct": _round(dist, 2) if dist is not None else None,
        "pullback_long_ok": pullback_long_ok,
        "short_setup_ok": short_setup,
        "directional_bias": bias,
        "note": {
            "long": "structure up + momentum intact — pullbacks are buyable",
            "short": "structure DOWN + below VWAP — bias is SHORT/SELL, not a dip to buy",
            "short-on-breakdown": "exhausted/distribution near VWAP — SHORT on loss of VWAP/OR-low, do NOT buy the dip",
            "neutral": "sideways / no clear edge — NO TRADE",
        }[bias],
    }


def breakout_state(today_bars: List[OHLCVBar], orng: Dict[str, Any], last: float,
                   atr: Optional[float]) -> Dict[str, Any]:
    """Detect a FRESH breakout of the opening range — the fix for 'told to buy a pullback to a level
    the stock had already broken out of' (CUPID: base ₹205 broke up at 10:00, ran to ₹210; the ₹205
    'pullback' had already passed). Distinguishes just-broke-out (buy the breakout/retest) from
    broke-out-long-ago-and-extended (the clean entry has passed)."""
    orh, orl = orng.get("high"), orng.get("low")
    if not orh or not orl or not orng.get("formed"):
        return {"note": "opening range not formed yet"}
    n = len(today_bars)
    atr_pct = (atr / last * 100) if (atr and last) else 0.6
    above = [i for i, b in enumerate(today_bars) if b.close > orh]
    below = [i for i, b in enumerate(today_bars) if b.close < orl]
    if above and last > orh:
        since = n - 1 - above[0]
        ext = (last / orh - 1) * 100
        return {"direction": "up", "level": _round(orh), "bars_since_break": since,
                "pct_beyond_level": _round(ext, 2),
                "fresh": since <= 2,                          # broke out within ~2 bars = actionable now
                "extended_past_level": ext > max(1.5, 2.5 * atr_pct),  # ran too far to chase
                "retest_zone": _round(orh)}                   # the breakout level = the buy-the-dip/retest zone
    if below and last < orl:
        since = n - 1 - below[0]
        ext = (orl / last - 1) * 100
        return {"direction": "down", "level": _round(orl), "bars_since_break": since,
                "pct_beyond_level": _round(ext, 2),
                "fresh": since <= 2,
                "extended_past_level": ext > max(1.5, 2.5 * atr_pct),
                "retest_zone": _round(orl)}
    return {"direction": "none", "note": "price inside the opening range (no breakout)"}


def reversal_watch(struct: Dict[str, Any], rsi: Optional[float], vwap: Optional[float],
                   last: float, inst: Dict[str, Any]) -> Dict[str, Any]:
    """Flag an UPTREND that is topping/exhausting as a SHORT-ON-BREAKDOWN *watch* (anticipatory).
    Surfaces 'going up now but likely to roll over' names that a confirmed-downtrend screen misses.
    It NEVER says 'short the strength now' — the trade requires a confirmation trigger (loss of VWAP /
    a lower high), preserving the SIKA discipline (short the break, not the top). A name can be a BUY
    (directional_bias long) AND a reversal watch at the same time: buy dips until the trigger fires."""
    structure = struct.get("structure", "")
    if structure not in ("uptrend (higher highs/lows)", "at/near highs (fresh)"):
        return {"reversal_short_watch": False}
    flags = list(struct.get("exhaustion_flags") or [])
    pctb = (inst.get("bollinger") or {}).get("percent_b")
    ext = inst.get("price_vs_ema20_pct")
    blowoff = struct.get("blowoff_top")
    signals = list(flags)
    if rsi is not None and rsi >= 78:
        signals.append("rsi_overbought_extreme")
    if pctb is not None and pctb >= 0.98:
        signals.append("upper_bollinger_tag")
    if ext is not None and ext >= 3.0:
        signals.append("parabolic_vs_ema20")
    if blowoff:
        signals.append(f"volume_blowoff_at_high (x{struct.get('volume_climax_ratio')})")
    # Watch = a blow-off climax bar at the high, OR any hard exhaustion flag, OR >=2 topping signals.
    if not (blowoff or flags or len(signals) >= 2):
        return {"reversal_short_watch": False}
    trig = (f"15m close below VWAP ₹{_round(vwap)}" if vwap else "loss of intraday support")
    # EARLY (aggressive) reversal-short flag — a confirmed LOWER HIGH has printed after the top.
    # Backtested (50 volatile names, 60d, 21 blow-offs): shorting the lower high hit target ~43% (36%
    # stopped) vs the VWAP-loss trigger's ~100% (0 stopped). So this is a LOWER-CONFIDENCE, small-size,
    # tight-stop early option — NOT a substitute for the confirmed below-VWAP short. Blindly shorting the
    # climax bar itself lost (67% stopped), so the lower-high confirmation is the minimum bar to enter early.
    rh = struct.get("recent_highs")
    bsh = struct.get("bars_since_day_high")
    pct_off = struct.get("pct_off_day_high")
    early = None
    if rh == "falling" and isinstance(bsh, (int, float)) and bsh >= 1 and (blowoff or len(signals) >= 2):
        day_high = last / (1 + (pct_off or 0) / 100.0) if pct_off else last
        early = {"armed": True, "entry_hint": _round(last),
                 "stop_above": _round(day_high * 1.002),
                 "confidence": "low (~43% backtested vs ~100% for the confirmed below-VWAP short)",
                 "note": "aggressive EARLY reversal short: a lower high has printed after the blow-off. "
                         "Short small with a stop above the day high; the higher-probability entry is still "
                         "the confirmed 15m close below VWAP."}
    return {"reversal_short_watch": True, "topping_signals": signals,
            "short_trigger": trig + " (or a lower high, then loss of the last swing low)",
            "early_short": early,
            "note": "uptrend showing exhaustion/over-extension — SHORT-ON-BREAKDOWN watch: do NOT short "
                    "into strength; short only AFTER the trigger. It is still a BUY-the-dip until then. "
                    "Once it CLOSES below VWAP it becomes a first-class SHORT (backtested ~100% to the "
                    "VWAP-cover target) — treat that as an actionable short, not a passive wait."}


def nearest_levels(last: float, levels: Dict[str, float]) -> Dict[str, Any]:
    """Nearest level above (resistance) and below (support) the last price, from the level stack."""
    above = {k: v for k, v in levels.items() if v is not None and v > last}
    below = {k: v for k, v in levels.items() if v is not None and v < last}
    res = min(above.items(), key=lambda kv: kv[1]) if above else (None, None)
    sup = max(below.items(), key=lambda kv: kv[1]) if below else (None, None)
    return {
        "nearest_resistance": {"label": res[0], "level": _round(res[1])} if res[0] else None,
        "nearest_support": {"label": sup[0], "level": _round(sup[1])} if sup[0] else None,
    }


# --------------------------------------------------------------------------------------
# Institutional indicators: EMAs, ADX, SuperTrend, multi-timeframe trend, market context
# --------------------------------------------------------------------------------------
def ema_last(values: List[float], span: int) -> Optional[float]:
    if len(values) < span:
        return None
    seq = ema_sequence(values, span)
    return seq[-1] if seq else None


def adx(bars: List[OHLCVBar], period: int = 14) -> Optional[Dict[str, Any]]:
    """Wilder ADX(14) with +DI/-DI on the given bars."""
    n = len(bars)
    if n < 2 * period + 2:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = bars[i].high - bars[i - 1].high
        dn = bars[i - 1].low - bars[i].low
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    def wilder(vals: List[float]) -> List[float]:
        if len(vals) < period:
            return []
        out = [sum(vals[:period])]
        for v in vals[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    str_, sp, sm = wilder(trs), wilder(plus_dm), wilder(minus_dm)
    if not str_ or not sp or not sm:
        return None
    dx = []
    for i in range(len(str_)):
        if str_[i] == 0:
            dx.append(0.0)
            continue
        pdi = 100 * sp[i] / str_[i]
        mdi = 100 * sm[i] / str_[i]
        s = pdi + mdi
        dx.append(100 * abs(pdi - mdi) / s if s else 0.0)
    adx_val = sum(dx[-period:]) / period if len(dx) >= period else (sum(dx) / len(dx) if dx else None)
    last_pdi = 100 * sp[-1] / str_[-1] if str_[-1] else 0.0
    last_mdi = 100 * sm[-1] / str_[-1] if str_[-1] else 0.0
    return {"adx": _round(adx_val, 1), "plus_di": _round(last_pdi, 1), "minus_di": _round(last_mdi, 1)}


def supertrend(bars: List[OHLCVBar], period: int = 10, mult: float = 3.0) -> Optional[Dict[str, Any]]:
    """SuperTrend(10, 3) direction + level (rolling Wilder ATR)."""
    n = len(bars)
    if n < period + 2:
        return None
    trs = []
    for i in range(1, n):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    atr = [sum(trs[:period]) / period]
    for i in range(period, len(trs)):
        atr.append((atr[-1] * (period - 1) + trs[i]) / period)
    dir_, fu, fl, st = 1, None, None, None
    for k in range(len(atr)):
        idx = period + k
        if idx >= n:
            break
        hl2 = (bars[idx].high + bars[idx].low) / 2
        bu, bl = hl2 + mult * atr[k], hl2 - mult * atr[k]
        if fu is None:
            fu, fl = bu, bl
            dir_ = 1 if bars[idx].close >= bl else -1
            st = fl if dir_ == 1 else fu
            continue
        fu = bu if (bu < fu or bars[idx - 1].close > fu) else fu
        fl = bl if (bl > fl or bars[idx - 1].close < fl) else fl
        if dir_ == 1:
            dir_, st = (-1, fu) if bars[idx].close < fl else (1, fl)
        else:
            dir_, st = (1, fl) if bars[idx].close > fu else (-1, fu)
    return {"direction": "up" if dir_ == 1 else "down", "level": _round(st, 2) if st is not None else None}


def resample_bars(bars: List[OHLCVBar], factor: int) -> List[OHLCVBar]:
    """Aggregate `factor` consecutive bars into one (e.g. 15m x4 -> 1h)."""
    out: List[OHLCVBar] = []
    for i in range(0, len(bars), factor):
        grp = bars[i:i + factor]
        if not grp:
            continue
        vols = [b.volume for b in grp if b.volume is not None]
        out.append(OHLCVBar(date=grp[-1].date, open=grp[0].open,
                            high=max(b.high for b in grp), low=min(b.low for b in grp),
                            close=grp[-1].close, volume=sum(vols) if vols else None))
    return out


def tf_trend(bars: List[OHLCVBar]) -> Dict[str, Any]:
    """Classify a timeframe's trend from EMA9/20/(50) stack + price."""
    closes = [b.close for b in bars]
    if len(closes) < 21:
        return {"trend": "unknown", "note": f"only {len(closes)} bars"}
    e9, e20 = ema_last(closes, 9), ema_last(closes, 20)
    e50 = ema_last(closes, 50) if len(closes) >= 50 else None
    last = closes[-1]
    if e9 and e20 and last > e9 > e20 and (e50 is None or last > e50):
        t = "bullish"
    elif e9 and e20 and last < e9 < e20 and (e50 is None or last < e50):
        t = "bearish"
    else:
        t = "neutral"
    seq = ema_sequence(closes, 20)
    slope = None
    if len(seq) >= 4:
        slope = "up" if seq[-1] > seq[-4] else "down" if seq[-1] < seq[-4] else "flat"
    return {"trend": t, "ema9": _round(e9), "ema20": _round(e20), "ema50": _round(e50), "ema20_slope": slope}


def overall_bias(trends: List[str]) -> str:
    votes = [t for t in trends if t in ("bullish", "bearish")]
    bull, bear = votes.count("bullish"), votes.count("bearish")
    if bull >= 3 and bear == 0:
        return "strong bullish"
    if bull > bear:
        return "moderately bullish"
    if bear >= 3 and bull == 0:
        return "strong bearish"
    if bear > bull:
        return "moderately bearish"
    return "neutral"


def _vix_ctx(vb: Optional[List[OHLCVBar]]) -> Dict[str, Any]:
    if not vb or len(vb) < 2:
        return {"note": "unavailable"}
    lv, pv = vb[-1].close, vb[-2].close
    return {"level": _round(lv), "change_pct": _round((lv / pv - 1) * 100, 2),
            "regime": ("low" if lv < 13 else "normal" if lv < 18 else "elevated" if lv < 22 else "high")}


def _nifty_ctx(nb: Optional[List[OHLCVBar]]) -> Dict[str, Any]:
    if not nb:
        return {"note": "unavailable"}
    ntb = group_by_day(nb)[next(reversed(group_by_day(nb)))]
    nlast, nopen = ntb[-1].close, ntb[0].open
    return {"last": _round(nlast), "day_change_pct": _round((nlast / nopen - 1) * 100, 2),
            "trend_15m": tf_trend(nb).get("trend")}


def institutional_block(bars: List[OHLCVBar], last: float, vwap: Optional[float]) -> Dict[str, Any]:
    """EMA stack, ADX, SuperTrend, Bollinger on the 15-min series."""
    closes = [b.close for b in bars]
    e9, e20 = ema_last(closes, 9), ema_last(closes, 20)
    e50 = ema_last(closes, 50) if len(closes) >= 50 else None
    e200 = ema_last(closes, 200) if len(closes) >= 200 else None
    if all(x is not None for x in (e9, e20, e50, e200)):
        if last > e9 > e20 > e50 > e200:
            align = "bullish_stack"
        elif last < e9 < e20 < e50 < e200:
            align = "bearish_stack"
        else:
            align = "mixed"
    else:
        align = "insufficient_bars_for_ema200"
    boll = macd_bollinger_pack(closes[-120:]) if len(closes) >= 30 else {}
    return {
        "ema9": _round(e9), "ema20": _round(e20), "ema50": _round(e50), "ema200": _round(e200),
        "ema_alignment": align,
        "price_vs_ema20_pct": _round((last / e20 - 1) * 100, 2) if e20 else None,
        "adx": adx(bars, 14),
        "supertrend": supertrend(bars, 10, 3.0),
        "bollinger": {
            "upper": boll.get("bollinger_upper_20"), "mid": boll.get("bollinger_mid_20"),
            "lower": boll.get("bollinger_lower_20"), "percent_b": boll.get("bollinger_percent_b"),
        },
    }


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------
def build_report(
    symbol: str, resolved: str, source: str, bars: List[OHLCVBar], interval: str, warnings: List[str],
    daily_bars: Optional[List[OHLCVBar]] = None, m5_bars: Optional[List[OHLCVBar]] = None,
    market: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    interval_min = interval_to_minutes(interval)
    days = group_by_day(bars)
    today = next(reversed(days))
    today_bars = days[today]
    last = today_bars[-1].close
    day_open = today_bars[0].open

    closes = [b.close for b in bars]
    rsi = rsi_simple(closes[-60:], 14) if len(closes) >= 15 else None
    macd = macd_bollinger_pack(closes[-80:]) if len(closes) >= 30 else {}
    atr = wilder_atr(bars[-15:], 14)

    vwap = session_vwap(today_bars)
    orng = opening_range(today_bars, interval_min)
    pdl = prior_day_levels(days, today)
    gap_pct = _round((day_open / pdl["PDC"] - 1) * 100, 2) if pdl.get("PDC") else None
    today_high = max(b.high for b in today_bars)
    today_low = min(b.low for b in today_bars)
    prog = session_progress(today_bars[-1], interval_min)

    last3 = [b.close for b in today_bars[-4:]]
    if len(last3) >= 2:
        bar_dir = "up" if last3[-1] > last3[0] else "down" if last3[-1] < last3[0] else "flat"
    else:
        bar_dir = None

    # level stack for nearest support/resistance
    stack: Dict[str, float] = {}
    if vwap is not None:
        stack["VWAP"] = vwap
    if orng.get("high") is not None:
        stack["OR_high"] = orng["high"]
        stack["OR_low"] = orng["low"]
    for k in ("PDH", "PDL", "pivot", "R1", "R2", "S1", "S2"):
        if pdl.get(k) is not None:
            stack[k] = pdl[k]
    stack["day_high"] = today_high
    stack["day_low"] = today_low
    near = nearest_levels(last, stack)

    proj_pts = atr * math.sqrt(prog["bars_remaining"]) if (atr and prog["bars_remaining"] > 0) else None
    proj_pct = _round(proj_pts / last * 100, 2) if proj_pts else None

    rvol_val = rvol(days, today)
    struct = intraday_structure(today_bars, last, today_high, today_low, rsi,
                                macd.get("macd_histogram"), rvol_val, vwap)
    brk = breakout_state(today_bars, orng, last, atr)

    # Institutional indicators (15m) + higher-timeframe trends (multi-TF) + market regime
    inst = institutional_block(bars, last, vwap)
    m15_t = tf_trend(bars)
    h1_t = tf_trend(resample_bars(bars, max(1, 60 // interval_min)))
    m5_t = tf_trend(m5_bars) if m5_bars else {"trend": "not_fetched"}
    daily_t = tf_trend(daily_bars) if daily_bars else {"trend": "not_fetched"}
    htf = {
        "daily": daily_t, "hour_1": h1_t, "min_15": m15_t, "min_5": m5_t,
        "overall_bias": overall_bias([daily_t.get("trend"), h1_t.get("trend"),
                                       m15_t.get("trend"), m5_t.get("trend")]),
    }

    # --- RECONCILE intraday exhaustion against the higher-timeframe trend (backtest-driven fix) ---
    # A pullback/"exhaustion" INSIDE a strong-bullish HTF + strong up-ADX + SuperTrend-up is a
    # BUY-THE-DIP, not a short (SUMICHEM/ZENSAR false-short bug). Only let the intraday short stand
    # when the bigger trend isn't fighting it. Mirror for strong-bearish HTF.
    adx_v = inst.get("adx") or {}
    adx_strong = bool(adx_v.get("adx") and adx_v["adx"] >= 30)
    di_up = adx_v.get("plus_di", 0) > adx_v.get("minus_di", 0)
    st_up = (inst.get("supertrend") or {}).get("direction") == "up"
    # HTF is "up" if the overall bias is bullish OR the two always-available TFs (1h + 15m) are both
    # bullish (daily/5m may be unfetched in fast/backtest paths).
    htf_up = (htf["overall_bias"] in ("strong bullish", "moderately bullish")
              or (h1_t.get("trend") == "bullish" and m15_t.get("trend") == "bullish"))
    # Only reconcile-to-long when price is STILL ABOVE VWAP — that is a genuine dip in an uptrend
    # (SUMICHEM/ZENSAR). If price is BELOW VWAP the intraday breakdown is confirmed and the short
    # STANDS even if slower higher-TF indicators still lag bullish (SIKA lost VWAP -> real −8% short).
    if (struct["directional_bias"] in ("short", "short-on-breakdown")
            and htf_up and adx_strong and di_up and st_up
            and vwap is not None and last >= vwap):
        struct["directional_bias"] = "long-on-pullback"
        struct["note"] = ("reconciled: bullish HTF + up-ADX + SuperTrend-up + still above VWAP -> the "
                          "intraday exhaustion is a BUY-the-dip, not a short")
        struct["htf_reconciled"] = True

    rev = reversal_watch(struct, rsi, vwap, last, inst)

    return {
        "symbol": symbol,
        "resolved_ticker": resolved,
        "data_source": source,
        "interval": interval,
        "as_of": today_bars[-1].date,
        "as_of_note": "IST clock" if source == "yahoo_intraday" else "exchange-local (AV, may not be IST)",
        "meta": {"currency": "INR", "exchange": "NSE" if source == "yahoo_intraday" else "BSE"},
        "price": {
            "last": _round(last),
            "day_open": _round(day_open),
            "day_high": _round(today_high),
            "day_low": _round(today_low),
            "position_in_day_range_pct": _round(
                100.0 * (last - today_low) / (today_high - today_low), 1
            ) if today_high > today_low else None,
        },
        "vwap": {
            "vwap": _round(vwap),
            "above_vwap": (last > vwap) if vwap is not None else None,
            "distance_pct": _round((last / vwap - 1) * 100, 2) if vwap else None,
        },
        "opening_range": orng,
        "opening_range_state": {
            "breakout_up": (last > orng["high"]) if orng.get("high") is not None else None,
            "breakdown_down": (last < orng["low"]) if orng.get("low") is not None else None,
        },
        "gap": {
            "gap_pct": gap_pct,
            "direction": ("up" if gap_pct and gap_pct > 0 else "down" if gap_pct and gap_pct < 0 else "flat")
            if gap_pct is not None else None,
        },
        "prior_day_levels": pdl or {"note": "no prior-day bars in window"},
        "nearest_levels": near,
        "indicators": {
            "rsi14": _round(rsi, 2) if rsi is not None else None,
            "macd_line": macd.get("macd_line"),
            "macd_signal": macd.get("macd_signal"),
            "macd_histogram": macd.get("macd_histogram"),
            "atr14_intraday": _round(atr, 2) if atr is not None else None,
            "recent_bar_direction": bar_dir,
        },
        "volume": {
            "rvol_vs_prior_days": rvol_val,
            "last_bar_volume": today_bars[-1].volume,
        },
        "intraday_structure": struct,
        "breakout": brk,
        "reversal_watch": rev,
        "institutional": inst,
        "higher_timeframe": htf,
        "market_context": market or {"note": "not fetched (use full mode)"},
        "session": prog,
        "projection": {
            "atr_projected_remaining_move_pts": _round(proj_pts, 2) if proj_pts else None,
            "atr_projected_remaining_move_pct": proj_pct,
            "basis": "atr14_intraday * sqrt(bars_remaining) — rough cap on rest-of-session travel",
        },
        "bars_today": len(today_bars),
        "bars_total": len(bars),
        "news": [],  # skill fetches same-day catalysts via WebSearch
        "warnings": warnings,
    }


# --------------------------------------------------------------------------------------
# Orchestration: AV first, Yahoo fallback
# --------------------------------------------------------------------------------------
def analyze_intraday(
    code: str, *, source: str, interval: str, apikey: Optional[str], min_interval: float,
    outputsize: str, full: bool = True,
) -> Dict[str, Any]:
    warnings: List[str] = []
    av_symbol = resolve_av_symbol(code)
    yh_symbol = resolve_yahoo_symbol(code)

    def _extras() -> Tuple[Optional[List[OHLCVBar]], Optional[List[OHLCVBar]], Optional[Dict[str, Any]]]:
        """Higher-timeframe (daily, 5m) + India VIX / NIFTY context. All via Yahoo, fetched in
        PARALLEL (independent GETs) so full mode stays ~one round-trip, not four."""
        if not full:
            return None, None, None
        jobs = {
            "daily": lambda: fetch_yahoo_chart(yh_symbol, "6mo", "1d"),
            "m5": lambda: fetch_intraday_yahoo(yh_symbol, "5m", "5d"),
            "vix": lambda: fetch_yahoo_chart("^INDIAVIX", "5d", "1d"),
            "nifty": lambda: fetch_intraday_yahoo("^NSEI", "15m", "5d"),
        }
        res: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(fn): name for name, fn in jobs.items()}
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    res[name] = fut.result()
                except Exception as e:
                    warnings.append(f"{name} timeframe/context unavailable ({e})")
                    res[name] = None
        market = {"india_vix": _vix_ctx(res.get("vix")), "nifty": _nifty_ctx(res.get("nifty"))}
        return res.get("daily"), res.get("m5"), market

    def _yahoo(reason: Optional[str] = None) -> Dict[str, Any]:
        if reason:
            warnings.append(reason)
        yint = interval if interval.endswith("m") else interval.replace("min", "m")
        rng = "1mo" if full else "5d"  # 1mo of 15m ~ enough bars for EMA200 / ADX
        try:
            bars = fetch_intraday_yahoo(yh_symbol, yint, rng)
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, KeyError, ValueError) as e:
            return {
                "symbol": av.clean_symbol(av_symbol),
                "resolved_ticker": yh_symbol,
                "error": f"no intraday data (Yahoo: {e}). Symbol may be uncovered/renamed intraday; "
                         f"verify the NSE code.",
                "warnings": warnings,
            }
        d, m5, mkt = _extras()
        return build_report(av.clean_symbol(av_symbol), yh_symbol, "yahoo_intraday", bars, interval,
                            warnings, d, m5, mkt)

    if source == "yahoo":
        return _yahoo()

    # source in ("auto", "av")
    if not apikey:
        if source == "av":
            raise SystemExit("source=av but no Alpha Vantage API key found.")
        return _yahoo("no Alpha Vantage key — using Yahoo intraday directly")

    try:
        bars = fetch_intraday_av(av_symbol, interval, apikey, min_interval, outputsize)
        d, m5, mkt = _extras()
        return build_report(av.clean_symbol(av_symbol), av_symbol, "alphavantage_intraday", bars,
                            interval, warnings, d, m5, mkt)
    except DailyBudgetExhausted as e:
        if source == "av":
            return {"symbol": code, "error": f"daily_budget_exhausted: {e}"}
        return _yahoo(f"AV daily budget exhausted ({e}) — fell back to Yahoo intraday")
    except (AlphaVantageError, urllib.error.URLError, RuntimeError) as e:
        if source == "av":
            return {"symbol": code, "error": str(e)}
        return _yahoo(f"AV intraday unavailable ({e}) — fell back to Yahoo intraday")


def main() -> None:
    p = argparse.ArgumentParser(description="Intraday 15-min facts (Indian market). AV first, Yahoo fallback.")
    p.add_argument("-s", "--symbol", required=True, help="e.g. RELIANCE, NSE:TCS, TATAMOTORS")
    p.add_argument("--source", choices=("auto", "av", "yahoo"), default="auto",
                   help="auto (AV then Yahoo on exhaustion/empty; default) | av | yahoo")
    p.add_argument("--interval", default="15min", help="AV-style interval (default 15min)")
    p.add_argument("--apikey", default=None, help="Alpha Vantage key (else env / ~/.alphavantage_key)")
    p.add_argument("--outputsize", choices=("compact", "full"), default="full",
                   help="AV intraday size: full (today + prior days; needed for pivots) or compact")
    p.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL,
                   help=f"seconds between AV calls (default {DEFAULT_MIN_INTERVAL}; free tier 5/min)")
    p.add_argument("--fast", action="store_true",
                   help="skip the institutional multi-timeframe + market-context fetches (base 15m only)")
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
            LOG.info("No AV key; auto mode will use Yahoo intraday.")

    report = analyze_intraday(
        args.symbol, source=args.source, interval=args.interval, apikey=apikey,
        min_interval=args.min_interval, outputsize=args.outputsize, full=not args.fast,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if "error" not in report else 1)


if __name__ == "__main__":
    main()
