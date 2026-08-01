"""Option-chain derivatives for the overnight short scan — PCR, max pain, OI walls.

The scan's options dimension carries 12% of its Final Short Score and had no data source, so the
skill was told to null it out. This closes that gap using Groww's option chain via the gateway
(the trade API only accepts whitelisted IPs, so it must go through the VPS like everything else).

CLI so the skill can call it from bash:

    python options_data.py --symbol RELIANCE            # next monthly expiry
    python options_data.py --symbol RELIANCE --expiry 2026-08-27

Everything derived here is arithmetic over the chain — no interpretation, no scoring. The skill
reads the numbers and decides. If the chain is unavailable the payload says so explicitly rather
than emitting zeros, because zero OI and unknown OI are not the same signal.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from typing import Any, Optional

log = logging.getLogger("autointraday.options_data")


def last_thursday(year: int, month: int) -> date:
    """NSE monthly expiry is the last Thursday of the month.

    Exchange holidays move it to the previous trading day; that is not modelled here, so treat the
    result as the nominal expiry and let a rejected chain request tell you it moved.
    """
    d = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != 3:                     # Thursday
        d -= timedelta(days=1)
    return d


def next_monthly_expiry(today: Optional[date] = None) -> str:
    """The nearest monthly expiry that has not passed, 'YYYY-MM-DD'."""
    today = today or date.today()
    this_month = last_thursday(today.year, today.month)
    if this_month >= today:
        return this_month.isoformat()
    nxt = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    return last_thursday(nxt.year, nxt.month).isoformat()


def _rows(chain: dict) -> list[dict]:
    """Strike rows out of the chain payload, tolerating the key names Groww may use."""
    for key in ("option_chain", "options", "data", "chain", "strikes"):
        val = (chain or {}).get(key)
        if isinstance(val, list) and val:
            return val
    return [v for v in (chain or {}).values() if isinstance(v, list) and v] and \
        next((v for v in chain.values() if isinstance(v, list) and v), [])


def _leg(row: dict, side: str) -> dict:
    """The CE or PE leg of a strike row, whatever nesting the payload uses."""
    for key in (side, side.lower(), f"{side.lower()}_option", f"{side.lower()}_data"):
        val = row.get(key)
        if isinstance(val, dict):
            return val
    return row if str(row.get("option_type", "")).upper() == side else {}


def _num(d: dict, *names) -> Optional[float]:
    for n in names:
        v = d.get(n)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def summarise(chain: dict, spot: Optional[float] = None) -> dict[str, Any]:
    """PCR, max pain and the heaviest OI strikes. Pure arithmetic over the chain."""
    rows = _rows(chain)
    if not rows:
        return {"available": False, "note": "option chain empty or in an unrecognised shape"}

    strikes: list[dict] = []
    for row in rows:
        strike = _num(row, "strike_price", "strike", "strikePrice")
        ce, pe = _leg(row, "CE"), _leg(row, "PE")
        ce_oi = _num(ce, "open_interest", "oi", "openInterest") or 0.0
        pe_oi = _num(pe, "open_interest", "oi", "openInterest") or 0.0
        if strike is None or (ce_oi == 0 and pe_oi == 0):
            continue
        strikes.append({"strike": strike, "call_oi": ce_oi, "put_oi": pe_oi,
                        "call_volume": _num(ce, "volume", "traded_volume") or 0.0,
                        "put_volume": _num(pe, "volume", "traded_volume") or 0.0})
    if not strikes:
        return {"available": False, "note": "no strike carried open interest"}

    strikes.sort(key=lambda s: s["strike"])
    call_oi = sum(s["call_oi"] for s in strikes)
    put_oi = sum(s["put_oi"] for s in strikes)

    # Max pain: the strike where total intrinsic value paid out to holders is smallest.
    pain = []
    for probe in strikes:
        k = probe["strike"]
        loss = sum(s["call_oi"] * max(0.0, k - s["strike"]) +
                   s["put_oi"] * max(0.0, s["strike"] - k) for s in strikes)
        pain.append((loss, k))
    max_pain = min(pain)[1]

    top_call = sorted(strikes, key=lambda s: s["call_oi"], reverse=True)[:3]
    top_put = sorted(strikes, key=lambda s: s["put_oi"], reverse=True)[:3]

    out: dict[str, Any] = {
        "available": True,
        "strikes_analysed": len(strikes),
        "total_call_oi": call_oi,
        "total_put_oi": put_oi,
        # PCR < 1 means calls dominate — call writers expect price to stay below those strikes,
        # which is a bearish tilt. Read it alongside where the OI actually sits.
        "pcr_oi": round(put_oi / call_oi, 3) if call_oi else None,
        "max_pain": max_pain,
        "heaviest_call_oi_strikes": [s["strike"] for s in top_call],
        "heaviest_put_oi_strikes": [s["strike"] for s in top_put],
        "call_wall": top_call[0]["strike"] if top_call else None,
        "put_wall": top_put[0]["strike"] if top_put else None,
    }
    if spot:
        out["spot"] = spot
        out["max_pain_vs_spot_pct"] = round((max_pain - spot) / spot * 100, 2)
        out["bearish_tilt"] = bool(max_pain < spot)   # max pain below spot pulls price down
    return out


def fetch(symbol: str, expiry: Optional[str] = None, client=None) -> dict[str, Any]:
    """Chain summary for one underlying. Never raises — an unavailable chain is reported, not
    thrown, so one missing name cannot abort a whole scan."""
    expiry = expiry or next_monthly_expiry()
    result: dict[str, Any] = {"symbol": symbol.upper(), "expiry": expiry}
    try:
        if client is None:
            from groww_client import GrowwClient
            from settings import load_settings
            load_settings()
            client = GrowwClient(mode="live")
            client.ensure_ready()
        spot = None
        try:
            spot = float(client.get_quote(symbol.upper())["ltp"])
        except Exception:
            pass
        chain = client.get_option_chain(symbol.upper(), expiry)
        result.update(summarise(chain, spot))
    except Exception as e:
        result.update({"available": False, "note": f"{type(e).__name__}: {e}"})
    return result


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Groww option-chain summary (PCR, max pain, OI walls)")
    p.add_argument("--symbol", required=True, help="underlying, e.g. RELIANCE or NIFTY")
    p.add_argument("--expiry", default=None, help="YYYY-MM-DD (default: next monthly expiry)")
    args = p.parse_args(argv)
    logging.basicConfig(level="WARNING")
    print(json.dumps(fetch(args.symbol, args.expiry), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
