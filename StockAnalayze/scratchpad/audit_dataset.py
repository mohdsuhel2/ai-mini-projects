#!/usr/bin/env python3
"""Quant-audit dataset for intraday-analyst-2: every checkpoint state (ALL labels, incl.
neutral) with features + forward outcomes. Walk-forward, no lookahead, 66 names Apr-Jul."""
import os
import pickle
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from stock_analyze_intraday import build_report, group_by_day  # noqa: E402
from intraday_hourly_backtest import (  # noqa: E402
    CACHE, CHECKPOINTS, HIST_DAYS, bar_time, synth_daily,
)


def main():
    cache = pickle.load(open(CACHE, "rb"))
    syms = [s for s in cache if s != "__NIFTY__"]
    rows = []
    for n, sym in enumerate(syms):
        m15, daily = cache[sym]["m15"], cache[sym]["daily"]
        days = group_by_day(m15)
        dk = list(days.keys())
        for di in range(10, len(dk)):
            D = dk[di]
            day_bars = days[D]
            if len(day_bars) < 12:
                continue
            hist = [b for k in dk[max(0, di - HIST_DAYS):di] for b in days[k]]
            daily_hist = [b for b in daily if b.date < D]
            for cp in CHECKPOINTS:
                today_now = [b for b in day_bars if bar_time(b) < cp]
                if len(today_now) < 3:
                    continue
                cp_idx = len(today_now)
                try:
                    rep = build_report(sym, f"{sym}.NS", "yahoo_intraday",
                                       hist + today_now, "15m", [],
                                       daily_hist + [synth_daily(D, today_now)], None, None)
                except Exception:
                    continue
                st = rep["intraday_structure"]
                ins = rep["institutional"]
                p = rep["price"]
                brk = rep.get("breakout") or {}
                last = p["last"]
                fwd = day_bars[cp_idx:]
                if not fwd or not last:
                    continue
                eod = fwd[-1].close
                hi = max(b.high for b in fwd)
                lo = min(b.low for b in fwd)
                adx = (ins.get("adx") or {})
                rows.append({
                    "sym": sym, "day": D, "cp": cp,
                    "bias": st["directional_bias"],
                    "daychg": round((last / p["day_open"] - 1) * 100, 2) if p.get("day_open") else None,
                    "pos_range": p.get("position_in_day_range_pct"),
                    "off_high": st.get("pct_off_day_high"),
                    "bars_since_hi": st.get("bars_since_day_high"),
                    "vwap_dist": rep["vwap"].get("distance_pct"),
                    "above_vwap": rep["vwap"].get("above_vwap"),
                    "adx": adx.get("adx"), "di_spread": (adx.get("plus_di") or 0) - (adx.get("minus_di") or 0),
                    "ema_align": ins.get("ema_alignment"),
                    "st_dir": (ins.get("supertrend") or {}).get("direction"),
                    "rsi": rep["indicators"].get("rsi14"),
                    "macd_h": rep["indicators"].get("macd_histogram"),
                    "rvol": rep["volume"].get("rvol_vs_prior_days"),
                    "blowoff": bool(st.get("blowoff_top")),
                    "brk_dir": brk.get("direction"), "brk_fresh": brk.get("fresh"),
                    "brk_ext": brk.get("extended_past_level"),
                    "htf": (rep.get("higher_timeframe") or {}).get("overall_bias"),
                    "fwd_eod": round((eod / last - 1) * 100, 3),
                    "fwd_up": round((hi / last - 1) * 100, 3),
                    "fwd_dn": round((lo / last - 1) * 100, 3),
                })
        if (n + 1) % 15 == 0:
            sys.stderr.write(f"{n + 1}/{len(syms)}: {len(rows)} rows\n")
    pickle.dump(rows, open(os.path.join(HERE, ".audit_rows.pkl"), "wb"))
    print(f"{len(rows)} checkpoint states saved")

    # quick headline stats
    from collections import Counter
    c = Counter(r["bias"] for r in rows)
    print("label distribution:", dict(c))
    longs = [r for r in rows if r["bias"] in ("long", "long-on-pullback")]
    shorts = [r for r in rows if r["bias"] in ("short", "short-on-breakdown")]
    print(f"long labels : n={len(longs)}  med daychg at signal {statistics.median(r['daychg'] for r in longs):+.2f}%  "
          f"med fwd_eod {statistics.median(r['fwd_eod'] for r in longs):+.3f}%  "
          f"neg fwd {sum(1 for r in longs if r['fwd_eod'] < 0)/len(longs)*100:.0f}%")
    print(f"short labels: n={len(shorts)}  med daychg at signal {statistics.median(r['daychg'] for r in shorts):+.2f}%  "
          f"med fwd_eod {statistics.median(r['fwd_eod'] for r in shorts):+.3f}%  "
          f"dn>3% already {sum(1 for r in shorts if r['daychg'] is not None and r['daychg'] < -3)/len(shorts)*100:.0f}%")


if __name__ == "__main__":
    main()
