#!/usr/bin/env python3
"""
StockAnalayze v2 — Alpha Vantage backend (Indian market, BSE-routed).

Emits the SAME clean-JSON contract as stock_analyze.py (--json / --screen), but sources
price history + fundamentals from Alpha Vantage's REST API instead of Yahoo. All swing
indicators (SMA/RSI/MACD/ATR/ADX-proxy/breakout/volume) are computed LOCALLY by importing
the exact same pure functions from stock_analyze.py, so v1 and v2 stay numerically identical.

Why this shape:
- Alpha Vantage's free tier is 25 requests/day, 5/minute. TIME_SERIES_DAILY returns the whole
  daily history in ONE call, so we never touch AV's per-indicator endpoints (which would be
  8-11 calls/stock). Default cost: ~2 calls for a single stock (DAILY + OVERVIEW), 1 call/stock
  when screening. News stays on WebSearch (skill layer) — free and better for Indian names.
- Indian symbols are routed to AV's `.BSE` suffix (NSE quote data on AV is unreliable). Most
  NSE large/mid caps are dual-listed on BSE with the same symbol.

API key: --apikey  >  env ALPHAVANTAGE_API_KEY  >  ~/.alphavantage_key . Use `demo` (IBM only)
to smoke-test the pipeline.

Educational output — not financial advice. Data may be delayed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

# Reuse v1's indicator math verbatim (pure stdlib functions; importing is cheap + safe —
# stock_analyze imports yfinance lazily, never at module load).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_analyze as v1  # noqa: E402
from stock_analyze import OHLCVBar  # noqa: E402

LOG = logging.getLogger("stock_analyze_av")

AV_BASE = "https://www.alphavantage.co/query"
DEFAULT_MIN_INTERVAL = 13.0          # 5 calls/min on free tier -> >=12s apart
DEFAULT_BENCHMARK = "NIFTYBEES.BSE"  # Nippon Nifty-50 ETF on BSE (relative-strength proxy)
USAGE_FILE = os.path.expanduser("~/.alphavantage_usage.json")
DAILY_FREE_LIMIT = 25

_LAST_CALL_AT = 0.0


# --------------------------------------------------------------------------------------
# Symbol routing (NSE/BSE -> Alpha Vantage)
# --------------------------------------------------------------------------------------
def resolve_av_symbol(code: str) -> str:
    """Map a user symbol to an Alpha Vantage symbol. Indian listings route to `.BSE`.

    RELIANCE -> RELIANCE.BSE | NSE:TCS -> TCS.BSE | INFY.NS/.BO -> INFY.BSE
    BSE:500325 -> 500325.BSE | US:IBM / IBM.US -> IBM | TSCO.LON -> TSCO.LON (passthrough)
    """
    c = (code or "").strip().upper()
    if not c:
        raise ValueError("empty symbol")
    if ":" in c:
        ex, sym = (p.strip() for p in c.split(":", 1))
        if ex in ("NSE", "BSE"):
            return f"{sym}.BSE"
        if ex in ("US", "NYSE", "NASDAQ"):
            return sym
        return sym
    if c.endswith((".NS", ".BO", ".BSE")):
        return c.rsplit(".", 1)[0] + ".BSE"
    if c.endswith(".US"):
        return c[:-3]
    if "." in c:          # already an AV-style suffixed symbol (e.g. TSCO.LON, IBM US bare won't hit this)
        return c
    return f"{c}.BSE"     # India default


def clean_symbol(av_symbol: str) -> str:
    return av_symbol.rsplit(".", 1)[0] if av_symbol.endswith(".BSE") else av_symbol


# --------------------------------------------------------------------------------------
# Alpha Vantage HTTP (rate-limit aware + daily usage counter)
# --------------------------------------------------------------------------------------
def _read_usage() -> Dict[str, Any]:
    try:
        with open(USAGE_FILE) as f:
            u = json.load(f)
        if u.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return u
    except Exception:
        pass
    return {"date": datetime.now().strftime("%Y-%m-%d"), "count": 0}


def _bump_usage() -> int:
    u = _read_usage()
    u["count"] = int(u.get("count", 0)) + 1
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(u, f)
    except Exception as e:
        LOG.debug("usage file write failed: %s", e)
    return u["count"]


def _throttle(min_interval: float) -> None:
    global _LAST_CALL_AT
    wait = min_interval - (time.time() - _LAST_CALL_AT)
    if wait > 0:
        LOG.debug("throttle: sleeping %.1fs (5 calls/min cap)", wait)
        time.sleep(wait)
    _LAST_CALL_AT = time.time()


class AlphaVantageError(RuntimeError):
    pass


class DailyBudgetExhausted(AlphaVantageError):
    pass


def av_get(params: Dict[str, str], apikey: str, min_interval: float) -> Dict[str, Any]:
    """One Alpha Vantage GET. Raises AlphaVantageError on AV's Note/Information/Error payloads."""
    q = dict(params)
    q["apikey"] = apikey
    url = AV_BASE + "?" + urllib.parse.urlencode(q)

    used = _bump_usage()
    if used > DAILY_FREE_LIMIT:
        LOG.warning("AV daily usage counter at %d (free tier ~%d/day) — calls may start failing.",
                    used, DAILY_FREE_LIMIT)
    _throttle(min_interval)
    LOG.info("→ AV %s %s (call #%d today)", params.get("function"), params.get("symbol", ""), used)

    req = urllib.request.Request(url, headers={"User-Agent": "StockAnalayze-v2/1.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode())

    if not isinstance(data, dict):
        raise AlphaVantageError("unexpected AV payload (not an object)")
    # AV signals problems via these keys instead of HTTP errors:
    if "Information" in data and len(data) == 1:
        msg = str(data["Information"])
        low = msg.lower()
        if "25 requests" in low or "per day" in low or "rate limit" in low or "call frequency" in low:
            raise DailyBudgetExhausted(msg)
        raise AlphaVantageError(msg)  # e.g. a premium-only feature/endpoint
    if "Note" in data and len(data) == 1:
        raise AlphaVantageError(str(data["Note"]))  # per-minute throttle
    if "Error Message" in data:
        raise AlphaVantageError(str(data["Error Message"]))  # bad symbol / bad call
    return data


# --------------------------------------------------------------------------------------
# Parse AV responses -> OHLCV bars / fundamentals
# --------------------------------------------------------------------------------------
def parse_daily(payload: Dict[str, Any]) -> List[OHLCVBar]:
    ts = payload.get("Time Series (Daily)")
    if not ts:
        raise AlphaVantageError("no 'Time Series (Daily)' in response (symbol may be uncovered)")
    bars: List[OHLCVBar] = []
    for date in sorted(ts.keys()):  # ascending (oldest -> newest)
        row = ts[date]
        try:
            bars.append(OHLCVBar(
                date=date,
                open=float(row["1. open"]),
                high=float(row["2. high"]),
                low=float(row["3. low"]),
                close=float(row["4. close"]),
                volume=float(row["5. volume"]) if row.get("5. volume") not in (None, "") else None,
            ))
        except (KeyError, ValueError) as e:
            LOG.debug("skip bad bar %s: %s", date, e)
    if not bars:
        raise AlphaVantageError("daily series present but empty after parse")
    return bars


def weekly_from_daily(daily: List[OHLCVBar], weeks: int = 260) -> List[OHLCVBar]:
    """Resample daily bars to weekly OHLCV (ISO week), newest `weeks` kept."""
    buckets: Dict[str, List[OHLCVBar]] = {}
    order: List[str] = []
    for b in daily:
        y, w, _ = datetime.strptime(b.date, "%Y-%m-%d").isocalendar()
        key = f"{y}-W{w:02d}"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(b)
    out: List[OHLCVBar] = []
    for key in order:
        grp = buckets[key]
        vols = [g.volume for g in grp if g.volume is not None]
        out.append(OHLCVBar(
            date=grp[-1].date,
            open=grp[0].open,
            high=max(g.high for g in grp),
            low=min(g.low for g in grp),
            close=grp[-1].close,
            volume=sum(vols) if vols else None,
        ))
    return out[-weeks:]


def _num(v: Any) -> Optional[float]:
    if v in (None, "", "None", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def map_overview(o: Dict[str, Any]) -> Dict[str, Any]:
    """Map AV OVERVIEW -> the same fundamentals shape v1 emits (best-effort; AV is sparser)."""
    if not o or not o.get("Symbol"):
        return {"source": "alphavantage_overview", "_note": "OVERVIEW empty (common for .BSE listings)"}
    desc = o.get("Description") or ""
    out: Dict[str, Any] = {
        "source": "alphavantage_overview",
        "company_long_name": o.get("Name"),
        "sector_profile": (o.get("Sector") or "").title() or None,
        "industry_profile": (o.get("Industry") or "").title() or None,
        "employees": None,
        "business_summary_excerpt": (desc[:1100] + ("…" if len(desc) > 1100 else "")) or None,
        "financial_targets": {
            "target_mean_price": _num(o.get("AnalystTargetPrice")),
            "target_high_price": None,
            "target_low_price": None,
            "current_price_hint": None,
            "recommendation_mean": None,
            "recommendation_key": "none",
            "number_of_analyst_opinions": None,
            "total_debt": None,
            "total_cash": None,
            "total_revenue": _num(o.get("RevenueTTM")),
            "revenue_per_share": _num(o.get("RevenuePerShareTTM")),
            "return_on_equity": _num(o.get("ReturnOnEquityTTM")),
            "gross_margins": None,  # AV OVERVIEW has no gross margin (GrossProfitTTM is an absolute $)
            "operating_margins": _num(o.get("OperatingMarginTTM")),
            "profit_margins": _num(o.get("ProfitMargin")),
            "debt_to_equity": None,
            "quick_ratio": None,
            "current_ratio": None,
        },
        "key_statistics": {
            "beta": _num(o.get("Beta")),
            "shares_outstanding": _num(o.get("SharesOutstanding")),
            "float_shares": None,
            "enterprise_value": None,
            "trailing_pe": _num(o.get("PERatio")),
            "forward_pe": _num(o.get("ForwardPE")),
            "peg_ratio": _num(o.get("PEGRatio")),
            "price_to_book": _num(o.get("PriceToBookRatio")),
            "price_to_sales": _num(o.get("PriceToSalesRatioTTM")),
            "week_change_52": None,
            "sand_p_52_week_change": None,
            "short_percent_of_float": None,
        },
        "market_session_snapshot": {
            "previous_close": None,
            "open": None,
            "day_low": None,
            "day_high": None,
            "fifty_two_week_low": _num(o.get("52WeekLow")),
            "fifty_two_week_high": _num(o.get("52WeekHigh")),
            "market_cap": _num(o.get("MarketCapitalization")),
            "dividend_yield": _num(o.get("DividendYield")),
        },
    }
    return out


# --------------------------------------------------------------------------------------
# Build the v1-compatible JSON report from AV data
# --------------------------------------------------------------------------------------
def build_report(
    av_symbol: str,
    daily: List[OHLCVBar],
    fund_pack: Dict[str, Any],
    bench_ctx: Dict[str, Any],
    warnings: List[str],
) -> Dict[str, Any]:
    w5y = weekly_from_daily(daily, 260)
    d2y = daily[-504:]
    d3m = daily[-65:]
    d1m = daily[-22:]

    metrics = v1.build_metrics_package(w5y, d2y, d3m, d1m)
    metrics["derived_from_daily_bars"] = v1.derived_technical_factors(d3m)
    metrics["extended_technicals"] = v1.extended_technical_indicators(d3m)
    swing = v1.compute_swing_signals(d3m, d2y, metrics)
    derived = metrics["derived_from_daily_bars"]
    ext = metrics["extended_technicals"]

    last = metrics.get("last_close")
    prev = d3m[-2].close if len(d3m) >= 2 else None
    day_change = round((last / prev - 1) * 100, 3) if (last is not None and prev) else None

    fund = fund_pack or {}
    sector = fund.get("sector_profile")
    industry = fund.get("industry_profile")

    return {
        "symbol": clean_symbol(av_symbol),
        "resolved_ticker": av_symbol,
        "data_source": "alphavantage",
        "as_of": d3m[-1].date if d3m else None,
        "meta": {
            "name": fund.get("company_long_name") or clean_symbol(av_symbol),
            "currency": "INR" if av_symbol.endswith(".BSE") else None,
            "exchange": "BSE" if av_symbol.endswith(".BSE") else None,
            "sector": sector,
            "industry": industry,
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
        "benchmark": bench_ctx,
        "fundamentals": fund,
        "news": [],  # news is fetched by the skill via WebSearch (free, better India coverage)
        "warnings": warnings,
    }


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def analyze_one(
    code: str,
    apikey: str,
    *,
    want_fundamentals: bool,
    want_benchmark: bool,
    benchmark_symbol: str,
    outputsize: str,
    min_interval: float,
) -> Dict[str, Any]:
    av_symbol = resolve_av_symbol(code)
    warnings: List[str] = []

    daily_payload = av_get(
        {"function": "TIME_SERIES_DAILY", "symbol": av_symbol, "outputsize": outputsize},
        apikey, min_interval,
    )
    daily = parse_daily(daily_payload)
    if len(daily) < 60:
        warnings.append(f"only {len(daily)} daily bars — some indicators (SMA50/52w) may be partial")
    elif len(daily) < 240:
        warnings.append(
            f"high_52w/low_52w approximated from {len(daily)} bars (~free-tier compact limit; "
            f"true 52-week range needs outputsize=full, an AV premium feature)"
        )

    fund_pack: Dict[str, Any] = {}
    if want_fundamentals:
        try:
            ov = av_get({"function": "OVERVIEW", "symbol": av_symbol}, apikey, min_interval)
            fund_pack = map_overview(ov)
            if fund_pack.get("_note"):
                warnings.append("fundamentals: " + fund_pack["_note"])
        except DailyBudgetExhausted:
            raise
        except AlphaVantageError as e:
            warnings.append(f"fundamentals skipped: {e}")
    else:
        warnings.append("fundamentals skipped (use --fundamentals; costs 1 AV call/stock)")

    bench_ctx: Dict[str, Any] = {"note": "benchmark skipped to conserve free-tier API calls "
                                         "(enable with --benchmark)"}
    if want_benchmark:
        try:
            bpayload = av_get(
                {"function": "TIME_SERIES_DAILY", "symbol": benchmark_symbol, "outputsize": "compact"},
                apikey, min_interval,
            )
            bbars = parse_daily(bpayload)
            bench_ctx = v1.relative_vs_benchmark(daily[-65:], bbars, benchmark_symbol)
        except DailyBudgetExhausted:
            raise
        except AlphaVantageError as e:
            bench_ctx = {"benchmark": benchmark_symbol, "note": str(e)}

    return build_report(av_symbol, daily, fund_pack, bench_ctx, warnings)


def run_screen(codes: List[str], apikey: str, **kw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in codes:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(analyze_one(raw, apikey, **kw))
        except DailyBudgetExhausted as e:
            LOG.error("Daily AV budget exhausted at '%s' — stopping screen.", raw)
            out.append({"symbol": raw, "error": f"daily_budget_exhausted: {e}"})
            break
        except Exception as e:
            LOG.warning("Screen: %s failed: %s", raw, e)
            out.append({"symbol": raw, "error": str(e)})
    return out


def resolve_apikey(cli_key: Optional[str]) -> str:
    if cli_key:
        return cli_key.strip()
    env = os.environ.get("ALPHAVANTAGE_API_KEY")
    if env:
        return env.strip()
    keyfile = os.path.expanduser("~/.alphavantage_key")
    if os.path.exists(keyfile):
        with open(keyfile) as f:
            k = f.read().strip()
        if k:
            return k
    raise SystemExit(
        "No Alpha Vantage API key. Set one of:\n"
        "  --apikey <KEY>\n"
        "  export ALPHAVANTAGE_API_KEY=<KEY>\n"
        "  echo '<KEY>' > ~/.alphavantage_key\n"
        "Get a free key: https://www.alphavantage.co/support/#api-key  (or use 'demo' for IBM only)"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Swing JSON via Alpha Vantage (Indian market, BSE-routed).")
    p.add_argument("-s", "--symbol", help="e.g. RELIANCE, NSE:TCS, INFY (routes to .BSE)")
    p.add_argument("--screen", help="Comma-separated symbols -> JSON array")
    p.add_argument("--json", action="store_true", help="(default) print structured JSON to stdout")
    p.add_argument("--apikey", default=None, help="Alpha Vantage key (else env / ~/.alphavantage_key)")
    p.add_argument("--fundamentals", dest="fundamentals", action="store_true", default=None,
                   help="Fetch OVERVIEW fundamentals (1 extra AV call). Default: on for -s, off for --screen.")
    p.add_argument("--no-fundamentals", dest="fundamentals", action="store_false",
                   help="Skip fundamentals to save AV calls.")
    p.add_argument("--benchmark", action="store_true", help="Compute relative strength vs benchmark (1 extra AV call).")
    p.add_argument("--benchmark-symbol", default=DEFAULT_BENCHMARK, help=f"default: {DEFAULT_BENCHMARK}")
    p.add_argument("--outputsize", choices=("compact", "full"), default="compact",
                   help="compact (~100 bars; free tier) or full (~20y; AV PREMIUM only). Same 1 call.")
    p.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL,
                   help=f"seconds between AV calls (default {DEFAULT_MIN_INTERVAL}; free tier = 5/min)")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"),
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = p.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S", stream=sys.stderr, force=True,
    )

    apikey = resolve_apikey(args.apikey)
    used = _read_usage()
    LOG.info("→ AV usage so far today: %d/%d", used.get("count", 0), DAILY_FREE_LIMIT)

    if args.screen:
        codes = [c for c in args.screen.split(",") if c.strip()]
        n = len(codes)
        per = 1 + (1 if args.fundamentals else 0) + (1 if args.benchmark else 0)
        LOG.warning("Screening %d symbols ≈ %d AV calls, ~%.0f min (free tier: 25/day, 5/min).",
                    n, n * per, n * per * args.min_interval / 60.0)
        reports = run_screen(
            codes, apikey,
            want_fundamentals=bool(args.fundamentals),  # default off for screen
            want_benchmark=args.benchmark,
            benchmark_symbol=args.benchmark_symbol,
            outputsize=args.outputsize,
            min_interval=args.min_interval,
        )
        print(json.dumps(reports, ensure_ascii=False, indent=2, default=str))
        return

    if not args.symbol:
        p.error("one of -s/--symbol or --screen is required")

    want_fund = True if args.fundamentals is None else args.fundamentals  # default on for single
    try:
        report = analyze_one(
            args.symbol, apikey,
            want_fundamentals=want_fund,
            want_benchmark=args.benchmark,
            benchmark_symbol=args.benchmark_symbol,
            outputsize=args.outputsize,
            min_interval=args.min_interval,
        )
    except DailyBudgetExhausted as e:
        print(json.dumps({"symbol": args.symbol, "error": f"daily_budget_exhausted: {e}"}, indent=2))
        sys.exit(3)
    except AlphaVantageError as e:
        print(json.dumps({"symbol": args.symbol, "error": str(e)}, indent=2))
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
