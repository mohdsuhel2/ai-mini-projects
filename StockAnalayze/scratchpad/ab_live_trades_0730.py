#!/usr/bin/env python3
"""What did v1 / v2 say at the moment of each LIVE trade taken on 2026-07-30?

For each position autoIntraday actually opened (times from trading-day.json),
replay BOTH engines truncated to the last completed 15m bar before the entry,
print each skill's call, and simulate the skill's trade on the 1m tape with the
common exit policy (structural stop, 2R capped target, square off 15:20).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import stock_analyze_intraday as v1                      # noqa: E402
import stock_analyze_intraday_2 as v2                    # noqa: E402
from intraday_hourly_backtest import synth_daily, running_vwap_series  # noqa: E402
from ab_v1_v2_0730 import (                              # noqa: E402
    BARS_1M, DAY, HIST_DAYS, bar_time, decide_v1, decide_v2, exec_action,
    fetch_all, market_ctx,
)

TRADING_DAY = "/Users/mohdsuhel/ai-mini-projects/autoIntraday/docs/skill-improvements/2026-07-30-trading-day.json"


def cp_of(open_ist):
    """Last 15m-grid close at/before the open time: 11:12 -> 11:00 (bars start < 11:00)."""
    hh, mm = int(open_ist[11:13]), int(open_ist[14:16])
    mm = mm - mm % 15
    return f"{hh:02d}:{mm:02d}"


def main():
    m1_raw = json.load(open(BARS_1M))["bars"]
    td = json.load(open(TRADING_DAY))
    data = fetch_all(sorted(m1_raw.keys()))

    print(f"{'symbol':11}{'live trade':34}{'':2}skill calls at the same moment")
    print("=" * 118)
    tot_live, tot_v1, tot_v2 = 0.0, 0.0, 0.0
    n_live = 0
    for p in td["positions"]:
        s = p["symbol"]
        if s not in m1_raw:
            print(f"{s:11}{p['side']} {p['status']} — no 1m bars (order never filled); skipped\n")
            continue
        cp = cp_of(p["opened_at_ist"])
        notional = p["entry_price"] * p["quantity"]
        live_pnl = p["realized_pnl"]
        if live_pnl is None:  # still open -> mark to last 1m close
            lastc = m1_raw[s][-1]["c"]
            live_pnl = (lastc - p["entry_price"]) * p["quantity"] * (1 if p["side"] == "LONG" else -1)
            live_tag = "OPEN(mtm)"
        else:
            live_tag = p["exit_reason"] or p["status"]
        live_pct = live_pnl / notional * 100
        tot_live += live_pnl
        n_live += 1

        days = v2.group_by_day(data[s]["m15"])
        dk = list(days.keys())
        di = dk.index(DAY)
        day15 = days[DAY]
        hist = [b for k in dk[max(0, di - HIST_DAYS):di] for b in days[k]]
        today_now = [b for b in day15 if bar_time(b) < cp]
        cp_idx = len(today_now)
        vwaps = running_vwap_series(day15)
        dtrunc = [b for b in data[s]["daily"] if b.date[:10] < DAY] + [synth_daily(DAY, today_now)]
        mkt = market_ctx(data, cp)

        print(f"{s:11}LIVE {p['side']:5} @{p['opened_at_ist'][11:16]} e{p['entry_price']:<9.2f}"
              f"sl{p['stop_loss']:<9.2f}-> {live_tag:9} {live_pnl:+9.0f} Rs ({live_pct:+.2f}%)")
        for tag, mod, decider in (("v1", v1, decide_v1), ("v2", v2, decide_v2)):
            rep = mod.build_report(s, f"{s}.NS", "yahoo_intraday", hist + today_now,
                                   "15m", [], list(dtrunc), None, dict(mkt))
            extra = ""
            if tag == "v2":
                rep["institutional_desk"] = v2.institutional_desk_block(rep)
                d = rep["institutional_desk"]
                extra = f"  [grade {d['trade_quality']['grade']} {d['trade_quality']['score']}/100]"
            act, why = decider(rep)
            bias = rep["intraday_structure"]["directional_bias"]
            if act == "NONE":
                print(f"  {tag}: NO TRADE — {why[:70]}  (bias {bias}){extra}")
                continue
            t = exec_action(act, rep, day15, vwaps, cp, m1_raw[s], cp_idx)
            if "skip" in t:
                print(f"  {tag}: {act} ({why[:45]}) -> not executed: {t['skip']}  (bias {bias}){extra}")
                continue
            rs_inr = t["pnl"] / 100 * notional
            if tag == "v1":
                tot_v1 += rs_inr
            else:
                tot_v2 += rs_inr
            print(f"  {tag}: {t['side']:5} @{t['etime']} e{t['entry']:<9.2f}sl{t['stop']:<9.2f}"
                  f"tg{t['target']:<9.2f}-> {t['res']:6} {rs_inr:+9.0f} Rs ({t['pnl']:+.2f}%)"
                  f"  [{why[:42]}]{extra}")
        print()

    print("=" * 118)
    print(f"TOTALS on the {n_live} live positions (same notional per name as live):")
    print(f"  LIVE (autoIntraday + v2 skill + its exit engine): {tot_live:+10.0f} Rs")
    print(f"  v1 replay (skill call + clean structural exits) : {tot_v1:+10.0f} Rs")
    print(f"  v2 replay (skill call + clean structural exits) : {tot_v2:+10.0f} Rs")


if __name__ == "__main__":
    main()
