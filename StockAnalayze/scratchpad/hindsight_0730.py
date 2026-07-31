#!/usr/bin/env python3
"""Suggested vs what ACTUALLY happened — hindsight table for the 9 live trades of 2026-07-30.

For each live-trade decision moment: what each side (LONG/SHORT) would have done with
the same structural-stop plan, what the tape actually did (max up / max down / close),
and therefore what the correct call was in hindsight.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import stock_analyze_intraday_2 as v2                    # noqa: E402
from intraday_hourly_backtest import plan_trade, synth_daily  # noqa: E402
from ab_v1_v2_0730 import (                              # noqa: E402
    BARS_1M, DAY, HIST_DAYS, SQUARE_OFF, bar_time, fetch_all, market_ctx, sim_1m,
)
from ab_live_trades_0730 import TRADING_DAY, cp_of       # noqa: E402


def main():
    m1_raw = json.load(open(BARS_1M))["bars"]
    td = json.load(open(TRADING_DAY))
    data = fetch_all(sorted(m1_raw.keys()))

    print(f"{'symbol':11}{'cp':6}{'px@cp':>9}  {'taken':6}{'-> what the tape did to 15:20':34}"
          f"{'LONG plan':>14}{'SHORT plan':>14}  hindsight-correct call")
    print("=" * 128)
    for p in td["positions"]:
        s = p["symbol"]
        if s not in m1_raw:
            continue
        cp = cp_of(p["opened_at_ist"])
        m1 = m1_raw[s]

        days = v2.group_by_day(data[s]["m15"])
        dk = list(days.keys())
        di = dk.index(DAY)
        day15 = days[DAY]
        hist = [b for k in dk[max(0, di - HIST_DAYS):di] for b in days[k]]
        today_now = [b for b in day15 if bar_time(b) < cp]
        dtrunc = [b for b in data[s]["daily"] if b.date[:10] < DAY] + [synth_daily(DAY, today_now)]
        rep = v2.build_report(s, f"{s}.NS", "yahoo_intraday", hist + today_now,
                              "15m", [], dtrunc, None, market_ctx(data, cp))
        last = rep["price"]["last"]
        vwap = rep["vwap"]["vwap"]
        atr = rep["indicators"]["atr14_intraday"]
        proj = rep["projection"]["atr_projected_remaining_move_pct"]
        cp_idx = len(today_now)
        swing_hi = max(b.high for b in day15[max(0, cp_idx - 4):cp_idx]) if cp_idx else None

        fwd = [b for b in m1 if cp <= b["t"] <= SQUARE_OFF]
        hi = max(b["h"] for b in fwd)
        lo = min(b["l"] for b in fwd)
        close = fwd[-1]["c"]
        up = (hi / last - 1) * 100
        dn = (lo / last - 1) * 100
        drift = (close / last - 1) * 100

        res = {}
        for side in ("LONG", "SHORT"):
            plan = plan_trade(side, last, vwap, atr, proj, swing_hi if side == "SHORT" else None)
            if not plan:
                res[side] = ("n/a", 0.0)
                continue
            stop, target, rr = plan
            r, pnl, _ = sim_1m(side, last, stop, target, m1, cp)
            res[side] = (r, pnl)

        lp, sp = res["LONG"][1], res["SHORT"][1]
        if lp <= 0 and sp <= 0:
            correct = "WAIT / NO TRADE (both sides lose)"
        elif lp > sp:
            correct = f"LONG ({res['LONG'][0]} {lp:+.2f}%)"
        else:
            correct = f"SHORT ({res['SHORT'][0]} {sp:+.2f}%)"

        taken = p["side"]
        print(f"{s:11}{cp:6}{last:>9.2f}  {taken:6}"
              f"max +{up:.2f}% / {dn:.2f}%, close {drift:+.2f}%   "
              f"{res['LONG'][0][:4]:>6} {lp:+6.2f}%{res['SHORT'][0][:4]:>7} {sp:+6.2f}%   {correct}")
    print("\n(plans = same structural-stop/2R-capped-target policy as the replay; "
          "max/close measured from the decision price to 15:20)")


if __name__ == "__main__":
    main()
