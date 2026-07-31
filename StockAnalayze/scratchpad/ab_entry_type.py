#!/usr/bin/env python3
"""A/B: IMMEDIATE entry vs VWAP-LIMIT (pullback) entry for engine LONG signals,
sliced by how extended the price is above VWAP at signal time and by climax flags.

Question (from the 2026-07-30 post-mortem): when the engine says LONG but price is
extended above VWAP (SYRMA +2.0% -> chased, stopped; BLACKBUCK limit at VWAP -> target),
should the verdict be BUY ON PULLBACK (limit at VWAP) instead of BUY NOW?

Policy A (immediate): enter at the checkpoint's closed-bar price.
Policy B (limit):     rest a limit at the running VWAP; fill only if a later 15m bar
                      trades down to it before 14:45; NO-FILL = flat (pnl 0).
Same structural-stop / 2R-capped-target / square-off exits both sides.
Per-SIGNAL expectancy (no-fills count as 0) decides — that is the honest comparison.
"""
import os
import pickle
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from stock_analyze_intraday import build_report, group_by_day  # noqa: E402
from intraday_hourly_backtest import (  # noqa: E402
    CACHE, CHECKPOINTS, HIST_DAYS, bar_time, plan_trade, simulate, synth_daily,
)

LAST_FILL = "14:45"


def run():
    cache = pickle.load(open(CACHE, "rb"))
    syms = [s for s in cache if s != "__NIFTY__"]
    sigs = []
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
                if st["directional_bias"] not in ("long", "long-on-pullback"):
                    continue
                rvol = rep["volume"]["rvol_vs_prior_days"]
                if rvol is not None and rvol < 0.8:
                    continue
                brk = rep.get("breakout") or {}
                if brk.get("direction") == "up" and brk.get("extended_past_level") and not brk.get("fresh"):
                    continue
                last = rep["price"]["last"]
                vwap = rep["vwap"]["vwap"]
                atr = rep["indicators"]["atr14_intraday"]
                proj = rep["projection"]["atr_projected_remaining_move_pct"]
                if not vwap or not atr:
                    continue
                vdist = (last / vwap - 1) * 100
                fwd = day_bars[cp_idx:]

                plan = plan_trade("LONG", last, vwap, atr, proj)
                if not plan:
                    continue
                stop, target, _ = plan
                ires, ipnl = simulate("LONG", last, stop, target, fwd)

                # policy B — limit at VWAP
                lres, lpnl = "NOFILL", 0.0
                if last <= vwap:                     # already at/below VWAP: same as immediate
                    lres, lpnl = ires, ipnl
                else:
                    for j in range(cp_idx, len(day_bars)):
                        b = day_bars[j]
                        if bar_time(b) >= LAST_FILL:
                            break
                        if b.low <= vwap:
                            lplan = plan_trade("LONG", vwap, vwap, atr, proj)
                            if lplan:
                                ls, lt, _ = lplan
                                lres, lpnl = simulate("LONG", vwap, ls, lt, day_bars[j:])
                            break

                sigs.append({"sym": sym, "day": D, "cp": cp,
                             "bias": st["directional_bias"], "vdist": vdist,
                             "blowoff": bool(st.get("blowoff_top")),
                             "climax": (st.get("volume_climax_ratio") or 0) >= 2.5,
                             "fresh_bo": bool(brk.get("fresh")) and brk.get("direction") == "up",
                             "ires": ires, "ipnl": round(ipnl, 2),
                             "lres": lres, "lpnl": round(lpnl, 2)})
        if (n + 1) % 10 == 0:
            sys.stderr.write(f"{n + 1}/{len(syms)} syms, {len(sigs)} signals\n")
    pickle.dump(sigs, open(os.path.join(HERE, ".ab_entry_type.pkl"), "wb"))
    return sigs


def row(ts, label):
    if not ts:
        return f"  {label:26} n=0"
    def side(res_key, pnl_key):
        w = sum(1 for t in ts if t[res_key] == "TARGET")
        nf = sum(1 for t in ts if t[res_key] == "NOFILL")
        pnls = [t[pnl_key] for t in ts]
        return (f"win {w/len(ts)*100:3.0f}% avg {statistics.mean(pnls):+.3f}% "
                f"tot {sum(pnls):+7.1f}%" + (f" (nofill {nf/len(ts)*100:.0f}%)" if nf else ""))
    return (f"  {label:26} n={len(ts):4}  IMM: {side('ires','ipnl')}   LIMIT: {side('lres','lpnl')}")


def main():
    pkl = os.path.join(HERE, ".ab_entry_type.pkl")
    sigs = pickle.load(open(pkl, "rb")) if os.path.exists(pkl) else run()
    print(f"\nLONG signals (all checkpoints, {len(sigs)} total) — IMMEDIATE vs VWAP-LIMIT entry")
    print("=" * 118)
    print(row(sigs, "ALL"))
    print("\nby extension above VWAP at signal:")
    for lo, hi, lab in ((-99, 0.0, "at/below VWAP"), (0.0, 0.5, "0-0.5%"),
                        (0.5, 1.0, "0.5-1%"), (1.0, 2.0, "1-2%"), (2.0, 99, ">2%")):
        print(row([t for t in sigs if lo <= t["vdist"] < hi], f"vwap_dist {lab}"))
    print("\nby climax state at signal:")
    print(row([t for t in sigs if t["blowoff"] or t["climax"]], "blowoff/climax flagged"))
    print(row([t for t in sigs if not (t["blowoff"] or t["climax"])], "clean"))
    print("\nfresh up-breakout at signal:")
    print(row([t for t in sigs if t["fresh_bo"]], "fresh breakout"))
    print(row([t for t in sigs if not t["fresh_bo"]], "no fresh breakout"))
    print("\nextended >1% AND flagged (the SYRMA-shape):")
    print(row([t for t in sigs if t["vdist"] >= 1.0 and (t["blowoff"] or t["climax"])], "ext+flagged"))
    print(row([t for t in sigs if t["vdist"] >= 1.0 and not (t["blowoff"] or t["climax"])], "ext, clean"))


if __name__ == "__main__":
    main()
