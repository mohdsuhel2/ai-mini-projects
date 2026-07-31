#!/usr/bin/env python3
"""A/B the AFTERNOON TOPPER-FADE rule (idea from the 2026-07-30 live loss).

Rule under test — at afternoon checkpoints (>= 13:00), when the v1 engine labels a
fresh LONG (`long` / `long-on-pullback`) on a name that:
    - is UP on the day  (day change > +0.5%)
    - has FADED off its high (pct_off_day_high <= -0.5%, bars_since_day_high >= 2)
    - while NIFTY is flat (|NIFTY day change| < 0.3%)   [sensitivity: also without this]
... the long is suspect:
    Variant A: SKIP it (filter).
    Variant B: FLIP it to a structural-stop SHORT.

Walk-forward over the cached 66-name / Apr-Jul 15m set (same replay mechanics as
intraday_hourly_backtest.py: no lookahead, structural stop, 2R target capped by the
ATR projection, square-off on the day's last bar).
"""
import os
import pickle
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from stock_analyze_intraday import build_report, group_by_day  # noqa: E402
from intraday_hourly_backtest import (  # noqa: E402
    CACHE, HIST_DAYS, bar_time, plan_trade, simulate, synth_daily,
)

CHECKPOINTS = ["13:00", "13:30", "14:00", "14:30"]


def nifty_daychg(nifty_days, day, cp):
    bars = [b for b in nifty_days.get(day, []) if bar_time(b) < cp]
    if len(bars) < 2:
        return None
    return (bars[-1].close / bars[0].open - 1) * 100


def stats(ts, label):
    if not ts:
        return f"  {label:34} n=0"
    w = sum(1 for t in ts if t["res"] == "TARGET")
    s = sum(1 for t in ts if t["res"] == "STOP")
    pnls = [t["pnl"] for t in ts]
    return (f"  {label:34} n={len(ts):4}  TGT={w:3} STOP={s:3} EOD={len(ts)-w-s:3}  "
            f"win%={w/len(ts)*100:3.0f}  avg={statistics.mean(pnls):+.3f}%  tot={sum(pnls):+.1f}%")


def main():
    cache = pickle.load(open(CACHE, "rb"))
    nifty_days = group_by_day(cache["__NIFTY__"]) if "__NIFTY__" in cache else {}
    syms = [s for s in cache if s != "__NIFTY__"]

    longs = []          # every afternoon long the baseline takes
    done = 0
    for sym in syms:
        data = cache[sym]
        m15, daily = data["m15"], data["daily"]
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
                if len(today_now) < 6:
                    continue
                cp_idx = len(today_now)
                try:
                    rep = build_report(sym, f"{sym}.NS", "yahoo_intraday",
                                       hist + today_now, "15m", [],
                                       daily_hist + [synth_daily(D, today_now)], None, None)
                except Exception:
                    continue
                st = rep["intraday_structure"]
                bias = st["directional_bias"]
                if bias not in ("long", "long-on-pullback"):
                    continue
                rvol = rep["volume"]["rvol_vs_prior_days"]
                if rvol is not None and rvol < 0.8:
                    continue
                brk = rep.get("breakout") or {}
                if brk.get("direction") == "up" and brk.get("extended_past_level") and not brk.get("fresh"):
                    continue                      # Gate D skip (baseline behaviour)
                last = rep["price"]["last"]
                dopen = rep["price"]["day_open"]
                vwap = rep["vwap"]["vwap"]
                atr = rep["indicators"]["atr14_intraday"]
                proj = rep["projection"]["atr_projected_remaining_move_pct"]
                plan = plan_trade("LONG", last, vwap, atr, proj)
                if not plan:
                    continue
                stop, target, rr = plan
                fwd = day_bars[cp_idx:]
                res, pnl = simulate("LONG", last, stop, target, fwd)

                daychg = (last / dopen - 1) * 100 if dopen else None
                fade = st.get("pct_off_day_high")
                bsh = st.get("bars_since_day_high")
                nchg = nifty_daychg(nifty_days, D, cp)
                topper = (daychg is not None and daychg > 0.5
                          and fade is not None and fade <= -0.5
                          and bsh is not None and bsh >= 2)
                nifty_flat = nchg is not None and abs(nchg) < 0.3

                # variant B: the flipped short at the same moment
                swing_hi = max(b.high for b in day_bars[max(0, cp_idx - 4):cp_idx])
                fres, fpnl = ("NA", 0.0)
                splan = plan_trade("SHORT", last, vwap, atr, proj, swing_hi)
                if splan:
                    fres, fpnl = simulate("SHORT", last, splan[0], splan[1], fwd)

                longs.append({"sym": sym, "day": D, "cp": cp, "bias": bias,
                              "daychg": daychg, "fade": fade, "nifty": nchg,
                              "topper": topper, "nifty_flat": nifty_flat,
                              "res": res, "pnl": round(pnl, 2),
                              "fres": fres, "fpnl": round(fpnl, 2)})
        done += 1
        if done % 10 == 0:
            sys.stderr.write(f"{done}/{len(syms)} symbols done, {len(longs)} afternoon longs\n")

    pickle.dump(longs, open(os.path.join(HERE, ".ab_topper_fade.pkl"), "wb"))

    flip = lambda ts: [dict(t, res=t["fres"], pnl=t["fpnl"]) for t in ts]  # noqa: E731
    m_full = [t for t in longs if t["topper"] and t["nifty_flat"]]
    m_any = [t for t in longs if t["topper"]]
    rest_full = [t for t in longs if not (t["topper"] and t["nifty_flat"])]

    print(f"\nAFTERNOON (>=13:00) fresh v1 longs over {len(syms)} names, Apr-Jul: n={len(longs)}")
    print("=" * 96)
    print(stats(longs, "ALL afternoon longs (baseline)"))
    print()
    print("Rule = topper (+0.5% up, faded 0.5% off high, 2+ bars) AND NIFTY flat (<0.3%):")
    print(stats(m_full, "  matched longs (would be cut)"))
    print(stats(flip(m_full), "  variant B: flipped to SHORT"))
    print(stats(rest_full, "  kept longs (rule doesn't fire)"))
    print()
    print("Sensitivity — topper condition alone (NO NIFTY-flat requirement):")
    print(stats(m_any, "  matched longs (would be cut)"))
    print(stats(flip(m_any), "  variant B: flipped to SHORT"))
    print(stats([t for t in longs if not t["topper"]], "  kept longs"))
    print()
    print("Slice: matched by bias label:")
    for b in ("long", "long-on-pullback"):
        sel = [t for t in m_any if t["bias"] == b]
        print(stats(sel, f"  {b} longs"))
        print(stats(flip(sel), f"  {b} flipped SHORT"))


if __name__ == "__main__":
    main()
