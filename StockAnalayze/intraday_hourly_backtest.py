#!/usr/bin/env python3
"""Walk-forward backtest of the intraday-analyst SKILL engine at hourly checkpoints.

Replays stock_analyze_intraday.build_report() — the exact code the skill runs live —
at 10:00 / 11:00 / 12:00 / 13:00 / 14:00 IST each day, feeding it ONLY the bars that
existed at that moment (15m intraday + truncated daily HTF, no lookahead). The report's
`directional_bias` is mapped to a trade the way the skill's DIRECTION GATE dictates:

  long / long-on-pullback   -> LONG at the checkpoint price
  short                     -> SHORT at the checkpoint price
  short-on-breakdown        -> trigger-gated: SHORT only on a later 15m close < running VWAP
  neutral                   -> WAIT

Skill gates applied: RVOL < 0.8 -> no participation, reject (counted separately);
effective R:R < 1.2 after the ATR-projection cap -> reject as poor R:R; no fresh entry
after 14:45. Stops/targets follow STEP 13 (structural VWAP/swing stop bounded by ATR,
target = 2R capped by atr_projected_remaining_move). Same-bar stop+target = STOP
(conservative). Open trades square off on the day's last bar (~15:20 rule).

Also records each day's opening gap (vs PDC) and whether it FILLED (touched PDC),
so decisions can be sliced by gap regime — flat <0.3% / modest 0.3-1% / big >1%.

Usage: .venv/bin/python intraday_hourly_backtest.py [SYM ...]   (default SRF ABB DIXON)
"""
import os
import pickle
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stock_analyze import OHLCVBar, fetch_yahoo_chart  # noqa: E402
from stock_analyze_intraday import (  # noqa: E402
    build_report, fetch_intraday_yahoo, group_by_day, session_vwap,
)

CHECKPOINTS = ["10:00", "11:00", "12:00", "13:00", "14:00"]
LAST_ENTRY = "14:45"          # skill: avoid fresh entries in the last stretch
HIST_DAYS = 25                # ~1mo of 15m bars into the engine, like live full mode
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratchpad", ".ihb_cache.pkl")


def fetch_all(sym):
    return {
        "m15": fetch_intraday_yahoo(f"{sym}.NS", "15m", "60d"),
        "daily": fetch_yahoo_chart(f"{sym}.NS", "1y", "1d"),
    }


def bar_time(b):
    return b.date.split(" ")[1][:5]


def synth_daily(day, bars_so_far):
    vols = [b.volume for b in bars_so_far if b.volume]
    return OHLCVBar(date=day, open=bars_so_far[0].open,
                    high=max(b.high for b in bars_so_far),
                    low=min(b.low for b in bars_so_far),
                    close=bars_so_far[-1].close, volume=sum(vols) if vols else None)


def running_vwap_series(day_bars):
    """VWAP after each bar of the day (what the engine would see live)."""
    out, num, den = [], 0.0, 0.0
    for b in day_bars:
        if b.volume and b.volume > 0:
            num += (b.high + b.low + b.close) / 3.0 * b.volume
            den += b.volume
        out.append(num / den if den else b.close)
    return out


def plan_trade(side, entry, vwap, atr, proj_pct, swing_hi=None):
    """STEP-13 style stop/target. Returns (stop, target, rr) or None if R:R too poor."""
    if not atr or atr <= 0:
        return None
    if side == "LONG":
        stop = max((vwap - 0.25 * atr) if vwap and vwap < entry else entry - 1.0 * atr,
                   entry - 1.5 * atr)
        stop = min(stop, entry - 0.5 * atr)
        risk = entry - stop
        target = entry + 2.0 * risk
        if proj_pct:
            target = min(target, entry * (1 + proj_pct / 100.0))
        rr = (target - entry) / risk if risk > 0 else 0
    else:
        ceil = vwap + 0.25 * atr if vwap and vwap > entry else entry + 1.0 * atr
        if swing_hi:
            ceil = min(ceil, swing_hi * 1.002) if swing_hi * 1.002 > entry else ceil
        stop = min(ceil, entry + 1.5 * atr)
        stop = max(stop, entry + 0.5 * atr)
        risk = stop - entry
        target = entry - 2.0 * risk
        if proj_pct:
            target = max(target, entry * (1 - proj_pct / 100.0))
        rr = (entry - target) / risk if risk > 0 else 0
    if rr < 1.2:
        return None
    return round(stop, 2), round(target, 2), round(rr, 2)


