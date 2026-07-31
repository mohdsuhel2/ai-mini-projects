#!/usr/bin/env python3
"""Deep-dive: are intraday-analyst-2 targets ACHIEVABLE?

Walk-forward over the cached 66-name / Apr-Jul 15m set. At every checkpoint signal
(bias long/lop -> LONG, short -> SHORT) build the v1 report, run v2's ACTUAL
_stop_target_rr() on it, and measure against the rest of the session:

  - distance to T1/T2/T3 vs the realized max favourable excursion (MFE)
  - P(T1 hit), P(T1 hit BEFORE the suggested stop), same for T2/T3
  - the same for capped T1 alternatives:
       cap_proj  = min(T1, last + proj)          (Gate F applied to T1, as the skill text claims)
       cap_1atr  = min(T1, last + 1.0*ATR)
       cap_hproj = min(T1, last + 0.5*proj)
  (mirrors for shorts)
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


def walk(side, entry, stop, level, fwd):
    """Did `level` get touched, and did it get touched BEFORE the stop? (same-bar -> stop first)"""
    hit = hit_before_stop = False
    stopped = False
    for b in fwd:
        if side == "LONG":
            stop_now = b.low <= stop
            hit_now = b.high >= level
        else:
            stop_now = b.high >= stop
            hit_now = b.low <= level
        if hit_now:
            hit = True
            if not stopped and not stop_now:
                hit_before_stop = True
            break
        if stop_now:
            stopped = True
    return hit, hit_before_stop


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
                if not tgts or stop is None:
                    continue
                last = rep["price"]["last"]
                atr = rep["indicators"]["atr14_intraday"] or 0
                proj = rep["projection"]["atr_projected_remaining_move_pts"] or 2 * atr
                fwd = day_bars[cp_idx:]
                if not fwd or not atr:
                    continue
                sgn = 1 if side == "LONG" else -1
                mfe = (max(b.high for b in fwd) / last - 1) * 100 if side == "LONG" \
                    else (1 - min(b.low for b in fwd) / last) * 100

                t1 = tgts[0]
                alts = {"current": t1,
                        "cap_proj": min(t1, last + proj) if side == "LONG" else max(t1, last - proj),
                        "cap_1atr": min(t1, last + atr) if side == "LONG" else max(t1, last - atr),
                        "cap_hproj": (min(t1, last + 0.5 * proj) if side == "LONG"
                                      else max(t1, last - 0.5 * proj))}
                r = {"sym": sym, "day": D, "cp": cp, "side": side,
                     "blue_sky": bool(rm.get("ladder_spent_blue_sky")),
                     "mfe": round(mfe, 2), "proj_pct": round(proj / last * 100, 2),
                     "risk_pct": round(abs(last - stop) / last * 100, 2)}
                for i, t in enumerate(tgts[:3]):
                    hit, hbs = walk(side, last, stop, t, fwd)
                    r[f"t{i+1}_dist"] = round(sgn * (t / last - 1) * 100, 2)
                    r[f"t{i+1}_hit"], r[f"t{i+1}_hbs"] = hit, hbs
                for k, lv in alts.items():
                    hit, hbs = walk(side, last, stop, lv, fwd)
                    r[f"{k}_dist"] = round(sgn * (lv / last - 1) * 100, 2)
                    r[f"{k}_hit"], r[f"{k}_hbs"] = hit, hbs
                rows.append(r)
        if (n + 1) % 10 == 0:
            sys.stderr.write(f"{n + 1}/{len(syms)} syms, {len(rows)} signals\n")
    pickle.dump(rows, open(os.path.join(HERE, ".ab_targets.pkl"), "wb"))

    def pct(xs):
        return f"{100 * sum(xs) / len(xs):3.0f}%" if xs else " n/a"

    def show(sel, label):
        if not sel:
            print(f"  {label:22} n=0")
            return
        d1 = [r["t1_dist"] for r in sel if "t1_dist" in r]
        m = [r["mfe"] for r in sel]
        print(f"  {label:22} n={len(sel):4}  T1 med dist {statistics.median(d1):+5.2f}% | med MFE "
              f"{statistics.median(m):+5.2f}% | T1 hit {pct([r['t1_hit'] for r in sel])} "
              f"(before stop {pct([r['t1_hbs'] for r in sel])})"
              + (f" | T3 hit {pct([r['t3_hit'] for r in sel if 't3_hit' in r])}"
                 if any('t3_hit' in r for r in sel) else ""))

    print(f"\nTARGET ACHIEVABILITY — {len(rows)} signals")
    print("=" * 110)
    show(rows, "ALL")
    show([r for r in rows if r["side"] == "LONG"], "LONG")
    show([r for r in rows if r["side"] == "SHORT"], "SHORT")
    show([r for r in rows if r["blue_sky"]], "blue-sky (ladder spent)")
    show([r for r in rows if not r["blue_sky"]], "laddered")
    print("\nT1 ALTERNATIVES (same signals, same stop):")
    for k, lab in (("current", "T1 as-is (nearest pivot)"), ("cap_proj", "T1 capped by proj (GateF)"),
                   ("cap_hproj", "T1 capped by 0.5*proj"), ("cap_1atr", "T1 capped by 1*ATR")):
        d = [r[f"{k}_dist"] for r in rows]
        print(f"  {lab:26} med dist {statistics.median(d):+5.2f}%  hit {pct([r[f'{k}_hit'] for r in rows])}  "
              f"hit-before-stop {pct([r[f'{k}_hbs'] for r in rows])}")
    print("\nby T1 distance bucket (current):")
    for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 99)):
        sel = [r for r in rows if lo <= r["t1_dist"] < hi]
        print(f"  T1 {lo}-{hi}% away          n={len(sel):4}  hit {pct([r['t1_hit'] for r in sel])}  "
              f"before-stop {pct([r['t1_hbs'] for r in sel])}")


if __name__ == "__main__":
    main()
