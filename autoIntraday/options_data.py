"""Option-chain derivatives for the overnight short scan — PCR, max pain, OI walls.

The scan's options dimension carries 12% of its Final Short Score and had no data source, so the
skill was told to null it out. This closes that gap using Groww's option chain via the gateway
(the trade API only accepts whitelisted IPs, so it must go through the VPS like everything else).

CLI so the skill can call it from bash:

    python options_data.py --symbol RELIANCE            # nearest listed expiry
    python options_data.py --symbol RELIANCE --expiry 2026-08-25

Everything derived here is arithmetic over the chain — no interpretation, no scoring. The skill
reads the numbers and decides. If the chain is unavailable the payload says so explicitly rather
than emitting zeros, because zero OI and unknown OI are not the same signal.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Optional

log = logging.getLogger("autointraday.options_data")


def _rows(chain: dict) -> list[dict]:
    """Strike rows out of the chain payload.

    Groww returns {"underlying_ltp": float, "strikes": {<strike>: {...}}} — `strikes` is a DICT
    keyed by strike, not a list. Verified against the live gateway 2026-08-01. A list form is also
    accepted so a payload change does not break this outright.
    """
    strikes = (chain or {}).get("strikes")
    if isinstance(strikes, dict) and strikes:
        out = []
        for k, v in strikes.items():
            if isinstance(v, dict):
                row = dict(v)
                row.setdefault("strike_price", k)      # the key IS the strike
                out.append(row)
        return out
    if isinstance(strikes, list) and strikes:
        return strikes
    for key in ("option_chain", "options", "data", "chain"):
        val = (chain or {}).get(key)
        if isinstance(val, list) and val:
            return val
    return []


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
        if strike is None:                              # dict-keyed payloads carry it as a string
            try:
                strike = float(row.get("strike_price"))
            except (TypeError, ValueError):
                strike = None
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
    thrown, so one missing name cannot abort a whole scan.

    The expiry comes from the instrument master, which is authoritative. Computing it was wrong:
    NSE moved the F&O expiry day, so RELIANCE's August 2026 expiry is Tuesday the 25th, not the
    last Thursday (the 27th) — and a wrong date returns an EMPTY chain rather than an error, which
    is exactly the kind of silent nothing that looks like "no open interest".
    """
    if expiry is None:
        from instrument_master import next_option_expiry
        expiry = next_option_expiry(symbol)
        if expiry is None:
            return {"symbol": symbol.upper(), "expiry": None, "available": False,
                    "note": "no listed option expiry — not an F&O name"}
    result: dict[str, Any] = {"symbol": symbol.upper(), "expiry": expiry}
    try:
        if client is None:
            from groww_client import GrowwClient
            from settings import load_settings
            load_settings()
            client = GrowwClient(mode="live")
            client.ensure_ready()
        chain = client.get_option_chain(symbol.upper(), expiry)
        # The chain carries underlying_ltp, so no second quote call is needed — and a quote can
        # fail out of hours while the chain still returns, which left bearish_tilt uncomputed.
        spot = None
        if isinstance(chain, dict) and isinstance(chain.get("underlying_ltp"), (int, float)):
            spot = float(chain["underlying_ltp"])
        if spot is None:
            try:
                spot = float(client.get_quote(symbol.upper())["ltp"])
            except Exception:
                pass
        result.update(summarise(chain, spot))
    except Exception as e:
        result.update({"available": False, "note": f"{type(e).__name__}: {e}"})
    return result


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Groww option-chain summary (PCR, max pain, OI walls)")
    p.add_argument("--symbol", required=True, help="underlying, e.g. RELIANCE or NIFTY")
    p.add_argument("--expiry", default=None,
                   help="YYYY-MM-DD (default: nearest listed expiry from the instrument master)")
    args = p.parse_args(argv)
    logging.basicConfig(level="WARNING")
    print(json.dumps(fetch(args.symbol, args.expiry), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
