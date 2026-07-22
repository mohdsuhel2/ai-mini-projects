#!/usr/bin/env python3
"""
StockAnalayze — historical Yahoo data + local Ollama for swing-style notes.

Fetches multiple horizons (5y weekly, 2y daily, 3mo daily, 1mo daily), Yahoo search
(sector/industry + headlines), optional Google News RSS, deep fundamentals via yfinance
(fallback legacy urllib quoteSummary), benchmark relative strength, extended technicals
(MACD, Bollinger, RSI, volume), then asks your model for a two-part decision summary plus narrative.

Default model: llama3.1:8b

This is educational output — not financial advice. Yahoo data may be delayed.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import logging
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET


DEFAULT_MODEL = "llama3.1:8b"
OLLAMA_GENERATE_PATH = "/api/generate"

# Model sometimes ignores long prompts; system message + data-last ordering reduces generic OHLCV rambling.
OLLAMA_SYSTEM = """
You are StockAnalyze — a professional swing-trading stock analysis assistant.

Your ONLY job is to analyze the supplied stock dataset and generate a HIGH-CONFIDENCE swing trading plan for the next 3–4 weeks.

You MUST focus on:
- probability-based swing opportunities
- proper entry zones
- profit booking zones
- stop loss protection
- momentum strength
- trend continuation/reversal probability
- risk vs reward
- volume confirmation
- support/resistance behavior
- overall market-relative strength if provided

You MUST NOT:
- give generic investing advice
- explain textbook concepts
- hallucinate data
- use outside knowledge
- invent news or fundamentals
- suggest long-term investing strategies

==================================================
STRICT OUTPUT RULES
==================================================

1. The FIRST character of the response MUST be "=".
No greeting, no intro, no markdown title before it.

2. Always use THIS exact structure:

==================================================
STOCKANALYZE SWING DECISION
==================================================

SYMBOL: <symbol from input>
COMPANY: <company name from input>
CURRENT PRICE: <latest close>

OVERALL SWING VIEW:
(BULLISH / NEUTRAL / BEARISH)

CONFIDENCE:
(HIGH / MEDIUM / LOW)

EXPECTED SWING DURATION:
(Approx 3–4 weeks unless invalidated)

==================================================
PART 1 — FOR EXISTING HOLDERS
==================================================

Action:
(HOLD / PARTIAL BOOKING / EXIT / HOLD WITH CAUTION)

Profit Booking Zone:
<price range>

Trailing Stop Loss:
<price>

Immediate Risk Level:
<LOW / MEDIUM / HIGH>

Reason:
- concise technical reasons
- momentum behavior
- support/resistance structure
- volume confirmation
- weakness signs if any

==================================================
PART 2 — FOR NEW ENTRY
==================================================

Best Buy Zone:
<price range>

Ideal Dip Buy Zone:
<price range or N/A>

Breakout Buy Above:
<price>

Target 1:
<price>

Target 2:
<price>

Stop Loss:
<price>

Risk/Reward:
(example: 1:2.4)

Entry Quality:
(EXCELLENT / GOOD / RISKY / AVOID)

Reason:
- support alignment
- breakout confirmation
- trend quality
- momentum strength
- volume behavior
- relative strength if available

==================================================
TECHNICAL ANALYSIS
==================================================

Trend Structure:
- higher highs/lows or breakdown structure

Momentum:
- RSI/MACD/trend momentum if metrics exist

Volume Analysis:
- accumulation/distribution observations

Support Levels:
- important supports from actual candles

Resistance Levels:
- important resistances from actual candles

Volatility View:
- calm / expanding / risky

Relative Strength:
- compare with benchmark ONLY if provided

==================================================
FUNDAMENTAL + SENTIMENT CONTEXT
==================================================

ONLY if provided:
- analyst targets
- PE valuation context
- earnings/news sentiment
- institutional confidence indications

Headlines/news are UNVERIFIED snippets.
Treat them only as sentiment/context.
Never present them as confirmed facts.

==================================================
FINAL SWING VERDICT
==================================================

One concise paragraph:
- whether this stock is attractive for a 3–4 week swing trade
- where risk becomes invalid
- whether momentum supports upside continuation

END DECISION SUMMARY
==================================================

Not financial advice.

==================================================
DATA USAGE RULES
==================================================

Use ONLY:
- OHLCV tables
- computed indicators/metrics JSON
- fundamentals JSON if available
- analyst JSON if available
- supplied news snippets

DO NOT:
- use external knowledge
- compare unrelated companies
- invent catalysts
- invent targets
- invent price levels
- invent financial values

==================================================
PRICE ANALYSIS RULES
==================================================

1. ALL price zones MUST come from:
- support/resistance
- candle structure
- moving averages if available
- breakout ranges
- recent swing highs/lows
- volatility behavior

2. Swing targets MUST be realistic for 3–4 weeks.
Avoid absurd upside projections.

3. Strong breakout + strong volume:
- allow aggressive targets

4. Weak momentum or overhead resistance:
- reduce confidence
- tighten targets

5. If risk/reward is poor:
- explicitly say AVOID

6. If trend is weak or breakdown likely:
- prefer capital protection over optimism

==================================================
IMPORTANT BEHAVIOR RULES
==================================================

- Think like a professional swing trader.
- Prioritize probability, not optimism.
- Prioritize capital protection.
- Prefer waiting for confirmation over forced entries.
- Detect fake breakouts if momentum/volume disagree.
- Mention if the stock is overextended.
- Mention if entry is late after a large rally.
- Mention if risk/reward is unfavorable.

==================================================
STYLE RULES
==================================================

- concise but detailed
- practical trading language
- deterministic output
- no motivational tone
- no emojis
- no textbook explanations
- no markdown tables
- no unnecessary fluff

The output should feel like a professional swing-trading desk note based STRICTLY on the supplied data.
"""

GENERATE_TEMPERATURE = 0.2

YAHOO_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 StockAnalayze/4.1"
    ),
    "Accept": "application/json",
}

# Browser-like headers for finance.yahoo.com HTML (avoid gzip so we can regex crumb).
YAHOO_FINANCE_HTML_HEADERS = {
    **YAHOO_HTTP_HEADERS,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Cache-Control": "no-cache",
}

QUOTE_SUMMARY_MODULES = (
    "assetProfile,summaryDetail,financialData,defaultKeyStatistics,"
    "recommendationTrend,upgradeDowngradeHistory,calendarEvents,earningsTrend,price"
)
YAHOO_SEARCH_NEWS_COUNT = 15

LOG = logging.getLogger("stock_analyze")


class _SuppressKnownYahooFundamentalsNoise(logging.Filter):
    """Yahoo quoteSummary frequently 404s for valid tickers; yfinance emits noisy ERROR lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "quotesummary" in msg.lower() and "404" in msg:
            return False
        if "no fundamentals data found for symbol" in msg.lower():
            return False
        return True


