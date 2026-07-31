#!/usr/bin/env python3
"""One-pass A/B for audit fixes 2, 3, 5 (2026-07-31).

Per walk-forward signal (long/lop -> LONG immediate, short -> SHORT immediate; sob excluded
to keep trigger mechanics out of the measurement):
  - simulate the trade (plan_trade structural stop, 2R capped target, square-off)
  - compute the CURRENT v2 desk grade (institutional_desk_block on the report + NIFTY ctx)
  - compute a REBALANCED grade: volume category scored on RVOL bands (side-aware) and the
    higher-timeframe category flattened to 7 (alignment was non-predictive/backwards)
Slices reported:
  Fix 2 — P&L by RVOL band (floor 0.8->1.2 decision)
  Fix 3 — P&L by grade bucket, old vs new scorer (monotonicity = better calibration)
  Fix 5 — shorts: HTF-bullish x topper (daychg +0.5..3) cohort vs the filter's assumption
"""
import os
import pickle
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from stock_analyze_intraday import build_report, group_by_day  # noqa: E402
import stock_analyze_intraday_2 as v2                          # noqa: E402
from intraday_hourly_backtest import (  # noqa: E402
    CACHE, CHECKPOINTS, HIST_DAYS, bar_time, plan_trade, simulate, synth_daily,
)


def new_vol_pts(side, rvol):
    if rvol is None or rvol < 0.8:
        return 0
    if side == "long":
        if rvol >= 5:
            return 6           # crowded parabola: raw-label mean was NEGATIVE
        if rvol >= 1.2:
            return 15
        return 4               # 0.8-1.2: the no-edge majority
    # short: high RVOL is fuel (panic/distribution)
    if rvol >= 2:
        return 15
    if rvol >= 1.2:
        return 10
    return 4


def regrade(desk, side, rvol):
    """New score = old score - old(volume) - old(HTF) + new(volume) + flat 7 HTF."""
    tq = desk.get("trade_quality") or {}
    b = tq.get("breakdown") or {}
    if not b:
        return None
    score = tq["score"] - b.get("volume", 0) - b.get("higher_timeframe", 0) \
        + new_vol_pts(side, rvol) + 7
    return score


def grade_of(score):
    return "A+" if score >= 90 else "A" if score >= 80 else "B" if score >= 70 \
        else "C" if score >= 60 else "F"


def main():
    cache = pickle.load(open(CACHE, "rb"))
    nifty_days = group_by_day(cache["__NIFTY__"]) if "__NIFTY__" in cache else {}
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
                nb = [b for b in nifty_days.get(D, []) if bar_time(b) < cp]
                mkt = {"india_vix": {"note": "unavailable"},
                       "nifty": v2._nifty_ctx(nb) if len(nb) >= 2 else {"note": "unavailable"}}
                try:
                    rep = build_report(sym, f"{sym}.NS", "yahoo_intraday",
                                       hist + today_now, "15m", [],
                                       daily_hist + [synth_daily(D, today_now)], None, mkt)
                except Exception:
                    continue
                bias = rep["intraday_structure"]["directional_bias"]
                if bias not in ("long", "long-on-pullback", "short"):
                    continue
                side = "long" if bias.startswith("long") else "short"
                rvol = rep["volume"]["rvol_vs_prior_days"]
                if rvol is not None and rvol < 0.8:
                    continue                                   # current floor
                last = rep["price"]["last"]
                vwap = rep["vwap"]["vwap"]
                atr = rep["indicators"]["atr14_intraday"]
                proj = rep["projection"]["atr_projected_remaining_move_pct"]
                dopen = rep["price"]["day_open"]
                fwd = day_bars[cp_idx:]
                swing_hi = max(b.high for b in day_bars[max(0, cp_idx - 4):cp_idx])
                plan = plan_trade("LONG" if side == "long" else "SHORT", last, vwap, atr, proj,
                                  swing_hi if side == "short" else None)
                if not plan or not fwd:
                    continue
                stop, target, _ = plan
                res, pnl = simulate("LONG" if side == "long" else "SHORT", last, stop, target, fwd)
                desk = v2.institutional_desk_block(rep)
                tq = desk.get("trade_quality") or {}
                ns = regrade(desk, side, rvol)
                rows.append({"side": side, "rvol": rvol,
                             "daychg": (last / dopen - 1) * 100 if dopen else None,
                             "htf": (rep.get("higher_timeframe") or {}).get("overall_bias"),
                             "old_score": tq.get("score"), "old_grade": tq.get("grade"),
                             "new_grade": grade_of(ns) if ns is not None else None,
                             "res": res, "pnl": round(pnl, 3)})
        if (n + 1) % 15 == 0:
            sys.stderr.write(f"{n + 1}/{len(syms)}: {len(rows)} trades\n")
    pickle.dump(rows, open(os.path.join(HERE, ".ab_fixes_236.pkl"), "wb"))

    def st(sel):
        if len(sel) < 25:
            return f"n={len(sel):5} (thin)"
        p = [r["pnl"] for r in sel]
        w = sum(1 for r in sel if r["res"] == "TARGET")
        return (f"n={len(sel):5}  win {w / len(sel) * 100:3.0f}%  avg {statistics.mean(p):+.3f}%  "
                f"tot {sum(p):+8.1f}%")

    print(f"\n{len(rows)} simulated trades (immediate entries, current 0.8 floor)")
    print("=" * 86)
    print("FIX 2 — P&L by RVOL band:")
    for side in ("long", "short"):
        sel = [r for r in rows if r["side"] == side]
        print(f" {side.upper()}:")
        for lo, hi, lab in ((0.8, 1.2, "0.8-1.2"), (1.2, 2, "1.2-2"), (2, 5, "2-5"), (5, 999, "5+")):
            print(f"   rvol {lab:8}", st([r for r in sel if r["rvol"] is not None and lo <= r["rvol"] < hi]))
    print("\nFIX 3 — P&L by grade, OLD vs NEW scorer:")
    for g in ("A+", "A", "B", "C", "F"):
        print(f"   grade {g:2}  OLD {st([r for r in rows if r['old_grade'] == g]):48}")
        print(f"            NEW {st([r for r in rows if r['new_grade'] == g])}")
    print("\nFIX 5 — SHORTS: HTF-bullish x day-change:")
    Ssel = [r for r in rows if r["side"] == "short"]
    bull = [r for r in Ssel if r["htf"] and "bullish" in r["htf"]]
    print("   all shorts              ", st(Ssel))
    print("   htf-bullish shorts      ", st(bull))
    print("   htf-bullish TOPPER (+0.5..3%)",
          st([r for r in bull if r["daychg"] is not None and 0.5 <= r["daychg"] <= 3]))
    print("   htf-bullish non-topper  ",
          st([r for r in bull if r["daychg"] is None or not (0.5 <= r["daychg"] <= 3)]))
    print("   htf-bearish shorts      ", st([r for r in Ssel if r["htf"] and "bearish" in r["htf"]]))


if __name__ == "__main__":
    main()
