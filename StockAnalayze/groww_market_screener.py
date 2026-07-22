#!/usr/bin/env python3
"""Groww market-screener scraper (Stage-1 discovery for swing / short-swing).

Fetches Groww's pre-built market screens and returns a clean ranked `picks` JSON whose
`symbol` (nseScriptCode) feeds straight into stock_analyze.py / stock_analyze_shortswing.py.

The two high-value screens (validated against our backtests):
  volume-shockers -> unusual-volume / catalyst-likely names (feeds entry_quality.volume_spike_up
                     + the 'In news' tag) — the catalyst-discovery the momentum bhav screen misses.
  top-losers      -> today's pullbacks; Stage-2 confirms which are dip_buy (uptrend intact + oversold).
Also supports top-gainers / 52-week-high / 52-week-low / top-volume for completeness.

EOD/live figures come from Groww's page state; use only to PICK the pool — the analysis scripts
compute the authoritative signals. Default index GIDXNIFTYTOTALMCAP = whole market.
"""
import argparse
import json
import re
import sys
import urllib.request
from typing import Any, Dict, List, Optional

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
SCREENS = {
    "volume-shockers": "https://groww.in/markets/volume-shockers",
    "top-gainers": "https://groww.in/markets/top-gainers",
    "top-losers": "https://groww.in/markets/top-losers",
    "52-week-high": "https://groww.in/markets/52-week-high",
    "52-week-low": "https://groww.in/markets/52-week-low",
    "top-volume": "https://groww.in/markets/top-volume",
}


def fetch_screen(screen: str, index: str, timeout: int = 30) -> List[Dict[str, Any]]:
    url = SCREENS[screen]
    if screen != "volume-shockers":            # volume-shockers is index-agnostic on Groww
        url += f"?index={urllib.parse.quote(index)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "ignore")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("Groww page shape changed — __NEXT_DATA__ not found")
    data = json.loads(m.group(1))
    return data.get("props", {}).get("pageProps", {}).get("stocks", []) or []


def _row(x: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sym = x.get("nseScriptCode")
    ltp = x.get("ltp")
    if not sym or ltp is None:
        return None
    close = x.get("close")               # prior close
    vol = x.get("volume")
    vwa = x.get("volumeWeekAvg")
    yhigh, ylow = x.get("yearHigh"), x.get("yearLow")
    mcap = x.get("marketCap") or 0
    return {
        "symbol": sym,
        "name": x.get("companyShortName") or x.get("companyName"),
        "ltp": ltp,
        "day_change_pct": round((ltp / close - 1) * 100, 2) if close else None,
        "vol_ratio": round(vol / vwa, 1) if (vol and vwa) else None,   # today's vol vs weekly avg (catalyst proxy)
        "in_news": (x.get("tag") == "In news"),
        "pct_from_52w_high": round((ltp / yhigh - 1) * 100, 2) if yhigh else None,
        "pct_from_52w_low": round((ltp / ylow - 1) * 100, 2) if ylow else None,
        "mcap_cr": round(mcap / 1e7) if mcap else None,
    }


def screen(name: str, index: str, top: int, min_price: float, min_mcap_cr: float) -> Dict[str, Any]:
    raw = fetch_screen(name, index)
    rows = [r for r in (_row(x) for x in raw) if r]
    rows = [r for r in rows if r["ltp"] >= min_price]
    if min_mcap_cr > 0:
        rows = [r for r in rows if (r["mcap_cr"] or 0) >= min_mcap_cr]
    # rank: volume screens by vol_ratio; gainers/losers by |day change|; 52w by proximity
    if name in ("volume-shockers", "top-volume"):
        rows.sort(key=lambda r: (r["vol_ratio"] or 0), reverse=True)
    elif name == "top-losers":
        rows.sort(key=lambda r: (r["day_change_pct"] if r["day_change_pct"] is not None else 0))
    else:
        rows.sort(key=lambda r: (r["day_change_pct"] if r["day_change_pct"] is not None else 0), reverse=True)
    picks = rows[:top]
    hint = {
        "volume-shockers": "unusual volume = catalyst-likely -> feeds volume_spike_up; WebSearch in_news names",
        "top-losers": "today's pullbacks -> feed to Stage-2; dip_buy fires on the ones still in an uptrend + oversold",
    }.get(name, "momentum/level screen")
    return {
        "source": "groww_market", "screen": name, "index": index,
        "note": f"LIVE from Groww. {hint}. Feed picks[].symbol into stock_analyze(_shortswing).py.",
        "count": len(picks), "picks": picks,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Groww market-screener discovery (Stage 1).")
    p.add_argument("--screen", choices=sorted(SCREENS), default="volume-shockers")
    p.add_argument("--index", default="GIDXNIFTYTOTALMCAP", help="Groww index code (default whole market)")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-price", type=float, default=50.0)
    p.add_argument("--min-mcap-cr", type=float, default=0.0, help="min market cap in ₹cr (0 = off; Groww mcap often absent)")
    a = p.parse_args()
    try:
        out = screen(a.screen, a.index, a.top, a.min_price, a.min_mcap_cr)
    except Exception as e:
        print(json.dumps({"error": f"groww market fetch failed: {e}", "source": "groww_market", "screen": a.screen}))
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