def setup_logging(level: str) -> None:
    """Log to stderr so model answer can stay clean on stdout."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    _noise = _SuppressKnownYahooFundamentalsNoise()
    logging.getLogger("yfinance").addFilter(_noise)
    for _child in ("yfinance.base", "yfinance.scrapers", "yfinance.data", "yfinance.ticker"):
        logging.getLogger(_child).addFilter(_noise)
    for _handler in logging.root.handlers:
        _handler.addFilter(_noise)


def normalize_yahoo_ticker(code: str) -> str:
    c = code.strip().upper()
    if not c:
        raise ValueError("empty symbol")
    if ":" in c:
        ex, sym = c.split(":", 1)
        ex = ex.strip().upper()
        sym = sym.strip().upper()
        if ex == "NSE":
            return f"{sym}.NS"
        if ex == "BSE":
            return f"{sym}.BO"
        return sym
    if c.endswith(".NS") or c.endswith(".BO") or "-" in c:
        return c
    return f"{c}.NS"


@dataclass
class OHLCVBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]


def fetch_yahoo_chart(ticker: str, range_param: str, interval: str) -> List[OHLCVBar]:
    """range_param: e.g. 5y, 2y, 3mo, 1mo — interval: 1d, 1wk, 1mo"""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(ticker, safe="")
        + f"?interval={urllib.parse.quote(interval)}&range={urllib.parse.quote(range_param)}"
    )
    LOG.debug("GET chart range=%s interval=%s url=%s", range_param, interval, url)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "StockAnalayze/3.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    result = data["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    bars: List[OHLCVBar] = []
    for i, ts in enumerate(timestamps):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        bars.append(
            OHLCVBar(
                date=dt,
                open=float(opens[i]) if i < len(opens) and opens[i] is not None else float(c),
                high=float(highs[i]) if i < len(highs) and highs[i] is not None else float(c),
                low=float(lows[i]) if i < len(lows) and lows[i] is not None else float(c),
                close=float(c),
                volume=float(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
            )
        )
    LOG.debug(
        "Yahoo chart OK range=%s interval=%s bars=%d (first=%s last=%s)",
        range_param,
        interval,
        len(bars),
        bars[0].date if bars else "-",
        bars[-1].date if bars else "-",
    )
    return bars


def sma(values: Sequence[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def wilder_atr(bars: Sequence[OHLCVBar], period: int = 14) -> Optional[float]:
    """Average True Range (Wilder-style approximation on available bars)."""
    if len(bars) < 2:
        return None
    trs: List[float] = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return statistics.mean(trs) if trs else None
    return statistics.mean(trs[-period:])


def build_metrics_package(
    w5y: List[OHLCVBar],
    d2y: List[OHLCVBar],
    d3m: List[OHLCVBar],
    d1m: List[OHLCVBar],
) -> Dict[str, Any]:
    closes_3m = [b.close for b in d3m]
    closes_2y = [b.close for b in d2y]
    closes_1m = [b.close for b in d1m]

    last_close = closes_3m[-1] if closes_3m else None
    low_3m = min(b.low for b in d3m) if d3m else None
    high_3m = max(b.high for b in d3m) if d3m else None
    low_20 = min(b.low for b in d3m[-20:]) if len(d3m) >= 5 else None
    high_20 = max(b.high for b in d3m[-20:]) if len(d3m) >= 5 else None

    high_52w = max(b.high for b in d2y) if d2y else None
    low_52w = min(b.low for b in d2y) if d2y else None

    pack: Dict[str, Any] = {
        "last_close": last_close,
        "sma20_daily": sma(closes_3m, 20),
        "sma50_daily": sma(closes_3m, 50) if len(closes_3m) >= 50 else None,
        "range_3mo_low": low_3m,
        "range_3mo_high": high_3m,
        "approx_support_20d": low_20,
        "approx_resistance_20d": high_20,
        "range_52w_low": low_52w,
        "range_52w_high": high_52w,
        "atr14_daily": wilder_atr(d3m, 14),
        "pct_from_52w_high": None,
        "pct_from_52w_low": None,
        "weekly_trend_note": None,
    }
    if last_close and high_52w and high_52w != 0:
        pack["pct_from_52w_high"] = (last_close / high_52w - 1) * 100
    if last_close and low_52w and low_52w != 0:
        pack["pct_from_52w_low"] = (last_close / low_52w - 1) * 100

    if w5y and len(w5y) >= 8:
        wcloses = [b.close for b in w5y[-26:]]
        if len(wcloses) >= 2:
            pack["weekly_trend_note"] = (
                "last ~26w close change ~ "
                f"{(wcloses[-1] / wcloses[0] - 1) * 100:+.2f}% (weekly bars)"
            )

    return pack


def rsi_simple(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Simple RSI from closing prices (period SMA of gains/losses)."""
    if len(closes) < period + 1:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def derived_technical_factors(d3m: List[OHLCVBar]) -> Dict[str, Any]:
    """Extra quantitative context from the same daily bars as metrics (RSI, returns, volume)."""
    closes = [b.close for b in d3m]
    out: Dict[str, Any] = {}
    if len(closes) >= 15:
        r = rsi_simple(closes, 14)
        if r is not None:
            out["rsi14_daily"] = round(r, 2)
    if len(closes) >= 6:
        out["return_5d_pct"] = round((closes[-1] / closes[-6] - 1) * 100, 3)
    if len(closes) >= 21:
        out["return_20d_pct"] = round((closes[-1] / closes[-21] - 1) * 100, 3)
    if len(closes) >= 63:
        out["return_60d_pct"] = round((closes[-1] / closes[-63] - 1) * 100, 3)

    if len(d3m) >= 22:
        prior = [b.volume for b in d3m[-21:-1]]
        last_v = d3m[-1].volume
        if last_v and prior:
            avg_v = statistics.mean([v for v in prior if v])
            if avg_v:
                out["volume_last_vs_prior20d_avg_ratio"] = round(float(last_v) / float(avg_v), 3)

    return out


def ema_sequence(closes: Sequence[float], span: int) -> List[float]:
    """Exponential moving average (seeded with first close; sufficient for context)."""
    if not closes:
        return []
    k = 2.0 / (span + 1)
    out: List[float] = [float(closes[0])]
    for i in range(1, len(closes)):
        out.append(float(closes[i]) * k + out[-1] * (1.0 - k))
    return out


def macd_bollinger_pack(closes: Sequence[float]) -> Dict[str, Any]:
    """MACD(12,26,9) last values + 20,2 Bollinger on last close window."""
    out: Dict[str, Any] = {}
    if len(closes) < 30:
        return out
    e12 = ema_sequence(closes, 12)
    e26 = ema_sequence(closes, 26)
    if len(e12) != len(e26) or not e12:
        return out
    macd_line = [e12[i] - e26[i] for i in range(len(closes))]
    sig = ema_sequence(macd_line, 9)
    if macd_line and sig:
        out["macd_line"] = round(macd_line[-1], 6)
        out["macd_signal"] = round(sig[-1], 6)
        out["macd_histogram"] = round(macd_line[-1] - sig[-1], 6)
    if len(closes) >= 20:
        tail = [float(x) for x in closes[-20:]]
        mu = statistics.mean(tail)
        sd = statistics.pstdev(tail) if len(tail) > 1 else 0.0
        upper = mu + 2.0 * sd
        lower = mu - 2.0 * sd
        last = float(closes[-1])
        width = upper - lower
        pct_b = (last - lower) / width if width and width != 0 else 0.5
        out["bollinger_mid_20"] = round(mu, 6)
        out["bollinger_upper_20"] = round(upper, 6)
        out["bollinger_lower_20"] = round(lower, 6)
        out["bollinger_percent_b"] = round(min(max(pct_b, 0.0), 1.0), 4)
    return out


def extended_technical_indicators(d3m: List[OHLCVBar]) -> Dict[str, Any]:
    """Higher-signal technicals on the 3mo daily series."""
    closes = [b.close for b in d3m]
    return macd_bollinger_pack(closes)