def simulate(side, entry, stop, target, fwd_bars):
    """Walk forward to square-off. Same-bar stop+target counts as STOP."""
    for b in fwd_bars:
        if side == "LONG":
            if b.low <= stop:
                return "STOP", (stop / entry - 1) * 100
            if b.high >= target:
                return "TARGET", (target / entry - 1) * 100
        else:
            if b.high >= stop:
                return "STOP", (1 - stop / entry) * 100
            if b.low <= target:
                return "TARGET", (1 - target / entry) * 100
    if not fwd_bars:
        return "EOD", 0.0
    close = fwd_bars[-1].close
    pnl = (close / entry - 1) * 100 if side == "LONG" else (1 - close / entry) * 100
    return "EOD", pnl


def decide_and_trade(rep, day_bars, cp_idx, vwaps):
    """Map the engine report to the skill's action; simulate. Returns a trade dict or a skip reason."""
    bias = rep["intraday_structure"]["directional_bias"]
    rvol = rep["volume"]["rvol_vs_prior_days"]
    atr = rep["indicators"]["atr14_intraday"]
    proj = rep["projection"]["atr_projected_remaining_move_pct"]
    vwap = rep["vwap"]["vwap"]
    last = rep["price"]["last"]
    brk = rep.get("breakout") or {}

    if bias == "neutral":
        return {"action": "WAIT"}
    if rvol is not None and rvol < 0.8:
        return {"action": "SKIP_RVOL", "bias": bias}

    fwd = day_bars[cp_idx:]                       # bars strictly after the checkpoint
    if bias in ("long", "long-on-pullback"):
        # breakout gate: broke out long ago and extended -> the clean entry has passed
        if brk.get("direction") == "up" and brk.get("extended_past_level") and not brk.get("fresh"):
            return {"action": "SKIP_EXTENDED", "bias": bias}
        plan = plan_trade("LONG", last, vwap, atr, proj)
        if not plan:
            return {"action": "SKIP_RR", "bias": bias}
        stop, target, rr = plan
        res, pnl = simulate("LONG", last, stop, target, fwd)
        return {"action": "LONG", "bias": bias, "entry": last, "stop": stop,
                "target": target, "rr": rr, "res": res, "pnl": round(pnl, 2)}

    if bias == "short":
        swing_hi = max(b.high for b in day_bars[max(0, cp_idx - 4):cp_idx]) if cp_idx else None
        plan = plan_trade("SHORT", last, vwap, atr, proj, swing_hi)
        if not plan:
            return {"action": "SKIP_RR", "bias": bias}
        stop, target, rr = plan
        res, pnl = simulate("SHORT", last, stop, target, fwd)
        return {"action": "SHORT", "bias": bias, "entry": last, "stop": stop,
                "target": target, "rr": rr, "res": res, "pnl": round(pnl, 2)}

    if bias == "short-on-breakdown":
        for j in range(cp_idx, len(day_bars)):
            b = day_bars[j]
            if bar_time(b) >= LAST_ENTRY:
                break
            if b.close < vwaps[j]:                # 15m close below running VWAP = trigger
                entry = b.close
                day_hi = max(x.high for x in day_bars[:j + 1])
                plan = plan_trade("SHORT", entry, vwaps[j], atr, proj, day_hi)
                if not plan:
                    return {"action": "SKIP_RR", "bias": bias}
                stop, target, rr = plan
                res, pnl = simulate("SHORT", entry, stop, target, day_bars[j + 1:])
                return {"action": "SHORT_TRIG", "bias": bias, "entry": entry, "stop": stop,
                        "target": target, "rr": rr, "res": res, "pnl": round(pnl, 2),
                        "trig_time": bar_time(b)}
        return {"action": "NO_TRIGGER", "bias": bias}
    return {"action": "WAIT"}


def gap_bucket(g):
    a = abs(g)
    return "flat(<0.3%)" if a < 0.3 else "modest(0.3-1%)" if a < 1.0 else "big(>1%)"


