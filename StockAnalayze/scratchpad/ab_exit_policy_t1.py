#!/usr/bin/env python3
"""Exit-policy A/B anchored to the NEW practical target ladder (2026-07-31).

For every walk-forward signal (bias long/lop/short, RVOL ok), take the v2 risk_model's
stop + [T1 practical, T2 structural, T3 ceiling] and simulate four exit policies on the
rest of the session (15m bars, conservative same-bar rules):

  FULL_T1   : entire position exits at T1 touch (what a broker target leg at T1 does)
  PART_T1   : 1/3 booked at T1 -> stop to breakeven -> trail 1.5*ATR on bar closes -> EOD
  TRAIL     : no partial; structural stop until +1R -> breakeven + 1.5*ATR trail -> EOD
  FULL_T2   : entire position exits at T2 touch (bracket leg at the structural rung)

All policies square off on the day's last bar. P&L in % of entry, per signal.
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
    CACHE, CHECKPOINTS, HIST_DAYS, bar_time, synth_daily,
)


def sim_full(side, entry, stop, target, fwd):
    for b in fwd:
        if side == "LONG":
            if b.low <= stop:
                return (stop / entry - 1) * 100
            if b.high >= target:
                return (target / entry - 1) * 100
        else:
            if b.high >= stop:
                return (1 - stop / entry) * 100
            if b.low <= target:
                return (1 - target / entry) * 100
    if not fwd:
        return 0.0
    c = fwd[-1].close
    return (c / entry - 1) * 100 if side == "LONG" else (1 - c / entry) * 100


def sim_managed(side, entry, stop0, t1, atr, fwd, book_third_at_t1):
    """1R = |entry-stop0|. Optional 1/3 book at T1 (stop->BE). After +1R: BE + 1.5*ATR trail."""
    sgn = 1 if side == "LONG" else -1
    risk = abs(entry - stop0)
    stop = stop0
    frac = 1.0
    booked = 0.0
    armed = False                       # +1R reached -> trailing active
    for b in fwd:
        lo_hits_stop = b.low <= stop if side == "LONG" else b.high >= stop
        if lo_hits_stop:                # conservative: stop first
            return booked + frac * sgn * (stop / entry - 1) * 100
        if book_third_at_t1 and frac == 1.0:
            t1_hit = b.high >= t1 if side == "LONG" else b.low <= t1
            if t1_hit:
                booked = (1 / 3) * sgn * (t1 / entry - 1) * 100
                frac = 2 / 3
                stop = entry            # runner to breakeven
        profit = sgn * (b.close - entry)
        if not armed and profit >= risk:
            armed = True
            stop = max(stop, entry) if side == "LONG" else min(stop, entry)
        if armed:
            tr = b.close - sgn * 1.5 * atr
            stop = max(stop, tr) if side == "LONG" else min(stop, tr)
    if not fwd:
        return 0.0
    c = fwd[-1].close
    return booked + frac * sgn * (c / entry - 1) * 100


def main():
    cache = pickle.load(open(CACHE, "rb"))
    syms = [s for s in cache if s != "__NIFTY__"]
    out = {"FULL_T1": [], "PART_T1": [], "TRAIL": [], "FULL_T2": []}
    n_sig = 0
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
                bias = rep["intraday_structure"]["directional_bias"]
                side = ("LONG" if bias in ("long", "long-on-pullback")
                        else "SHORT" if bias == "short" else None)
                if side is None:
                    continue
                rvol = rep["volume"]["rvol_vs_prior_days"]
                if rvol is not None and rvol < 0.8:
                    continue
                rm = v2._stop_target_rr(rep, "long" if side == "LONG" else "short")
                tgts = rm.get("targets") or []
                stop = rm.get("suggested_stop")
                last = rep["price"]["last"]
                atr = rep["indicators"]["atr14_intraday"]
                if not tgts or stop is None or not atr:
                    continue
                fwd = day_bars[cp_idx:]
                if not fwd:
                    continue
                t1 = tgts[0]
                t2 = tgts[1] if len(tgts) > 1 else tgts[-1]
                out["FULL_T1"].append(sim_full(side, last, stop, t1, fwd))
                out["FULL_T2"].append(sim_full(side, last, stop, t2, fwd))
                out["PART_T1"].append(sim_managed(side, last, stop, t1, atr, fwd, True))
                out["TRAIL"].append(sim_managed(side, last, stop, t1, atr, fwd, False))
                n_sig += 1
        if (n + 1) % 15 == 0:
            sys.stderr.write(f"{n + 1}/{len(syms)} syms, {n_sig} signals\n")

    print(f"\nEXIT-POLICY A/B on the practical ladder — {n_sig} signals")
    print("=" * 74)
    for k, lab in (("FULL_T1", "full exit at practical T1"),
                   ("FULL_T2", "full exit at structural T2"),
                   ("PART_T1", "1/3 book at T1 + BE + trail"),
                   ("TRAIL", "no partial, +1R BE + 1.5*ATR trail")):
        p = out[k]
        w = sum(1 for x in p if x > 0)
        print(f"  {lab:36} avg {statistics.mean(p):+.3f}%  med {statistics.median(p):+.3f}%  "
              f"win {w / len(p) * 100:3.0f}%  tot {sum(p):+8.1f}%")


if __name__ == "__main__":
    main()
