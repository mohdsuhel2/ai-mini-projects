#!/usr/bin/env python3
"""A/B the MATURITY CARVE-OUT added to the skill on 2026-07-22.

Baseline (pre-22-Jul skill):  directional_bias == "neutral"  -> WAIT
Carve-out (current skill):    at bars_today < 6, OVERRIDE "neutral" using
                              ADX + DI spread + ema_alignment + SuperTrend + HTF overall_bias.

Replays the real engine (build_report) with no lookahead, exactly like
intraday_hourly_backtest.py. The 10:00 checkpoint sees 3 bars = the carve-out window.
Measures ONLY the trades the carve-out newly creates.
"""
import os, pickle, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/Users/mohdsuhel/ai-mini-projects/StockAnalayze")
from stock_analyze_intraday import build_report, group_by_day
from intraday_hourly_backtest import (bar_time, synth_daily, running_vwap_series,
                                      plan_trade, simulate, HIST_DAYS, CACHE)

EARLY_CPS = ["10:00", "10:15"]   # 3 and 4 bars in -> inside the <6-bar carve-out window


def carveout_call(rep):
    """Faithful encoding of the carve-out as I have been applying it live."""
    ins = rep.get("institutional") or {}
    adx = (ins.get("adx") or {})
    a, p, m = adx.get("adx"), adx.get("plus_di"), adx.get("minus_di")
    ema = ins.get("ema_alignment")
    st = (ins.get("supertrend") or {}).get("direction")
    htf = ((rep.get("higher_timeframe") or {}).get("overall_bias") or "")
    if None in (a, p, m):
        return None
    bull = p > m * 2 and ema == "bullish_stack" and st == "up" and "bullish" in htf
    bear = m > p * 2 and ema == "bearish_stack" and st == "down" and "bearish" in htf
    if bull and not bear:
        return "LONG"
    if bear and not bull:
        return "SHORT"
    return None            # fields disagree -> a TRUE neutral, stay out


def run(sym, data):
    m15, daily = data["m15"], data["daily"]
    days = group_by_day(m15); dk = list(days.keys())
    out = []
    for di in range(10, len(dk)):
        D = dk[di]; day_bars = days[D]
        if len(day_bars) < 8: continue
        hist = [b for k in dk[max(0, di - HIST_DAYS):di] for b in days[k]]
        vwaps = running_vwap_series(day_bars)
        dhist = [b for b in daily if b.date < D]
        for cp in EARLY_CPS:
            now = [b for b in day_bars if bar_time(b) < cp]
            if len(now) < 3 or len(now) >= 6: continue      # carve-out window only
            ci = len(now)
            try:
                rep = build_report(sym, f"{sym}.NS", "yahoo_intraday", hist + now, "15m",
                                   [], dhist + [synth_daily(D, now)], None, None)
            except Exception:
                continue
            if rep["intraday_structure"]["directional_bias"] != "neutral":
                continue                                     # baseline already traded it
            side = carveout_call(rep)
            if side is None:
                continue                                     # carve-out also says WAIT
            rvol = rep["volume"]["rvol_vs_prior_days"]
            if rvol is not None and rvol < 0.8: continue      # skill's RVOL gate
            atr = rep["indicators"]["atr14_intraday"]
            proj = rep["projection"]["atr_projected_remaining_move_pct"]
            plan = plan_trade(side, rep["price"]["last"], rep["vwap"]["vwap"], atr, proj)
            if not plan: continue
            stop, target, rr = plan
            res, pnl = simulate(side, rep["price"]["last"], stop, target, day_bars[ci:])
            out.append({"sym": sym, "day": D, "cp": cp, "bars": len(now), "side": side,
                        "adx": rep["institutional"]["adx"]["adx"], "res": res,
                        "pnl": round(pnl, 2)})
    return out


def stats(ts, label):
    if not ts:
        print(f"{label:34} n=0"); return
    w = sum(1 for t in ts if t["res"] == "TARGET"); s = sum(1 for t in ts if t["res"] == "STOP")
    p = [t["pnl"] for t in ts]
    print(f"{label:34} n={len(ts):4}  TGT={w:3} STOP={s:3} EOD={len(ts)-w-s:3}  "
          f"win%={w/len(ts)*100:3.0f}  avg={statistics.mean(p):+.3f}%  "
          f"med={statistics.median(p):+.3f}%  TOTAL={sum(p):+.1f}%")


cache = pickle.load(open(CACHE, "rb"))
syms = sorted(cache.keys())
allt = []
for s in syms:
    try: allt += run(s, cache[s])
    except Exception as e: sys.stderr.write(f"{s}: {e}\n")

print(f"\nUniverse: {len(syms)} symbols  |  window: bars_today 3-5 (the carve-out window)")
print("=" * 100)
print("These are trades the CARVE-OUT creates that the OLD skill would have skipped as `neutral -> WAIT`.")
print("Baseline P&L on these same setups is by definition 0.00% (no trade taken).\n")
stats(allt, "CARVE-OUT overrides (ALL)")
stats([t for t in allt if t["side"] == "LONG"],  "  LONG overrides")
stats([t for t in allt if t["side"] == "SHORT"], "  SHORT overrides")
print()
for lo, hi in ((0, 30), (30, 45), (45, 60), (60, 200)):
    stats([t for t in allt if lo <= t["adx"] < hi], f"  ADX {lo}-{hi}")
print()
for b in (3, 4, 5):
    stats([t for t in allt if t["bars"] == b], f"  bars_today={b}")
import json; json.dump(allt, open("scratchpad/ab_carveout.json", "w"))
