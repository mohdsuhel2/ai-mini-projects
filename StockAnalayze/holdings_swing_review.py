#!/usr/bin/env python3
"""Fetch Groww delivery HOLDINGS (read-only) and run the swing engine on each, so the
swing-analyst can classify keep / add / trim-trail / exit per holding.

REUSES the autoIntraday GrowwClient (same auth the user already uses). This script is
STRICTLY READ-ONLY: it calls get_holdings()/get_ltp() only — it NEVER places, modifies,
or cancels an order. Credentials come from GROWW_API_KEY + GROWW_TOTP_SECRET in the
environment (via autoIntraday's config.yaml ${VAR} placeholders). Nothing is printed
that reveals a secret.

Run (from a shell where the two env vars are set):
  ~/ai-mini-projects/autoIntraday/.venv/bin/python \
      ~/ai-mini-projects/StockAnalayze/holdings_swing_review.py

Or skip the live fetch and pass holdings manually (symbol:qty:avg_price, comma-sep):
  ... holdings_swing_review.py --holdings "TCS:10:3500,GABRIEL:50:900"
"""
from __future__ import annotations
import os, sys, json, subprocess, argparse

AUTO = os.path.expanduser("~/ai-mini-projects/autoIntraday")
SA = os.path.expanduser("~/ai-mini-projects/StockAnalayze")
SA_PY = f"{SA}/.venv/bin/python"
SA_SCRIPT = f"{SA}/stock_analyze.py"


def fetch_holdings_live() -> list[dict]:
    sys.path.insert(0, AUTO)
    try:
        from settings import load_settings          # type: ignore
        from groww_client import GrowwClient         # type: ignore
    except Exception as e:
        raise SystemExit(f"could not import autoIntraday modules from {AUTO}: {e}")
    cfg = os.path.join(AUTO, "config.yaml")
    if os.path.exists(cfg):
        load_settings(cfg).apply_to_environ()        # resolve ${VAR} -> os.environ
    if not os.environ.get("GROWW_API_KEY") or not os.environ.get("GROWW_TOTP_SECRET"):
        raise SystemExit("GROWW_API_KEY and GROWW_TOTP_SECRET must be set in the environment "
                         "(export them, then re-run). This script only reads holdings.")
    client = GrowwClient(mode="live")
    client.authenticate()
    holdings = client.get_holdings()                 # [{symbol, quantity, avg_price}] read-only
    syms = [h["symbol"] for h in holdings]
    ltp = {}
    try:
        ltp = client.get_ltp(syms) if syms else {}
    except Exception:
        pass                                         # LTP is a nice-to-have; the screener has price too
    for h in holdings:
        h["ltp"] = ltp.get(h["symbol"])
    return holdings


def parse_manual(spec: str) -> list[dict]:
    out = []
    for part in spec.split(","):
        bits = part.strip().split(":")
        if len(bits) >= 3:
            out.append({"symbol": bits[0].upper(), "quantity": int(float(bits[1])),
                        "avg_price": float(bits[2]), "ltp": None})
    return out


def run_screen(symbols: list[str]) -> dict:
    if not symbols:
        return {}
    out = subprocess.run([SA_PY, SA_SCRIPT, "--screen", ",".join(symbols)],
                         capture_output=True, text=True, timeout=600)
    try:
        arr = json.loads(out.stdout)
    except Exception:
        raise SystemExit(f"screen failed to return JSON. stderr tail:\n{out.stderr[-800:]}")
    by = {}
    for r in (arr if isinstance(arr, list) else []):
        sym = (r.get("meta") or {}).get("yahoo_symbol") or r.get("symbol") or r.get("ticker")
        # the screen echoes the requested code; fall back to matching on 'requested'/'code'
        key = r.get("requested") or r.get("code") or sym
        if key:
            by[str(key).replace(".NS", "").upper()] = r
    return by


