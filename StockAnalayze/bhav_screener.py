#!/usr/bin/env python3
"""
NSE bhavcopy market-wide short-swing discovery (Stage 1).

Downloads the last few OFFICIAL NSE end-of-day "bhavcopy" files (the entire cash market in one
CSV each, ~2000 EQ stocks) and computes short-swing discovery metrics across the WHOLE market —
5-day return, volume surge, near-recent-high, and liquidity (turnover) — then returns a ranked
shortlist of "liquid movers". This is the discovery stage that feeds the Yahoo deep-dive +
scoring in stock_analyze_shortswing.py, so short-swing candidates are NOT limited to a fixed index.

Source (reachable, official, robust — no fragile 3rd-party screener API):
  https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip

EOD only (files publish after market close). Educational — not financial advice.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger("bhav_screener")

ARCHIVE = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) StockAnalayze-bhav/1.0",
    "Referer": "https://www.nseindia.com/",
    "Accept": "*/*",
}


def _f(v: Any) -> Optional[float]:
    try:
        s = str(v).strip()
        return float(s) if s not in ("", "-", "None") else None
    except (TypeError, ValueError):
        return None


def fetch_bhavcopy(ymd: str, series: str = "EQ") -> Optional[Dict[str, Dict[str, Any]]]:
    """Return {symbol: row} for the given date, or None if that day has no file (weekend/holiday)."""
    url = ARCHIVE.format(ymd=ymd)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            blob = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None
        raise
    except urllib.error.URLError:
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        text = zf.read(zf.namelist()[0]).decode()
    except (zipfile.BadZipFile, IndexError):
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("SctySrs") or "").strip() != series:
            continue
        sym = (row.get("TckrSymb") or "").strip()
        if sym:
            out[sym] = row
    return out or None


def recent_files(want: int, max_back: int, series: str, start: date) -> List[Tuple[str, Dict[str, Any]]]:
    """Collect up to `want` most-recent trading-day bhavcopies (newest first)."""
    files: List[Tuple[str, Dict[str, Any]]] = []
    d = start
    attempts = 0
    while len(files) < want and attempts < max_back:
        if d.weekday() < 5:  # skip weekends without a network call
            ymd = d.strftime("%Y%m%d")
            rows = fetch_bhavcopy(ymd, series)
            if rows:
                files.append((ymd, rows))
                LOG.info("bhavcopy %s: %d %s rows", ymd, len(rows), series)
        d -= timedelta(days=1)
        attempts += 1
    return files


# Per-horizon discovery profiles: default lookback window + how candidates are scored/filtered.
#   shortswing (3-5d): short window, reward fresh breakouts + volume + near recent high.
#   swing (weeks):     longer window, reward sustained trend + near-window high (continuation).
#   intraday:          rank a day-trade WATCHLIST by liquidity x volatility (direction-agnostic).
PROFILES = {
    "shortswing": {"days": 7, "require_up": True},
    "swing": {"days": 30, "require_up": True},
    "intraday": {"days": 10, "require_up": False},
}


def _score(profile: str, *, ret_win_pct: float, vol_surge: Optional[float], near_high: float,
           turnover_cr: float, avg_range_pct: float) -> float:
    vs = min(vol_surge or 1.0, 5.0)
    near_bonus = 5.0 if near_high >= 0.98 else 2.0 if near_high >= 0.95 else 0.0
    if profile == "swing":
        # sustained trend + continuation near the window high; volume secondary
        return round(min(ret_win_pct, 40.0) * 0.6 + near_bonus + 2.0 * vs, 2)
    if profile == "intraday":
        # day-tradeable = liquid AND volatile AND active; momentum direction-agnostic
        return round(min(turnover_cr, 500.0) / 40.0 + 2.5 * avg_range_pct + 1.5 * vs, 2)
    # shortswing (default): fresh breakout momentum + volume, capped vs outliers
    return round(min(ret_win_pct, 25.0) + 3.0 * vs + near_bonus, 2)


def discover(
    *, profile: str = "shortswing", pool: int = 40, min_turnover_cr: float = 10.0,
    days: Optional[int] = None, series: str = "EQ", min_price: float = 0.0,
    start: Optional[date] = None,
) -> Dict[str, Any]:
    """Whole-market Stage-1 shortlist ranked for the given horizon `profile`. Returns dict with
    `candidates`. Profiles: shortswing (3-5d), swing (weeks), intraday (day-trade watchlist)."""
    prof = PROFILES.get(profile, PROFILES["shortswing"])
    days = days or prof["days"]
    start = start or date.today()
    files = recent_files(days, max_back=days * 3 + 10, series=series, start=start)
    if len(files) < 3:
        raise RuntimeError(f"only {len(files)} bhavcopy files fetched — need >=3 (NSE unreachable/holidays?)")

    newest = files[0][1]
    cands: List[Dict[str, Any]] = []
    for sym, row in newest.items():
        last = _f(row.get("ClsPric"))
        prev = _f(row.get("PrvsClsgPric"))
        if last is None or last < min_price:
            continue
        # align this symbol's series across the fetched files (newest -> oldest)
        closes, vols, highs, lows, turns = [], [], [], [], []
        for _, day_rows in files:
            r = day_rows.get(sym)
            if not r:
                continue
            c, v = _f(r.get("ClsPric")), _f(r.get("TtlTradgVol"))
            h, lo, t = _f(r.get("HghPric")), _f(r.get("LwPric")), _f(r.get("TtlTrfVal"))
            if c is not None:
                closes.append(c)
            if v is not None:
                vols.append(v)
            if h is not None:
                highs.append(h)
            if lo is not None:
                lows.append(lo)
            if t is not None:
                turns.append(t)
        if len(closes) < 3 or not turns:
            continue
        turnover_cr = mean(turns) / 1e7  # rupees -> crore
        if turnover_cr < min_turnover_cr:  # liquidity floor
            continue
        oldest_close = closes[-1]
        ret_win_pct = (last / oldest_close - 1) * 100 if oldest_close else 0.0
        vol_last = vols[0] if vols else None
        vol_surge = (vol_last / mean(vols[1:])) if (vol_last and len(vols) > 1 and mean(vols[1:])) else None
        high_win = max(highs) if highs else last
        near_high = last / high_win if high_win else 0.0
        # daily-range volatility proxy (for the intraday watchlist)
        ranges = [(h - lo) / c * 100 for h, lo, c in zip(highs, lows, closes) if c]
        avg_range_pct = mean(ranges) if ranges else 0.0
        day_chg = (last / prev - 1) * 100 if prev else None

        s1 = _score(profile, ret_win_pct=ret_win_pct, vol_surge=vol_surge, near_high=near_high,
                    turnover_cr=turnover_cr, avg_range_pct=avg_range_pct)
        cands.append({
            "symbol": sym,
            "stage1_score": s1,
            "last_close": round(last, 2),
            "day_change_pct": round(day_chg, 2) if day_chg is not None else None,
            "ret_window_pct": round(ret_win_pct, 2),
            "window_days": len(closes),
            "vol_surge": round(vol_surge, 2) if vol_surge else None,
            "near_recent_high": round(near_high, 3),
            "avg_daily_range_pct": round(avg_range_pct, 2),
            "turnover_cr": round(turnover_cr, 1),
        })

    # movers filter: swing/shortswing want positive-window momentum; intraday keeps all (both ways)
    if prof["require_up"]:
        movers = [c for c in cands if c["ret_window_pct"] > 0]
    else:
        movers = list(cands)
    movers.sort(key=lambda c: c["stage1_score"], reverse=True)
    picks = movers[:pool]
    return {
        "discovery": "nse_bhavcopy_market_wide",
        "profile": profile,
        "files_used": [f[0] for f in files],
        "series": series,
        "universe_size": len(newest),
        "passed_liquidity": len(cands),
        "ranked_candidates": len(movers),
        "min_turnover_cr": min_turnover_cr,
        "pool": len(picks),
        "candidates": picks,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="NSE bhavcopy whole-market discovery (Stage 1).")
    p.add_argument("--profile", choices=("shortswing", "swing", "intraday"), default="shortswing",
                   help="horizon profile: shortswing (3-5d, default) | swing (weeks) | intraday (watchlist)")
    p.add_argument("--pool", type=int, default=40, help="how many candidates to shortlist (default 40)")
    p.add_argument("--min-turnover-cr", type=float, default=10.0, help="min avg daily turnover in ₹cr (default 10)")
    p.add_argument("--days", type=int, default=None, help="recent EOD files to use (default: per-profile)")
    p.add_argument("--min-price", type=float, default=0.0, help="skip stocks below this price (default 0)")
    p.add_argument("--series", default="EQ", help="NSE series (default EQ)")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = p.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s | %(levelname)s | %(message)s",
                        datefmt="%H:%M:%S")
    import sys
    res = discover(profile=args.profile, pool=args.pool, min_turnover_cr=args.min_turnover_cr,
                   days=args.days, series=args.series, min_price=args.min_price)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str), file=sys.stdout)


if __name__ == "__main__":
    main()