def compute_swing_signals(
    d3m: List[OHLCVBar],
    d2y: List[OHLCVBar],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Swing-trade signals derived from already-fetched daily bars + metrics. No network."""
    closes = [b.close for b in d3m]
    last = closes[-1] if closes else None
    sma20 = metrics.get("sma20_daily")
    sma50 = metrics.get("sma50_daily")

    above_sma20 = (last > sma20) if (last is not None and sma20) else None
    above_sma50 = (last > sma50) if (last is not None and sma50) else None

    # Trend via 20d slope of close
    trend = "sideways"
    if len(closes) >= 21:
        change = (closes[-1] / closes[-21] - 1) * 100
        if change > 3:
            trend = "up"
        elif change < -3:
            trend = "down"

    # Volume surge: last vs prior 20d average
    vol_ratio = None
    volume_confirmed = False
    if len(d3m) >= 22:
        prior = [b.volume for b in d3m[-21:-1] if b.volume]
        last_v = d3m[-1].volume
        if last_v and prior:
            avg_v = statistics.mean(prior)
            if avg_v:
                vol_ratio = round(float(last_v) / float(avg_v), 3)
                volume_confirmed = vol_ratio >= 1.5 and (last is not None and len(closes) >= 2 and closes[-1] >= closes[-2])

    # Consolidation: tight 20d range; Breakout: last close above prior 20d high
    consolidating = False
    breakout = False
    if len(d3m) >= 21:
        window = d3m[-21:-1]
        hi = max(b.high for b in window)
        lo = min(b.low for b in window)
        if lo and hi:
            band_pct = (hi - lo) / lo * 100
            consolidating = band_pct <= 12
            breakout = last is not None and last > hi

    # ADX proxy: % of last 14 closes that moved in trend direction (0..100)
    adx_proxy = None
    if len(closes) >= 15:
        ups = sum(1 for i in range(-14, 0) if closes[i] > closes[i - 1])
        directional = ups if trend == "up" else (14 - ups)
        adx_proxy = round(directional / 14 * 100, 1)

    return {
        "above_sma20": above_sma20,
        "above_sma50": above_sma50,
        "trend": trend,
        "volume_surge_ratio": vol_ratio,
        "volume_confirmed": volume_confirmed,
        "breakout": breakout,
        "consolidating": consolidating,
        "adx_proxy": adx_proxy,
    }


def entry_quality_signals(
    d3m: List[OHLCVBar],
    metrics: Dict[str, Any],
    swing: Dict[str, Any],
    rsi14: Optional[float],
    macd_hist: Optional[float],
) -> Dict[str, Any]:
    """Guard against the classic swing trap: chasing an EXTENDED trend into resistance
    on DRYING volume. This is the CGPOWER/AXISBANK failure mode — new highs on falling
    volume + rolling-over MACD = distribution, not a fresh BUY. Emits hard-gate booleans
    the skill scores on, plus an overall entry_grade."""
    last = metrics.get("last_close")
    res20 = metrics.get("approx_resistance_20d")
    ret60 = (metrics.get("derived_from_daily_bars") or {}).get("return_60d_pct")
    pct_from_52wh = metrics.get("pct_from_52w_high")
    surge = swing.get("volume_surge_ratio")

    # Volume TREND (not just today's bar): recent 5d avg vs the prior 20d avg.
    vol_trend_ratio = None
    vols = [b.volume for b in d3m if b.volume]
    if len(vols) >= 25:
        recent5 = statistics.mean(vols[-5:])
        prior20 = statistics.mean(vols[-25:-5])
        if prior20:
            vol_trend_ratio = round(recent5 / prior20, 3)

    # Headroom to nearest overhead resistance (negative => already above it / broken out).
    headroom_pct = None
    if last and res20:
        headroom_pct = round((res20 / last - 1) * 100, 2)

    volume_drying = bool(
        (vol_trend_ratio is not None and vol_trend_ratio < 0.9)
        or (surge is not None and surge < 1.0)
    )
    # Volatility regime — the biggest driver of whether a swing target is reachable.
    # Backtested: BUY setups on ATR>=2.5% names won +8% ~32% of the time (avg +4.5%);
    # on calm ATR<2.5% grinders only ~12% (avg ~0) — half the base rate. JSWCEMENT-type.
    atr = metrics.get("atr14_daily")
    atr_pct = round(atr / last * 100, 2) if (atr and last) else None
    low_vol_grinder = bool(atr_pct is not None and atr_pct < 2.5)
    # Unusual volume SPIKE on an up-day in the last ~3 bars = accumulation / catalyst likely (BULLISH).
    # Backtested: +8% in 20d hit 21% after such a spike vs 14% base. The mirror of volume_drying.
    vol_spike_up = False
    spike_ratio = None
    if len(d3m) >= 25:
        prior = [b.volume for b in d3m[-23:-3] if b.volume]
        avg20 = statistics.mean(prior) if prior else None
        for b in d3m[-3:]:
            if b.volume and avg20 and b.volume >= 3 * avg20 and b.open and (b.close / b.open - 1) * 100 >= 1.5:
                vol_spike_up = True
                spike_ratio = round(b.volume / avg20, 1)
    near_high = bool(pct_from_52wh is not None and pct_from_52wh > -4)
    extended = bool((ret60 is not None and ret60 > 30) or (pct_from_52wh is not None and pct_from_52wh > -3))
    momentum_rolling = bool(macd_hist is not None and macd_hist <= 0)
    # Into resistance = little headroom AND not yet broken above it (headroom>=0 means res sits above price).
    into_resistance = bool(headroom_pct is not None and 0 <= headroom_pct < 1.5 and not swing.get("breakout"))

    # THE failure-mode gates:
    distribution_risk = bool(extended and near_high and volume_drying and momentum_rolling)
    chase_into_resistance = bool(
        rsi14 is not None and rsi14 > 72 and headroom_pct is not None and headroom_pct < 2.5
    )

    if distribution_risk:
        grade = "distribution-risk"          # new highs on drying volume + rolling MACD -> AVOID/wait
    elif chase_into_resistance:
        grade = "overbought-into-resistance"  # RSI>72 with no headroom -> no chase, buy the pullback
    elif extended and not swing.get("volume_confirmed"):
        grade = "extended-no-volume"          # run is mature and volume hasn't confirmed -> wait for pullback
    elif into_resistance:
        grade = "into-resistance"             # pinned under 20d high with no room -> wait for break/pullback
    elif swing.get("trend") == "up" and (surge is not None and surge >= 1.5):
        grade = "constructive"
    else:
        grade = "neutral"

    return {
        "entry_grade": grade,
        "extended": extended,
        "near_52w_high": near_high,
        "volume_trend_ratio_5v20": vol_trend_ratio,   # <0.9 => volume drying up at these prices
        "volume_drying": volume_drying,
        "volume_ok": (not volume_drying),             # 5v20 trend healthy — the ROBUST confirm (vs noisy 1-day surge)
        "atr_pct": atr_pct,                           # daily ATR as % of price — the volatility regime
        "low_volatility_grinder": low_vol_grinder,    # ⚠️ ATR<2.5% -> swing target unlikely in window; ~half the edge
        "volume_spike_up": vol_spike_up,              # ⭐ unusual accumulation / catalyst-likely -> WebSearch the news
        "volume_spike_ratio": spike_ratio,
        "headroom_to_resistance_pct": headroom_pct,   # room to 20d high; <1.5% = into resistance
        "into_resistance": into_resistance,
        "momentum_rolling_over": momentum_rolling,     # MACD histogram <= 0
        "distribution_risk": distribution_risk,        # ⛔ do not initiate a fresh long
        "chase_into_resistance": chase_into_resistance,  # ⛔ overbought into resistance, buy the dip only
    }


def market_regime(bench_bars: Optional[List[OHLCVBar]], index_sym: str) -> Dict[str, Any]:
    """Broad-market (index) regime for the swing horizon. A month-long long entered while
    the index is risk-off / rolling over is fighting the tape — this makes that explicit."""
    if not bench_bars or len(bench_bars) < 25:
        return {"note": "insufficient index bars for regime"}
    closes = [b.close for b in bench_bars]
    last = closes[-1]
    s20 = sma(closes, 20)
    s50 = sma(closes, 50) if len(closes) >= 50 else None
    ret20 = round((last / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None
    trend = "sideways"
    if ret20 is not None:
        trend = "up" if ret20 > 2 else ("down" if ret20 < -2 else "sideways")
    above20 = (last > s20) if s20 else None
    above50 = (last > s50) if s50 else None
    if above20 and above50 and trend != "down":
        regime = "risk-on"
    elif above20 is False and (above50 is False or above50 is None):
        regime = "risk-off"
    else:
        regime = "neutral"
    return {
        "index": index_sym,
        "index_trend": trend,
        "index_ret_20d_pct": ret20,
        "index_above_sma20": above20,
        "index_above_sma50": above50,
        "regime": regime,
    }


def build_json_report(data: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a compact, Claude-friendly JSON report from gather_stock_data output."""
    meta = data["meta"]
    metrics = data["metrics"]
    d3m, d2y = data["d3m"], data["d2y"]
    derived = metrics.get("derived_from_daily_bars", {})
    ext = metrics.get("extended_technicals", {})
    swing = compute_swing_signals(d3m, d2y, metrics)

    last = metrics.get("last_close")
    prev = d3m[-2].close if len(d3m) >= 2 else None
    day_change = round((last / prev - 1) * 100, 3) if (last is not None and prev) else None

    eq = entry_quality_signals(d3m, metrics, swing, derived.get("rsi14_daily"), ext.get("macd_histogram"))
    # ⭐ BUY-THE-DIP overlay (needs regime + SMA50): a pullback inside a longer uptrend, risk-on, oversold on a
    # volatile name — backtested ~2x base (see entry_quality_signals note). A 2nd buy path momentum rules miss.
    _sma50 = metrics.get("sma50_daily"); _rsi = derived.get("rsi14_daily")
    _regime = (data.get("market_regime") or {}).get("regime")
    _pullback = (swing.get("trend") == "down") or (swing.get("above_sma20") is False)
    eq["dip_buy"] = bool(
        _pullback and last and _sma50 and last > _sma50 and _regime == "risk-on"
        and _rsi is not None and 30 <= _rsi <= 55 and not eq.get("low_volatility_grinder")
    )

    warnings: List[str] = []
    if data.get("asof_mode"):
        warnings.append(f"ASOF/BACKTEST mode — computed as of {data['last_bar_date']}; fundamentals/news omitted (look-ahead)")
    fund = data.get("fund_pack") or {}
    if not _fundamentals_payload_usable(fund):
        warnings.append("fundamentals sparse or unavailable (normal for some tickers)")
    if data.get("bench_ctx", {}).get("note"):
        warnings.append(f"benchmark: {data['bench_ctx']['note']}")

    return {
        "symbol": meta.get("yahoo_symbol", data["ticker"]).replace(".NS", "").replace(".BO", ""),
        "resolved_ticker": data["ticker"],
        "as_of": data["last_bar_date"],
        "meta": {
            "name": meta.get("short_name"),
            "currency": meta.get("currency"),
            "exchange": meta.get("exchange"),
            "sector": fund.get("sector_profile"),
            "industry": fund.get("industry_profile"),
        },
        "price": {
            "last": last,
            "prev_close": prev,
            "day_change_pct": day_change,
            "high_52w": metrics.get("range_52w_high"),
            "low_52w": metrics.get("range_52w_low"),
            "pct_from_52w_high": metrics.get("pct_from_52w_high"),
            "pct_from_52w_low": metrics.get("pct_from_52w_low"),
            "support_20d": metrics.get("approx_support_20d"),
            "resistance_20d": metrics.get("approx_resistance_20d"),
        },
        "indicators": {
            "sma20": metrics.get("sma20_daily"),
            "sma50": metrics.get("sma50_daily"),
            "rsi14": derived.get("rsi14_daily"),
            "atr14": metrics.get("atr14_daily"),
            "macd_line": ext.get("macd_line"),
            "macd_signal": ext.get("macd_signal"),
            "macd_histogram": ext.get("macd_histogram"),
            "bollinger_percent_b": ext.get("bollinger_percent_b"),
            "return_5d_pct": derived.get("return_5d_pct"),
            "return_20d_pct": derived.get("return_20d_pct"),
            "return_60d_pct": derived.get("return_60d_pct"),
        },
        "volume": {
            "last": d3m[-1].volume if d3m else None,
            "surge_ratio": swing.get("volume_surge_ratio"),
        },
        "swing_signals": swing,
        "entry_quality": eq,
        "market_regime": data.get("market_regime", {}),
        "benchmark": data.get("bench_ctx", {}),
        "fundamentals": fund,
        "news": [
            {
                "title": n.get("title"),
                "source": n.get("publisher") or n.get("source"),
                "published": n.get("published") or n.get("pubDate"),
                "link": n.get("link"),
            }
            for n in (data.get("news_merged") or [])[:20]
        ],
        "warnings": warnings,
    }


def run_screen(symbols: List[str], asof: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch JSON reports for many symbols in one process. Failures become error entries."""
    out: List[Dict[str, Any]] = []
    for raw in symbols:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ticker = normalize_yahoo_ticker(raw)
            data = gather_stock_data(ticker, asof=asof)
            out.append(build_json_report(data))
        except Exception as e:
            LOG.warning("Screen: %s failed: %s", raw, e)
            out.append({"symbol": raw, "error": str(e)})
        time.sleep(0.6)
    return out


def benchmark_index_for_ticker(ticker: str) -> Optional[str]:
    """Broad index in same region for relative strength (Yahoo symbol)."""
    t = ticker.upper()
    if t.endswith(".NS"):
        return "^NSEI"
    if t.endswith(".BO"):
        return "^BSESN"
    if t.endswith(".L"):
        return "^FTSE"
    if ".HK" in t or t.endswith(".HK"):
        return "^HSI"
    if t.endswith((".T", ".TO")):
        return "^GSPTSE" if t.endswith(".TO") else "^N225"
    if t.endswith((".AX", ".AU")):
        return "^AXJO"
    if t.endswith((".DE", ".F", ".MI", ".PA", ".AS", ".MC", ".SW", ".BR", ".CO", ".ST", ".OL")):
        return "^STOXX50E"
    return "^GSPC"


def relative_vs_benchmark(
    stock: List[OHLCVBar],
    benchmark: List[OHLCVBar],
    label: str,
) -> Dict[str, Any]:
    """Return / excess return on overlapping daily closes (first-to-last in overlap)."""
    bmap = {x.date: x.close for x in benchmark}
    sc, bc = [], []
    for bar in stock:
        if bar.date in bmap:
            sc.append(bar.close)
            bc.append(bmap[bar.date])
    if len(sc) < 8:
        return {"benchmark": label, "note": "insufficient overlap", "overlap_days": len(sc)}
    r_stock = (sc[-1] / sc[0] - 1.0) * 100.0
    r_bench = (bc[-1] / bc[0] - 1.0) * 100.0
    return {
        "benchmark": label,
        "overlap_trading_days": len(sc),
        "stock_total_return_pct": round(r_stock, 3),
        "benchmark_total_return_pct": round(r_bench, 3),
        "excess_return_vs_benchmark_pct": round(r_stock - r_bench, 3),
    }


def _y_raw(node: Any) -> Any:
    if isinstance(node, dict) and "raw" in node:
        return node.get("raw")
    return node


def sanitize_quote_summary_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse Yahoo quoteSummary into a prompt-sized, mostly JSON-serializable dict."""
    out: Dict[str, Any] = {}
    qs = payload.get("quoteSummary")
    if not qs:
        return {"_note": "no quoteSummary in response", "raw_error": str(payload)[:200]}
    if qs.get("error"):
        return {"_error": qs.get("error")}
    res = qs.get("result") or []
    if not res:
        return {"_note": "empty quoteSummary result"}

    r0 = res[0]
    ap = r0.get("assetProfile") or {}
    if ap:
        out["company_long_name"] = ap.get("longName") or ap.get("name")
        out["sector_profile"] = ap.get("sector")
        out["industry_profile"] = ap.get("industry")
        out["employees"] = _y_raw(ap.get("fullTimeEmployees"))
        summ = ap.get("longBusinessSummary") or ""
        if summ:
            out["business_summary_excerpt"] = summ[:1100] + ("…" if len(summ) > 1100 else "")

    fd = r0.get("financialData") or {}
    if fd:
        out["financial_targets"] = {
            "target_mean_price": _y_raw(fd.get("targetMeanPrice")),
            "target_high_price": _y_raw(fd.get("targetHighPrice")),
            "target_low_price": _y_raw(fd.get("targetLowPrice")),
            "current_price_hint": _y_raw(fd.get("currentPrice")),
            "recommendation_mean": _y_raw(fd.get("recommendationMean")),
            "recommendation_key": fd.get("recommendationKey"),
            "number_of_analyst_opinions": _y_raw(fd.get("numberOfAnalystOpinions")),
            "total_debt": _y_raw(fd.get("totalDebt")),
            "total_cash": _y_raw(fd.get("totalCash")),
            "total_revenue": _y_raw(fd.get("totalRevenue")),
            "revenue_per_share": _y_raw(fd.get("revenuePerShare")),
            "return_on_equity": _y_raw(fd.get("returnOnEquity")),
            "gross_margins": _y_raw(fd.get("grossMargins")),
            "operating_margins": _y_raw(fd.get("operatingMargins")),
            "profit_margins": _y_raw(fd.get("profitMargins")),
            "debt_to_equity": _y_raw(fd.get("debtToEquity")),
            "quick_ratio": _y_raw(fd.get("quickRatio")),
            "current_ratio": _y_raw(fd.get("currentRatio")),
        }

    dk = r0.get("defaultKeyStatistics") or {}
    if dk:
        out["key_statistics"] = {
            "beta": _y_raw(dk.get("beta")),
            "shares_outstanding": _y_raw(dk.get("sharesOutstanding")),
            "float_shares": _y_raw(dk.get("floatShares")),
            "enterprise_value": _y_raw(dk.get("enterpriseValue")),
            "trailing_pe": _y_raw(dk.get("trailingPE")),
            "forward_pe": _y_raw(dk.get("forwardPE")),
            "peg_ratio": _y_raw(dk.get("pegRatio")),
            "price_to_book": _y_raw(dk.get("priceToBook")),
            "price_to_sales": _y_raw(dk.get("priceToSalesTrailing12Months")),
            "week_change_52": _y_raw(dk.get("52WeekChange")),
            "sand_p_52_week_change": _y_raw(dk.get("SandP52WeekChange")),
            "short_percent_of_float": _y_raw(dk.get("shortPercentOfFloat")),
        }

    sd = r0.get("summaryDetail") or {}
    if sd:
        out["market_session_snapshot"] = {
            "previous_close": _y_raw(sd.get("previousClose")),
            "open": _y_raw(sd.get("open")),
            "day_low": _y_raw(sd.get("dayLow")),
            "day_high": _y_raw(sd.get("dayHigh")),
            "fifty_two_week_low": _y_raw(sd.get("fiftyTwoWeekLow")),
            "fifty_two_week_high": _y_raw(sd.get("fiftyTwoWeekHigh")),
            "market_cap": _y_raw(sd.get("marketCap")),
            "dividend_yield": _y_raw(sd.get("dividendYield")),
        }

    rt = r0.get("recommendationTrend") or {}
    trends = rt.get("trend") or []
    if trends:
        last = trends[-1]
        out["analyst_score_counts_latest_period"] = {
            "strong_buy": _y_raw(last.get("strongBuy")),
            "buy": _y_raw(last.get("buy")),
            "hold": _y_raw(last.get("hold")),
            "sell": _y_raw(last.get("sell")),
            "strong_sell": _y_raw(last.get("strongSell")),
            "period": last.get("period"),
        }

    uh = r0.get("upgradeDowngradeHistory") or {}
    hist = uh.get("history") or []
    rows = []
    for h in hist[:14]:
        rows.append(
            {
                "epoch": h.get("epochGradeDate"),
                "firm": h.get("firm"),
                "to_grade": h.get("toGrade"),
                "from_grade": h.get("fromGrade"),
                "action": h.get("action"),
            }
        )
    if rows:
        out["recent_analyst_grade_changes"] = rows

    ce = r0.get("calendarEvents") or {}
    if ce:
        ed = ce.get("earnings") or {}
        edates = ed.get("earningsDate") or []
        out["earnings_date_hints"] = [_y_raw(x) for x in edates[:3]]
        if ce.get("exDividendDate"):
            out["ex_dividend_date"] = _y_raw(ce.get("exDividendDate"))
        if ce.get("dividendDate"):
            out["dividend_date"] = _y_raw(ce.get("dividendDate"))

    et = r0.get("earningsTrend") or {}
    etrend = et.get("trend") or []
    if etrend:
        snap = []
        for row in etrend[:5]:
            ee = row.get("earningsEstimate") or {}
            re = row.get("revenueEstimate") or {}
            snap.append(
                {
                    "period": row.get("period"),
                    "earnings_growth": _y_raw(ee.get("growth")),
                    "revenue_growth": _y_raw(re.get("growth")),
                }
            )
        if snap:
            out["earnings_trend_snippet"] = snap

    return out


def _yf_scalar(v: Any) -> Any:
    """Make yfinance / numpy scalars JSON-friendly."""
    import math

    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int,)):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, str):
        return v
    return v