def classify(h: dict, r: dict) -> dict:
    """First-pass bucket. The analyst refines this with catalysts + judgment."""
    p = r.get("price", {}) or {}; ss = r.get("swing_signals", {}) or {}
    eq = r.get("entry_quality", {}) or {}; ind = r.get("indicators", {}) or {}
    last = p.get("last") or h.get("ltp")
    avg = h.get("avg_price")
    pnl = round((last / avg - 1) * 100, 1) if (last and avg) else None
    trend_up = ss.get("trend") == "up"
    above50 = ss.get("above_sma50")
    grade = eq.get("entry_grade")
    blocked = grade in {"distribution-risk", "overbought-into-resistance", "extended-no-volume", "into-resistance"}
    # buckets
    if not trend_up and above50 is False:
        bucket = "EXIT"                              # trend broken, below SMA50
    elif eq.get("distribution_risk"):
        bucket = "TRIM/TRAIL"                        # distribution — protect the gain
    elif eq.get("dip_buy"):
        bucket = "ADD (dip)"                         # validated buy-the-dip
    elif trend_up and eq.get("volume_ok") and not blocked and not eq.get("extended") \
            and not eq.get("low_volatility_grinder"):
        bucket = "ADD (momentum)"
    elif trend_up and (blocked or eq.get("extended") or eq.get("volume_drying")):
        bucket = "HOLD/TRAIL"                        # winner but extended/into-resistance
    elif eq.get("low_volatility_grinder"):
        bucket = "HOLD (grinder)"
    else:
        bucket = "HOLD"
    return {"symbol": h["symbol"], "qty": h.get("quantity"), "avg": avg, "ltp": last, "pnl_pct": pnl,
            "bucket": bucket, "trend": ss.get("trend"), "above_sma50": above50,
            "entry_grade": grade, "distribution_risk": eq.get("distribution_risk"),
            "dip_buy": eq.get("dip_buy"), "volume_ok": eq.get("volume_ok"),
            "extended": eq.get("extended"), "into_resistance": eq.get("into_resistance"),
            "low_vol_grinder": eq.get("low_volatility_grinder"), "atr_pct": eq.get("atr_pct"),
            "headroom_pct": eq.get("headroom_to_resistance_pct"),
            "rsi": ind.get("rsi14"), "macd_hist": ind.get("macd_histogram"),
            "adx_proxy": ss.get("adx_proxy"), "res20": p.get("resistance_20d"),
            "sup20": p.get("support_20d"), "hi52": p.get("high_52w"),
            "regime": (r.get("market_regime") or {}).get("regime"),
            "bench_excess": (r.get("benchmark") or {}).get("excess_return_vs_benchmark_pct")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdings", help="manual 'SYM:qty:avg,...' (skips live fetch)")
    a = ap.parse_args()
    holdings = parse_manual(a.holdings) if a.holdings else fetch_holdings_live()
    if not holdings:
        raise SystemExit("no holdings found.")
    sys.stderr.write(f"holdings fetched: {len(holdings)} — running swing screen...\n")
    by = run_screen([h["symbol"] for h in holdings])
    rows = []
    for h in holdings:
        r = by.get(h["symbol"].replace(".NS", "").upper(), {})
        rows.append(classify(h, r) if r else {"symbol": h["symbol"], "qty": h.get("quantity"),
                    "avg": h.get("avg_price"), "ltp": h.get("ltp"), "bucket": "NO_DATA"})
    order = {"EXIT": 0, "TRIM/TRAIL": 1, "HOLD/TRAIL": 2, "HOLD": 3, "HOLD (grinder)": 4,
             "ADD (dip)": 5, "ADD (momentum)": 6, "NO_DATA": 7}
    rows.sort(key=lambda x: order.get(x.get("bucket"), 9))
    print(json.dumps({"count": len(rows), "holdings": rows}, indent=2, default=str))


if __name__ == "__main__":
    main()