def run_symbol(sym, data):
    m15, daily = data["m15"], data["daily"]
    days = group_by_day(m15)
    day_keys = list(days.keys())
    trades, waits, day_meta = [], [], {}

    for di in range(10, len(day_keys)):           # need prior days for RVOL/pivots
        D = day_keys[di]
        day_bars = days[D]
        if len(day_bars) < 8:
            continue
        hist_keys = day_keys[max(0, di - HIST_DAYS):di]
        hist_bars = [b for k in hist_keys for b in days[k]]
        vwaps = running_vwap_series(day_bars)
        daily_hist = [b for b in daily if b.date < D]

        pdc = days[day_keys[di - 1]][-1].close
        gap = (day_bars[0].open / pdc - 1) * 100
        d_hi, d_lo = max(b.high for b in day_bars), min(b.low for b in day_bars)
        filled = (gap > 0 and d_lo <= pdc) or (gap < 0 and d_hi >= pdc)
        day_meta[D] = {"gap": round(gap, 2), "filled": filled,
                       "oc_ret": round((day_bars[-1].close / day_bars[0].open - 1) * 100, 2)}

        for cp in CHECKPOINTS:
            today_now = [b for b in day_bars if bar_time(b) < cp]
            if len(today_now) < 3:
                continue
            cp_idx = len(today_now)
            bars_now = hist_bars + today_now
            dtrunc = daily_hist + [synth_daily(D, today_now)]
            try:
                rep = build_report(sym, f"{sym}.NS", "yahoo_intraday", bars_now, "15m",
                                   [], dtrunc, None, None)
            except Exception as e:
                sys.stderr.write(f"{sym} {D} {cp}: engine error {e}\n")
                continue
            t = decide_and_trade(rep, day_bars, cp_idx, vwaps)
            t.update({"sym": sym, "day": D, "cp": cp, "gap": day_meta[D]["gap"],
                      "bias": t.get("bias", rep["intraday_structure"]["directional_bias"])})
            (trades if t["action"] in ("LONG", "SHORT", "SHORT_TRIG") else waits).append(t)
    return trades, waits, day_meta


def wr(ts):
    if not ts:
        return "n=0"
    w = sum(1 for t in ts if t["res"] == "TARGET")
    s = sum(1 for t in ts if t["res"] == "STOP")
    e = len(ts) - w - s
    pnls = [t["pnl"] for t in ts]
    return (f"n={len(ts):3}  TGT={w:3} STOP={s:3} EOD={e:3}  win%={w / len(ts) * 100:3.0f}  "
            f"avgP&L={statistics.mean(pnls):+.2f}%  medP&L={statistics.median(pnls):+.2f}%  "
            f"totP&L={sum(pnls):+.1f}%")


def main():
    syms = [a.upper() for a in sys.argv[1:]] or ["SRF", "ABB", "DIXON"]
    cache = pickle.load(open(CACHE, "rb")) if os.path.exists(CACHE) else {}
    for s in syms:
        if s not in cache:
            sys.stderr.write(f"fetching {s}...\n")
            cache[s] = fetch_all(s)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    pickle.dump(cache, open(CACHE, "wb"))

    all_trades, all_waits = [], []
    for s in syms:
        trades, waits, meta = run_symbol(s, cache[s])
        all_trades += trades
        all_waits += waits
        gaps = [m["gap"] for m in meta.values()]
        nfill = sum(1 for m in meta.values() if abs(m["gap"]) >= 0.3 and m["filled"])
        ngap = sum(1 for m in meta.values() if abs(m["gap"]) >= 0.3)
        print(f"\n===== {s}: {len(meta)} days, avg|gap|={statistics.mean([abs(g) for g in gaps]):.2f}%, "
              f"gaps>=0.3%: {ngap} (filled same day: {nfill}, {nfill / ngap * 100 if ngap else 0:.0f}%) =====")
        for cp in CHECKPOINTS:
            print(f"  {cp}  {wr([t for t in trades if t['cp'] == cp])}")
        print(f"  ALL    {wr(trades)}")

    print("\n===== COMBINED (all symbols) =====")
    for cp in CHECKPOINTS:
        print(f"  {cp}  {wr([t for t in all_trades if t['cp'] == cp])}")
    for side in ("LONG", "SHORT", "SHORT_TRIG"):
        print(f"  {side:10} {wr([t for t in all_trades if t['action'] == side])}")
    print("\n  by gap regime (trade-day gap):")
    for gb in ("flat(<0.3%)", "modest(0.3-1%)", "big(>1%)"):
        print(f"  {gb:15} {wr([t for t in all_trades if gap_bucket(t['gap']) == gb])}")
    print("\n  by bias label:")
    for b in sorted({t["bias"] for t in all_trades}):
        print(f"  {b:20} {wr([t for t in all_trades if t['bias'] == b])}")

    skip = {}
    for t in all_waits:
        skip[t["action"]] = skip.get(t["action"], 0) + 1
    print(f"\n  non-trades: {skip}")

    print("\n===== trade log =====")
    for t in all_trades:
        print(f"  {t['sym']:6} {t['day']} {t['cp']} gap{t['gap']:+.1f}% {t['action']:10} "
              f"[{t['bias']}] e{t['entry']:.1f} sl{t['stop']:.1f} tg{t['target']:.1f} "
              f"rr{t['rr']} -> {t['res']:6} {t['pnl']:+.2f}%"
              + (f" (trig {t['trig_time']})" if t.get("trig_time") else ""))


if __name__ == "__main__":
    main()
