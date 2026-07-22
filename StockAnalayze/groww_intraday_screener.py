#!/usr/bin/env python3
"""
Groww intraday movers screener — live candidate pool for the `intraday-analyst` skill.

Fetches https://groww.in/stocks/intraday (Groww's ~100 most-traded / intraday-eligible NSE stocks) and
returns today's movers with live LTP, intraday change %, a volume ratio (volume vs weekly-average), a
market-cap size band, and Groww's own "In news" tag. Unlike `bhav_screener.py` (EOD bhavcopy, whole
market), this is a LIVE intraday list — the actual names in play *right now* — so it feeds the intraday
skill's TOP-N with today's real movers instead of an EOD-selected liquid pool.

Data lives in the page's Next.js `__NEXT_DATA__` blob at `props.pageProps.data` (no auth needed).
Output is clean JSON: a ranked `picks` array of {symbol, ltp, change_pct, vol_ratio, in_news, mcap_cr}.
Feed `symbol` (nseScriptCode) straight into stock_analyze_intraday.py for the full engine.

Educational tooling, not financial advice.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from typing import Any, Dict, List, Optional

GROWW_URL = "https://groww.in/stocks/intraday"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_NEXT = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def fetch_groww_intraday(timeout: int = 30) -> List[Dict[str, Any]]:
    """Fetch the Groww intraday page and return the raw stock list from __NEXT_DATA__."""
    req = urllib.request.Request(GROWW_URL, headers={"User-Agent": _UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "replace")
    m = _NEXT.search(html)
    if not m:
        raise RuntimeError("Groww page layout changed — __NEXT_DATA__ not found")
    blob = json.loads(m.group(1))
    data = (blob.get("props", {}).get("pageProps", {}) or {}).get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("Groww intraday list empty / moved in page JSON")
    return data


def _row(x: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sym = x.get("nseScriptCode")
    ltp, close = x.get("ltp"), x.get("close")
    if not sym or not ltp or not close:
        return None
    chg = (ltp / close - 1) * 100
    vwa = x.get("volumeWeekAvg") or 0
    vol_ratio = round((x.get("volume") or 0) / vwa, 2) if vwa else None
    mcap_cr = round(x.get("marketCap"), 0) if x.get("marketCap") else None  # already in ₹ crore
    return {
        "symbol": sym, "name": x.get("shortName"),
        "ltp": round(ltp, 2), "prev_close": round(close, 2),
        "change_pct": round(chg, 2),
        "vol_ratio": vol_ratio,                    # today's volume vs weekly avg (NOT true RVOL)
        "in_news": (x.get("tag") == "In news"),
        "mcap_cr": mcap_cr,
        "searchId": x.get("searchId"),
    }


def screen(direction: str = "both", top: int = 15, min_vol_ratio: float = 0.0,
           min_price: float = 0.0, min_mcap_cr: float = 0.0,
           min_change: float = 0.0) -> Dict[str, Any]:
    """Rank Groww's intraday list into movers. direction: up / down / both."""
    raw = fetch_groww_intraday()
    rows = [r for r in (_row(x) for x in raw) if r]
    scanned = len(rows)
    # filters
    rows = [r for r in rows if r["ltp"] >= min_price
            and (r["mcap_cr"] or 0) >= min_mcap_cr
            and (r["vol_ratio"] or 0) >= min_vol_ratio
            and abs(r["change_pct"]) >= min_change]
    if direction == "up":
        rows = [r for r in rows if r["change_pct"] > 0]
        rows.sort(key=lambda r: -r["change_pct"])
    elif direction == "down":
        rows = [r for r in rows if r["change_pct"] < 0]
        rows.sort(key=lambda r: r["change_pct"])
    else:
        rows.sort(key=lambda r: -abs(r["change_pct"]))
    return {
        "source": "groww_intraday", "url": GROWW_URL,
        "note": ("LIVE intraday movers from Groww's most-traded list. vol_ratio = today's volume vs "
                 "weekly avg (a liquidity/participation proxy, not the intraday RVOL the engine computes). "
                 "Feed `symbol` into stock_analyze_intraday.py for the full 20-step read."),
        "direction": direction, "universe_scanned": scanned,
        "picks": rows[:top],
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Groww intraday movers screener (live candidate pool).")
    p.add_argument("--direction", default="both", choices=("up", "down", "both"),
                   help="up = gainers (long ideas), down = losers (short ideas), both = biggest movers")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-vol-ratio", type=float, default=0.0, help="min today-vol / weekly-avg-vol")
    p.add_argument("--min-price", type=float, default=0.0, help="drop sub-₹X names (penny filter)")
    p.add_argument("--min-mcap-cr", type=float, default=0.0, help="min market cap in ₹ crore")
    p.add_argument("--min-change", type=float, default=0.0, help="min abs intraday change %")
    args = p.parse_args()
    try:
        out = screen(direction=args.direction, top=args.top, min_vol_ratio=args.min_vol_ratio,
                     min_price=args.min_price, min_mcap_cr=args.min_mcap_cr, min_change=args.min_change)
    except Exception as e:
        print(json.dumps({"error": f"groww fetch failed: {e}", "source": "groww_intraday"}))
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