def compact_fundamentals_from_yfinance(ticker: str) -> Dict[str, Any]:
    """
    Pull fundamentals via yfinance (maintains Yahoo cookies/crumbs internally).
    Falls back messaging if yfinance is not installed.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {
            "_note": 'Install: pip install yfinance   (or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)',
        }

    out: Dict[str, Any] = {"source": "yfinance"}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        LOG.warning("yfinance.Ticker failed for %s: %s", ticker, e)
        return {"_error": str(e), "source": "yfinance"}

    if not info or len(info) < 4:
        out["_note"] = "yfinance info empty (symbol, rate limit, or delisting)"
        return out

    def g(key: str) -> Any:
        return _yf_scalar(info.get(key))

    out["company_long_name"] = info.get("longName") or info.get("shortName")
    out["sector_profile"] = info.get("sector")
    out["industry_profile"] = info.get("industry")
    out["employees"] = g("fullTimeEmployees")
    summ = info.get("longBusinessSummary") or ""
    if summ:
        out["business_summary_excerpt"] = summ[:1100] + ("…" if len(summ) > 1100 else "")

    out["financial_targets"] = {
        "target_mean_price": g("targetMeanPrice"),
        "target_high_price": g("targetHighPrice"),
        "target_low_price": g("targetLowPrice"),
        "current_price_hint": g("currentPrice"),
        "recommendation_mean": g("recommendationMean"),
        "recommendation_key": info.get("recommendationKey"),
        "number_of_analyst_opinions": g("numberOfAnalystOpinions"),
        "total_debt": g("totalDebt"),
        "total_cash": g("totalCash"),
        "total_revenue": g("totalRevenue"),
        "revenue_per_share": g("revenuePerShare"),
        "return_on_equity": g("returnOnEquity"),
        "gross_margins": g("grossMargins"),
        "operating_margins": g("operatingMargins"),
        "profit_margins": g("profitMargins"),
        "debt_to_equity": g("debtToEquity"),
        "quick_ratio": g("quickRatio"),
        "current_ratio": g("currentRatio"),
    }

    out["key_statistics"] = {
        "beta": g("beta"),
        "shares_outstanding": g("sharesOutstanding"),
        "float_shares": g("floatShares"),
        "enterprise_value": g("enterpriseValue"),
        "trailing_pe": g("trailingPE"),
        "forward_pe": g("forwardPE"),
        "peg_ratio": g("pegRatio"),
        "price_to_book": g("priceToBook"),
        "price_to_sales": g("priceToSalesTrailing12Months"),
        "week_change_52": g("52WeekChange"),
        "sand_p_52_week_change": g("SandP52WeekChange"),
        "short_percent_of_float": g("shortPercentOfFloat"),
    }

    out["market_session_snapshot"] = {
        "previous_close": g("previousClose"),
        "open": g("open"),
        "day_low": g("dayLow"),
        "day_high": g("dayHigh"),
        "fifty_two_week_low": g("fiftyTwoWeekLow"),
        "fifty_two_week_high": g("fiftyTwoWeekHigh"),
        "market_cap": g("marketCap"),
        "dividend_yield": g("dividendYield"),
    }

    # Optional analyst histogram if Yahoo exposes counts on info (often absent)
    _sb, _b, _h, _s, _ss = g("strongBuy"), g("buy"), g("hold"), g("sell"), g("strongSell")
    if any(x is not None for x in (_sb, _b, _h, _s, _ss)):
        out["analyst_score_counts_latest_period"] = {
            "strong_buy": _sb,
            "buy": _b,
            "hold": _h,
            "sell": _s,
            "strong_sell": _ss,
        }

    try:
        ug = getattr(t, "upgrades_downgrades", None)
        if ug is not None and hasattr(ug, "empty") and not ug.empty:
            df = ug.sort_index(ascending=False).head(14).reset_index()
            out["recent_analyst_grade_changes"] = df.to_dict(orient="records")
    except Exception as e:
        LOG.debug("yfinance upgrades_downgrades: %s", e)

    try:
        cal = getattr(t, "calendar", None)
        if cal is not None and hasattr(cal, "empty") and not cal.empty:
            out["earnings_calendar_snippet"] = cal.head(6).to_dict()
    except Exception as e:
        LOG.debug("yfinance calendar: %s", e)

    try:
        ed = getattr(t, "earnings_dates", None)
        if ed is not None and hasattr(ed, "empty") and not ed.empty:
            ed2 = ed.sort_index(ascending=False).head(8).reset_index()
            out["earnings_dates_snippet"] = ed2.to_dict(orient="records")
    except Exception as e:
        LOG.debug("yfinance earnings_dates: %s", e)

    return out


def _fundamentals_payload_usable(d: Dict[str, Any]) -> bool:
    if d.get("_error"):
        return False
    note = str(d.get("_note", ""))
    if "pip install yfinance" in note or "Install:" in note:
        return False
    ft = d.get("financial_targets") or {}
    ks = d.get("key_statistics") or {}
    return bool(
        d.get("sector_profile")
        or d.get("business_summary_excerpt")
        or ft.get("target_mean_price")
        or ft.get("number_of_analyst_opinions")
        or ks.get("trailing_pe")
        or ks.get("forward_pe")
        or ks.get("beta")
        or (d.get("market_session_snapshot") or {}).get("market_cap")
    )


def fetch_deep_fundamentals(ticker: str) -> Dict[str, Any]:
    """Prefer yfinance (robust Yahoo session); optionally merge urllib quoteSummary."""
    primary = compact_fundamentals_from_yfinance(ticker)
    if _fundamentals_payload_usable(primary):
        LOG.info("→ Deep fundamentals loaded via yfinance")
        return primary

    LOG.info("→ Trying legacy Yahoo quoteSummary (urllib)…")
    legacy = fetch_yahoo_quote_summary(ticker)

    if legacy and not legacy.get("_error") and legacy.get("_note") != "empty quoteSummary result":
        legacy["source"] = "yahoo_quoteSummary_urllib"
        if primary.get("company_long_name") or (primary.get("financial_targets") or {}).get(
            "target_mean_price"
        ):
            legacy["_yfinance_partial"] = {k: v for k, v in primary.items() if not str(k).startswith("_")}
        LOG.info("→ Deep fundamentals loaded via urllib quoteSummary fallback")
        return legacy

    if primary:
        note = primary.get("_note") or ""
        primary["_note"] = (note + " | urllib quoteSummary also unavailable").strip(" |")
        LOG.warning("Deep fundamentals remain sparse — install yfinance or check symbol/network.")
    return primary


def fetch_yahoo_quote_summary(ticker: str) -> Dict[str, Any]:
    """Full fundamentals via quoteSummary; cookie jar + optional crumb from quote HTML."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [(k, v) for k, v in YAHOO_HTTP_HEADERS.items()]

    enc_sym = urllib.parse.quote(ticker, safe="")
    quote_urls = (
        f"https://finance.yahoo.com/quote/{enc_sym}",
        f"https://finance.yahoo.com/quote/{enc_sym}/profile",
        f"https://finance.yahoo.com/quote/{enc_sym}/key-statistics",
    )
    html = ""
    last_err: Optional[BaseException] = None
    for qpage in quote_urls:
        try:
            req = urllib.request.Request(qpage, headers=YAHOO_FINANCE_HTML_HEADERS)
            resp = opener.open(req, timeout=32)
            html = resp.read().decode("utf-8", errors="replace")
            if "RootStore" in html or "crumb" in html.lower() or len(html) > 8000:
                break
        except Exception as e:
            last_err = e
            LOG.debug("Yahoo HTML page failed %s: %s", qpage, e)
            continue

    if not html:
        LOG.warning(
            "Could not load Yahoo Finance quote HTML (fundamentals skipped): %s",
            last_err,
        )
        return {}

    crumb: Optional[str] = None
    for pattern in (
        r'"crumb"\s*:\s*"([^"]+)"',
        r'"CrumbStore"\s*:\s*\{\s*"crumb"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pattern, html)
        if m:
            crumb = m.group(1)
            break

    enc_t = urllib.parse.quote(ticker, safe="")

    def request_quote_summary(include_crumb: bool) -> Optional[Dict[str, Any]]:
        params: Dict[str, str] = {"modules": QUOTE_SUMMARY_MODULES}
        if include_crumb and crumb:
            params["crumb"] = crumb
        qs = urllib.parse.urlencode(params)
        for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
            url = f"https://{host}/v10/finance/quoteSummary/{enc_t}?{qs}"
            try:
                time.sleep(0.25)
                r2 = opener.open(urllib.request.Request(url, headers=YAHOO_HTTP_HEADERS), timeout=45)
                return json.loads(r2.read().decode())
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                LOG.debug("quoteSummary HTTP %s %s body=%s", host, e.code, body)
                continue
            except json.JSONDecodeError as e:
                LOG.debug("quoteSummary JSON error %s: %s", host, e)
                continue
            except Exception as e:
                LOG.debug("quoteSummary request error %s: %s", host, e)
                continue
        return None

    payload: Optional[Dict[str, Any]] = None
    if crumb:
        payload = request_quote_summary(include_crumb=True)
    qerr = (payload.get("quoteSummary") or {}).get("error") if payload else None
    if payload is None or qerr:
        payload = request_quote_summary(include_crumb=False)

    if payload is None:
        LOG.warning("Yahoo quoteSummary request failed after fallbacks.")
        return {}
    qerr2 = (payload.get("quoteSummary") or {}).get("error")
    if qerr2:
        LOG.warning("Yahoo quoteSummary returned error: %s", qerr2)
        return {}

    compact = sanitize_quote_summary_payload(payload)
    if compact.get("_error") or compact.get("_note") == "empty quoteSummary result":
        LOG.debug("quoteSummary compact: %s", compact)
    return compact


def normalize_yahoo_news_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    ts = raw.get("providerPublishTime")
    when = "?"
    if isinstance(ts, (int, float)):
        when = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "title": (raw.get("title") or "").strip(),
        "publisher": (raw.get("publisher") or "").strip(),
        "link": (raw.get("link") or "").strip(),
        "when": when,
    }


def fetch_yahoo_search_bundle(ticker: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Sector/industry + Yahoo-hosted headlines (no API key; same family as chart/search)."""
    params = urllib.parse.urlencode(
        {
            "q": ticker,
            "quotesCount": 8,
            "newsCount": YAHOO_SEARCH_NEWS_COUNT,
            "listsCount": 0,
        }
    )
    url = f"https://query2.finance.yahoo.com/v1/finance/search?{params}"
    LOG.debug("GET finance/search newsCount=%s", YAHOO_SEARCH_NEWS_COUNT)

    data: Optional[Dict[str, Any]] = None
    for attempt in range(3):
        req = urllib.request.Request(url, headers=YAHOO_HTTP_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                delay = 1.5 * (attempt + 1)
                LOG.debug("Yahoo search 429; sleeping %.1fs before retry", delay)
                time.sleep(delay)
                continue
            if e.code == 429:
                LOG.warning(
                    "Yahoo search rate-limited (429) — sector/listing + Yahoo headlines skipped for this run."
                )
                return {}, []
            raise
    if data is None:
        return {}, []

    quotes = data.get("quotes") or []
    quote_compact: Dict[str, Any] = {}
    if quotes:
        q0 = quotes[0]
        for k in ("symbol", "shortname", "longname", "sector", "industry", "exchange", "quoteType"):
            if q0.get(k) is not None:
                quote_compact[k] = q0[k]

    news_out = [normalize_yahoo_news_item(n) for n in (data.get("news") or []) if n.get("title")]
    return quote_compact, news_out


def _rss_local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def fetch_google_news_rss(search_query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Broad web headlines via Google News RSS (free; rate-limit friendly single fetch)."""
    params = urllib.parse.urlencode({"q": search_query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    url = f"https://news.google.com/rss/search?{params}"
    hdrs = dict(YAHOO_HTTP_HEADERS)
    hdrs["Accept"] = "application/rss+xml,application/xml;q=0.9,*/*;q=0.8"
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        LOG.debug("Google RSS failed: %s", e)
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    items: List[Dict[str, Any]] = []
    for el in root.iter():
        if _rss_local_tag(el.tag) != "item":
            continue
        title = link = pub = ""
        for ch in el:
            t = _rss_local_tag(ch.tag)
            if t == "title":
                title = (ch.text or "").strip()
            elif t == "link":
                link = (ch.text or "").strip()
            elif t == "pubDate":
                pub = (ch.text or "").strip()
        if title:
            items.append(
                {
                    "title": title,
                    "publisher": "Google News RSS",
                    "link": link,
                    "when": pub or "?",
                }
            )
        if len(items) >= limit:
            break
    return items


def merge_news_headlines(
    yahoo: List[Dict[str, Any]],
    google: List[Dict[str, Any]],
    max_items: int = 22,
) -> List[Dict[str, Any]]:
    """De-duplicate by lowercased title; prefer Yahoo order then Google."""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for tag, rows in (("yahoo_finance", yahoo), ("google_news_rss", google)):
        for row in rows:
            t = (row.get("title") or "").strip().lower()
            if not t or t in seen:
                continue
            seen.add(t)
            merged = dict(row)
            merged["feed"] = tag
            out.append(merged)
            if len(out) >= max_items:
                return out
    return out


def headlines_for_prompt(items: Sequence[Dict[str, Any]]) -> str:
    if not items:
        return "(none)"
    lines = []
    for it in items:
        title = (it.get("title") or "")[:220]
        lines.append(
            f"- [{it.get('when', '?')}] {title} — {it.get('publisher', '')} [{it.get('feed', '')}]"
        )
    return "\n".join(lines)


def bars_to_compact_table(bars: List[OHLCVBar], max_rows: int) -> str:
    tail = bars[-max_rows:] if len(bars) > max_rows else bars
    lines = ["date,open,high,low,close,volume"]
    for b in tail:
        v = "" if b.volume is None else f"{b.volume:.0f}"
        lines.append(
            f"{b.date},{b.open:.4f},{b.high:.4f},{b.low:.4f},{b.close:.4f},{v}"
        )
    return "\n".join(lines)


def decision_summary_ok(text: str) -> bool:
    """Whether the model followed the mandatory decision block (prefix scan)."""
    head = text.lstrip()[:6000]
    required = (
        "PART 1 — YOU ALREADY OWN THIS STOCK",
        "PART 2 — YOU WANT TO BUY (NO POSITION)",
        "Primary call:",
        "Sell or trim near:",
        "Buy zone:",
        "Sell zone after entry (swing target):",
        "END DECISION SUMMARY",
    )
    return all(s in head for s in required)


def build_user_prompt(
    ticker: str,
    meta_name: str,
    currency: str,
    yahoo_symbol: str,
    last_bar_date: str,
    w5y: List[OHLCVBar],
    d2y: List[OHLCVBar],
    d3m: List[OHLCVBar],
    d1m: List[OHLCVBar],
    metrics: Dict[str, Any],
    listing_context: Optional[Dict[str, Any]] = None,
    news_items: Optional[List[Dict[str, Any]]] = None,
    fundamentals_pack: Optional[Dict[str, Any]] = None,
    benchmark_context: Optional[Dict[str, Any]] = None,
    retry_preamble: bool = False,
) -> str:
    table_3m = bars_to_compact_table(d3m, 65)
    table_1m = bars_to_compact_table(d1m, 35)
    weekly_tail = bars_to_compact_table(w5y[-52:], 52) if w5y else ""

    preamble = ""
    if retry_preamble:
        preamble = "\n".join(
            [
                "!!! RETRY — PREVIOUS REPLY WAS INVALID !!!",
                "You must not output generic OHLCV definitions, wrong tickers, or wrong years.",
                f"Analyze ONLY this listing: Yahoo symbol **{yahoo_symbol}**, name **{meta_name}**, currency **{currency}**.",
                f"User symbol input resolved to: **{ticker}**. Latest bar date in data: **{last_bar_date}**.",
                "Your reply MUST start with '=' as the first character (top banner line). Then the STOCKANALAYZE DECISION SUMMARY exactly as specified below.",
                "",
            ]
        )

    data_block = "\n".join(
        [
            preamble + "DATA PACKAGE — USE ONLY THIS (delayed Yahoo export; not live)",
            f"Yahoo symbol: {yahoo_symbol}",
            f"Company / instrument name: {meta_name}",
            f"Currency: {currency}",
            f"Resolved from user input: {ticker}",
            f"Latest daily bar date in this download: {last_bar_date}",
            "Do not substitute another stock, ticker, or time period from memory.",
            "",
            "LISTING & SECTOR (Yahoo Finance search snapshot — may omit some fields):",
            json.dumps(listing_context or {}, indent=2, default=str),
            "",
            "FUNDAMENTALS & ANALYSTS (yfinance + Yahoo; delayed — pip install -r requirements.txt):",
            json.dumps(fundamentals_pack or {}, indent=2, default=str),
            "",
            "RELATIVE STRENGTH vs BENCHMARK INDEX (total returns on overlapping daily closes in ~3mo sample):",
            json.dumps(benchmark_context or {}, indent=2, default=str),
            "",
            "RECENT HEADLINES (Yahoo Finance + Google News RSS — third-party; verify; sentiment only):",
            headlines_for_prompt(news_items or []),
            "",
            "AGGREGATE METRICS (computed from fetched OHLCV bars):",
            json.dumps(metrics, indent=2, default=str),
            "",
            "LAST ~52 WEEKLY BARS (OHLCV, oldest→newest within window):",
            weekly_tail or "(unavailable)",
            "",
            "LAST ~65 DAILY BARS (~3 months, OHLCV):",
            table_3m,
            "",
            "LAST ~35 DAILY BARS (~1 month, OHLCV):",
            table_1m,
        ]
    )

    instructions = "\n".join(
        [
            "",
            "================================================================================",
            "OUTPUT — READ CAREFULLY",
            "================================================================================",
            "Your NEXT output tokens must begin the STOCKANALAYZE DECISION SUMMARY.",
            "Nothing before the first line below — no intro, no definitions of OHLCV.",
            "",
            "================================================================================",
            "STOCKANALAYZE DECISION SUMMARY  (swing ~30d — not financial advice)",
            "================================================================================",
            "",
            "PART 1 — YOU ALREADY OWN THIS STOCK",
            "Primary call: <one label: HOLD | ADD | TRIM | SELL — add a very short reason after the label, same line>",
            "Sell or trim near: <swing exit zone as a PRICE RANGE with currency, e.g. 1480–1520 INR — or N/A + one short reason>",
            "Hold or reassess until: <one line: window or reassess-by date>",
            "",
            "PART 2 — YOU WANT TO BUY (NO POSITION)",
            "Primary call: <one label: ENTER IN ZONE | WAIT FOR DIP | STAND ASIDE — short reason on same line>",
            "Buy zone: <swing ENTRY zone as a PRICE RANGE with currency>",
            "Sell zone after entry (swing target): <take-profit PRICE RANGE with currency after a hypothetical buy>",
            "Hold horizon if you buy: <one line, e.g. next 3–5 weeks or reassess by YYYY-MM-DD>",
            "",
            "================================================================================",
            "END DECISION SUMMARY — narrative, chart read, and risks follow below.",
            "================================================================================",
            "",
            "After END DECISION SUMMARY, leave one blank line, then:",
            "1) Regime (weekly + daily). 2) Why you chose those zones (levels, RSI/MACD/Bollinger/volume/returns, ranges).",
            "3) If fundamentals / analyst targets / relative vs benchmark exist, integrate them qualitatively — they are delayed and incomplete.",
            "4) Brief headline sentiment vs setup — not fact.",
            "5) Risks. End with one line: not financial advice.",
            "",
            "REMINDER: The first character of your entire reply must be '=' (start of the banner).",
        ]
    )

    return data_block + instructions


def fetch_meta(ticker: str) -> Dict[str, str]:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(ticker, safe="")
        + "?interval=1d&range=5d"
    )
    LOG.debug("GET meta url=%s", url)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "StockAnalayze/3.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    meta = data["chart"]["result"][0]["meta"]
    info = {
        "short_name": str(meta.get("shortName") or meta.get("symbol") or ticker),
        "currency": str(meta.get("currency") or ""),
        "exchange": str(meta.get("exchangeName") or ""),
        "yahoo_symbol": str(meta.get("symbol") or ticker),
    }
    LOG.debug(
        "Instrument meta: %s | exchange=%s | currency=%s",
        info["short_name"],
        info["exchange"] or "?",
        info["currency"] or "?",
    )
    return info


def call_ollama_generate(
    prompt: str,
    model: str,
    base_url: str,
    *,
    system: Optional[str] = None,
    temperature: float = GENERATE_TEMPERATURE,
) -> str:
    url = base_url.rstrip("/") + OLLAMA_GENERATE_PATH
    LOG.info("→ Model analyzing (generating response)…")
    LOG.debug("POST %s model=%s prompt_chars=%d", url, model, len(prompt))
    if system:
        LOG.debug("system_chars=%d temperature=%s", len(system), temperature)
    body: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system is not None:
        body["system"] = system
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode())
    text = out.get("response")
    if not text:
        LOG.error("Ollama returned no response text keys=%s", list(out.keys()))
        raise RuntimeError(f"Unexpected Ollama response: {out}")
    text = str(text).strip()
    LOG.debug("Ollama response chars=%d", len(text))
    return text


def gather_stock_data(ticker: str, asof: Optional[str] = None) -> Dict[str, Any]:
    """Fetch + compute everything for one ticker. Network-bound. Logs to stderr.

    asof (YYYY-MM-DD): BACKTEST mode — truncate all bars to <= asof and recompute as of
    that date, so no future data leaks in. Fundamentals/news are LIVE snapshots (would be
    look-ahead), so they are OMITTED in asof mode. Use for evaluating past calls."""
    LOG.info("→ Fetching market data… (%s)%s", ticker, f" [asof {asof}]" if asof else "")
    meta = fetch_meta(ticker)
    w5y = fetch_yahoo_chart(ticker, "5y", "1wk")
    d2y = fetch_yahoo_chart(ticker, "2y", "1d")
    if asof:
        w5y = [b for b in w5y if b.date <= asof]
        d2y = [b for b in d2y if b.date <= asof]
        d3m = d2y[-65:]
        d1m = d2y[-22:]
    else:
        d3m = fetch_yahoo_chart(ticker, "3mo", "1d")
        d1m = fetch_yahoo_chart(ticker, "1mo", "1d")
    if not d3m:
        raise ValueError("No daily bars returned — check symbol or exchange suffix (.NS / .BO)"
                         + (" or asof date predates history." if asof else "."))

    last_bar_date = d3m[-1].date
    LOG.info("→ Loaded %s — %s | last daily bar %s", meta["yahoo_symbol"], meta["short_name"], last_bar_date)
    metrics = build_metrics_package(w5y, d2y, d3m, d1m)
    metrics["derived_from_daily_bars"] = derived_technical_factors(d3m)
    metrics["extended_technicals"] = extended_technical_indicators(d3m)

    benchmark_sym = benchmark_index_for_ticker(ticker)
    bench_ctx: Dict[str, Any] = {}
    regime: Dict[str, Any] = {}
    if benchmark_sym:
        time.sleep(0.55)
        try:
            bench_bars = fetch_yahoo_chart(benchmark_sym, "2y" if asof else "3mo", "1d")
            if asof:
                bench_bars = [b for b in bench_bars if b.date <= asof][-65:]
            bench_ctx = relative_vs_benchmark(d3m, bench_bars, benchmark_sym)
            regime = market_regime(bench_bars, benchmark_sym)
        except Exception as e:
            LOG.warning("Benchmark fetch failed (continuing): %s", e)
            bench_ctx = {"benchmark": benchmark_sym, "note": str(e)}
    else:
        bench_ctx = {"note": "no benchmark mapped for this ticker pattern"}

    # In asof/backtest mode, fundamentals + news are LIVE snapshots (look-ahead) → omit them.
    fund_pack: Dict[str, Any] = {}
    quote_ctx: Dict[str, Any] = {}
    news_merged: List[Dict[str, Any]] = []
    if asof:
        LOG.info("→ asof mode: skipping fundamentals/news (live snapshot would be look-ahead)")
    else:
        time.sleep(0.45)
        LOG.info("→ Deep fundamentals (yfinance / Yahoo)…")
        try:
            fund_pack = fetch_deep_fundamentals(ticker)
        except Exception as e:
            LOG.warning("Deep fundamentals fetch failed: %s", e)

        news_yh: List[Dict[str, Any]] = []
        time.sleep(0.8)
        try:
            quote_ctx, news_yh = fetch_yahoo_search_bundle(ticker)
        except (urllib.error.URLError, KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            LOG.warning("Yahoo search bundle failed (continuing): %s", e)

        news_g: List[Dict[str, Any]] = []
        try:
            news_g = fetch_google_news_rss(f'{meta["short_name"]} stock OR {ticker}', limit=12)
        except (urllib.error.URLError, ET.ParseError, ValueError) as e:
            LOG.debug("Google News RSS skipped: %s", e)
        news_merged = merge_news_headlines(news_yh, news_g, max_items=26)

    return {
        "asof_mode": bool(asof),
        "ticker": ticker,
        "meta": meta,
        "w5y": w5y, "d2y": d2y, "d3m": d3m, "d1m": d1m,
        "last_bar_date": last_bar_date,
        "metrics": metrics,
        "bench_ctx": bench_ctx,
        "market_regime": regime,
        "fund_pack": fund_pack,
        "quote_ctx": quote_ctx,
        "news_merged": news_merged,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Swing-style stock notes from Yahoo history + Ollama."
    )
    parser.add_argument(
        "-s",
        "--symbol",
        default=None,
        help="e.g. RELIANCE, NSE:TCS, INFY.BO",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        help="Ollama base URL",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON data to stdout (no LLM call). For the Claude skill.",
    )
    parser.add_argument(
        "--dump-prompt",
        action="store_true",
        help="Print only the constructed prompt (no LLM call)",
    )
    parser.add_argument(
        "--screen",
        default=None,
        help="Comma-separated symbols to batch-screen as JSON array (no LLM). e.g. TCS,INFY,RELIANCE",
    )
    parser.add_argument(
        "--asof",
        default=None,
        help="BACKTEST: compute as of a past date YYYY-MM-DD (truncates bars; omits look-ahead "
             "fundamentals/news). For evaluating past calls / walk-forward validation.",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Log detail on stderr (default: INFO, or env LOG_LEVEL)",
    )
    args = parser.parse_args()

    setup_logging(args.log_level)

    if args.screen:
        symbols = [s for s in args.screen.split(",") if s.strip()]
        reports = run_screen(symbols, asof=args.asof)
        print(json.dumps(reports, ensure_ascii=False, indent=2, default=str))
        return
    if not args.symbol:
        parser.error("one of -s/--symbol or --screen is required")

    ticker = normalize_yahoo_ticker(args.symbol)
    base_url = args.ollama_host.rstrip("/")

    LOG.info("→ %s → %s | Ollama model: %s", args.symbol, ticker, args.model)
    try:
        data = gather_stock_data(ticker, asof=args.asof)
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TypeError) as e:
        LOG.exception("Failed to load data: %s", e)
        sys.exit(1)

    meta = data["meta"]
    w5y, d2y, d3m, d1m = data["w5y"], data["d2y"], data["d3m"], data["d1m"]
    last_bar_date = data["last_bar_date"]
    metrics = data["metrics"]
    bench_ctx = data["bench_ctx"]
    fund_pack = data["fund_pack"]
    quote_ctx = data["quote_ctx"]
    news_merged = data["news_merged"]

    if args.json:
        report = build_json_report(data)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return

    user_prompt = build_user_prompt(
        ticker,
        meta["short_name"],
        meta["currency"],
        meta["yahoo_symbol"],
        last_bar_date,
        w5y,
        d2y,
        d3m,
        d1m,
        metrics,
        listing_context=quote_ctx,
        news_items=news_merged,
        fundamentals_pack=fund_pack,
        benchmark_context=bench_ctx,
        retry_preamble=False,
    )
    LOG.debug("User prompt size: %d characters (+ system %d)", len(user_prompt), len(OLLAMA_SYSTEM))

    if args.dump_prompt:
        LOG.info("→ Writing system + user prompt to stdout (no model call)")
        print("=== OLLAMA system ===\n")
        print(OLLAMA_SYSTEM)
        print("\n=== OLLAMA user ===\n")
        print(user_prompt)
        return

    try:
        answer = call_ollama_generate(
            user_prompt,
            args.model,
            base_url,
            system=OLLAMA_SYSTEM,
            temperature=GENERATE_TEMPERATURE,
        )
        if not decision_summary_ok(answer):
            LOG.warning(
                "→ First reply skipped the decision format; retrying once with stricter instructions…"
            )
            retry_prompt = build_user_prompt(
                ticker,
                meta["short_name"],
                meta["currency"],
                meta["yahoo_symbol"],
                last_bar_date,
                w5y,
                d2y,
                d3m,
                d1m,
                metrics,
                listing_context=quote_ctx,
                news_items=news_merged,
                fundamentals_pack=fund_pack,
                benchmark_context=bench_ctx,
                retry_preamble=True,
            )
            answer = call_ollama_generate(
                retry_prompt,
                args.model,
                base_url,
                system=OLLAMA_SYSTEM,
                temperature=GENERATE_TEMPERATURE,
            )
    except urllib.error.URLError as e:
        LOG.error("Ollama unreachable at %s: %s", base_url, e)
        LOG.error("Start Ollama and pull model: ollama pull %s", args.model)
        sys.exit(2)
    except Exception as e:
        LOG.exception("Ollama error: %s", e)
        sys.exit(2)

    print(answer)
    if not decision_summary_ok(answer):
        LOG.warning(
            "Answer still missing STOCKANALAYZE DECISION SUMMARY after retry. "
            "Try a stronger instruction-following model (e.g. llama3.1, qwen2.5) via -m."
        )
    LOG.info("→ Finished — full analysis is on stdout.")


if __name__ == "__main__":
    main()
