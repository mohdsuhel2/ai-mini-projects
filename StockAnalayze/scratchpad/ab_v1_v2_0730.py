#!/usr/bin/env python3
"""A/B: intraday-analyst (v1) vs intraday-analyst-2 (v2) on 2026-07-30.

Replays BOTH engines with no lookahead at random 15m-close checkpoints, on the
9 symbols autoIntraday actually traded today (v2 live). Decisions are mapped to
trades per each SKILL's documented deterministic policy; fills/stops/targets are
simulated on the real 1-minute tape exported by autoIntraday.

Common to both sides (identical, so the A/B isolates SELECTION quality):
  - entry price = last CLOSED 15m bar close (what the engine calls price.last)
  - stops/targets: plan_trade() from intraday_hourly_backtest (structural stop,
    2R target capped by the ATR projection)
  - square-off 15:20; same-bar stop+target = STOP (conservative)
  - RS mild-weak-band long veto (both skills: v1 Gate C2 / v2 L3 hard rule)

v1 policy (SKILL.md gates, deterministic parts):
  long -> BUY NOW; long-on-pullback -> VWAP limit; short -> SHORT NOW
  (FINOPB veto -> breakdown trigger; corpse -> skip); short-on-breakdown ->
  trigger on 15m close < running VWAP; neutral -> WAIT; RVOL<0.8 skip;
  extended-stale-breakout skip.

v2 policy: institutional_desk.computed_verdict_hint + no_trade_filters
  (A/A+ -> immediate; B -> conditional trigger; C/F/WAIT/NO TRADE -> nothing).
"""
import json
import os
import pickle
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import stock_analyze_intraday as v1                      # noqa: E402
import stock_analyze_intraday_2 as v2                    # noqa: E402
from stock_analyze import OHLCVBar, fetch_yahoo_chart    # noqa: E402
from intraday_hourly_backtest import plan_trade, synth_daily, running_vwap_series  # noqa: E402

DAY = "2026-07-30"
BARS_1M = "/Users/mohdsuhel/ai-mini-projects/autoIntraday/docs/skill-improvements/2026-07-30-bars-1m.json"
CACHE = os.path.join(HERE, ".ab0730_cache.pkl")
HIST_DAYS = 25
LAST_ENTRY = "14:45"
SQUARE_OFF = "15:20"
NOTIONAL = 100_000  # Rs per trade for the money view


def bar_time(b):
    return b.date.split(" ")[1][:5]


def fetch_all(syms):
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, "rb"))
    data = {}
    for s in syms:
        sys.stderr.write(f"fetching {s}...\n")
        data[s] = {"m15": v2.fetch_intraday_yahoo(f"{s}.NS", "15m", "60d"),
                   "daily": fetch_yahoo_chart(f"{s}.NS", "6mo", "1d")}
    sys.stderr.write("fetching NIFTY / VIX...\n")
    data["^NSEI"] = {"m15": v2.fetch_intraday_yahoo("^NSEI", "15m", "60d")}
    data["^VIXD"] = {"daily": fetch_yahoo_chart("^INDIAVIX", "3mo", "1d")}
    try:
        data["^VIX15"] = {"m15": v2.fetch_intraday_yahoo("^INDIAVIX", "15m", "5d")}
    except Exception as e:
        sys.stderr.write(f"VIX 15m unavailable ({e}); will use daily closes\n")
        data["^VIX15"] = {"m15": []}
    pickle.dump(data, open(CACHE, "wb"))
    return data


def market_ctx(data, cp):
    """Truncated NIFTY + VIX context as of checkpoint cp on DAY."""
    nb = [b for b in data["^NSEI"]["m15"]
          if b.date[:10] < DAY or (b.date[:10] == DAY and bar_time(b) < cp)]
    vix_daily = [b for b in data["^VIXD"]["daily"] if b.date[:10] < DAY]
    vix_now = [b for b in data["^VIX15"]["m15"]
               if b.date[:10] == DAY and bar_time(b) < cp]
    vb = list(vix_daily)
    if vix_now:
        vb = vb + [OHLCVBar(date=DAY, open=vix_now[0].open,
                            high=max(x.high for x in vix_now),
                            low=min(x.low for x in vix_now),
                            close=vix_now[-1].close, volume=None)]
    return {"india_vix": v2._vix_ctx(vb[-5:]) if len(vb) >= 2 else {"note": "unavailable"},
            "nifty": v2._nifty_ctx(nb[-200:]) if nb else {"note": "unavailable"}}


def rs_veto_long(rep):
    """Shared RS mild-weak-band veto (v1 C2 / v2 L3). True -> block the long."""
    p = rep.get("price") or {}
    n = (rep.get("market_context") or {}).get("nifty") or {}
    last, dopen, nchg = p.get("last"), p.get("day_open"), n.get("day_change_pct")
    if not last or not dopen or nchg is None:
        return False
    rs = (last / dopen - 1) * 100 - nchg
    return -2.0 <= rs <= -0.5


# ---------------- 1m simulation ----------------

def sim_1m(side, entry, stop, target, m1, start_t):
    """Walk 1m bars with t >= start_t to square-off."""
    fwd = [b for b in m1 if b["t"] >= start_t and b["t"] <= SQUARE_OFF]
    for b in fwd:
        if side == "LONG":
            if b["l"] <= stop:
                return "STOP", (stop / entry - 1) * 100, b["t"]
            if b["h"] >= target:
                return "TARGET", (target / entry - 1) * 100, b["t"]
        else:
            if b["h"] >= stop:
                return "STOP", (1 - stop / entry) * 100, b["t"]
            if b["l"] <= target:
                return "TARGET", (1 - target / entry) * 100, b["t"]
    if not fwd:
        return "EOD", 0.0, start_t
    c = fwd[-1]["c"]
    pnl = (c / entry - 1) * 100 if side == "LONG" else (1 - c / entry) * 100
    return "EOD", pnl, fwd[-1]["t"]


def fill_limit_long(limit, m1, start_t):
    """First 1m bar (start_t..LAST_ENTRY) whose low touches the limit."""
    for b in m1:
        if b["t"] < start_t or b["t"] > LAST_ENTRY:
            continue
        if b["l"] <= limit:
            return b["t"]
    return None


def _bar_close_time(t):
    h, m = int(t[:2]), int(t[3:])
    m += 15
    return f"{h + m // 60:02d}:{m % 60:02d}"


def breakdown_trigger(day15, vwaps, cp):
    """First 15m close < running VWAP at/after cp, before LAST_ENTRY.
    Returns (entry_price, entry_time_after_bar_close) or None."""
    for j, b in enumerate(day15):
        t = bar_time(b)
        if t < cp or t >= LAST_ENTRY:
            continue
        if b.close < vwaps[j]:
            return b.close, _bar_close_time(t)
    return None


def swing_low_trigger(day15, cp, cp_idx):
    """FINOPB path (both skills): short only the BREAK of the swing low.
    Swing low = min low of the last 4 completed bars before cp. Triggers on the
    first later 15m bar CLOSING below it (before LAST_ENTRY)."""
    if cp_idx < 1:
        return None
    swing_lo = min(b.low for b in day15[max(0, cp_idx - 4):cp_idx])
    for b in day15[cp_idx:]:
        t = bar_time(b)
        if t >= LAST_ENTRY:
            break
        if b.close < swing_lo:
            return b.close, _bar_close_time(t)
    return None


def exec_action(act, rep, day15, vwaps, cp, m1, cp_idx):
    """Shared executor. act in IMMEDIATE_LONG/IMMEDIATE_SHORT/LIMIT_LONG/BREAKDOWN_SHORT."""
    last = rep["price"]["last"]
    vwap = rep["vwap"]["vwap"]
    atr = rep["indicators"]["atr14_intraday"]
    proj = rep["projection"]["atr_projected_remaining_move_pct"]
    swing_hi = max(b.high for b in day15[max(0, cp_idx - 4):cp_idx]) if cp_idx else None

    if act == "IMMEDIATE_LONG":
        plan = plan_trade("LONG", last, vwap, atr, proj)
        if not plan:
            return {"skip": "rr"}
        stop, target, rr = plan
        res, pnl, xt = sim_1m("LONG", last, stop, target, m1, cp)
        return {"side": "LONG", "entry": last, "stop": stop, "target": target,
                "rr": rr, "res": res, "pnl": pnl, "etime": cp, "xtime": xt}

    if act == "IMMEDIATE_SHORT":
        plan = plan_trade("SHORT", last, vwap, atr, proj, swing_hi)
        if not plan:
            return {"skip": "rr"}
        stop, target, rr = plan
        res, pnl, xt = sim_1m("SHORT", last, stop, target, m1, cp)
        return {"side": "SHORT", "entry": last, "stop": stop, "target": target,
                "rr": rr, "res": res, "pnl": pnl, "etime": cp, "xtime": xt}

    if act == "LIMIT_LONG":
        limit = round(vwap, 2) if vwap else None
        if not limit or limit >= last:          # already at/below vwap -> immediate
            return exec_action("IMMEDIATE_LONG", rep, day15, vwaps, cp, m1, cp_idx)
        ft = fill_limit_long(limit, m1, cp)
        if not ft:
            return {"skip": "no_fill"}
        plan = plan_trade("LONG", limit, vwap, atr, proj)
        if not plan:
            return {"skip": "rr"}
        stop, target, rr = plan
        res, pnl, xt = sim_1m("LONG", limit, stop, target, m1, ft)
        return {"side": "LONG", "entry": limit, "stop": stop, "target": target,
                "rr": rr, "res": res, "pnl": pnl, "etime": ft, "xtime": xt, "kind": "limit"}

    if act in ("BREAKDOWN_SHORT", "SWINGLOW_SHORT"):
        trig = (swing_low_trigger(day15, cp, cp_idx) if act == "SWINGLOW_SHORT"
                else breakdown_trigger(day15, vwaps, cp))
        if not trig:
            return {"skip": "no_trigger"}
        entry, et = trig
        day_hi = max(b.high for b in day15 if bar_time(b) < et)
        plan = plan_trade("SHORT", entry, vwap, atr, proj, day_hi)
        if not plan:
            return {"skip": "rr"}
        stop, target, rr = plan
        res, pnl, xt = sim_1m("SHORT", entry, stop, target, m1, et)
        return {"side": "SHORT", "entry": entry, "stop": stop, "target": target,
                "rr": rr, "res": res, "pnl": pnl, "etime": et, "xtime": xt, "kind": "trigger"}
    return {"skip": "none"}


# ---------------- policies ----------------

def decide_v1(rep):
    st = rep["intraday_structure"]
    bias = st["directional_bias"]
    rvol = rep["volume"]["rvol_vs_prior_days"]
    brk = rep.get("breakout") or {}
    ins = rep.get("institutional") or {}
    stdir = (ins.get("supertrend") or {}).get("direction")
    macd = (rep.get("indicators") or {}).get("macd_line")
    p = rep["price"]
    daychg = (p["last"] / p["day_open"] - 1) * 100 if p.get("day_open") else None

    if bias == "neutral":
        return "NONE", "neutral (Gate E)"
    if rvol is not None and rvol < 0.8:
        return "NONE", f"RVOL {rvol} < 0.8"
    if bias in ("long", "long-on-pullback"):
        if brk.get("direction") == "up" and brk.get("extended_past_level") and not brk.get("fresh"):
            return "NONE", "stale extended breakout (Gate D)"
        if rs_veto_long(rep):
            return "NONE", "RS mild-weak long veto (C2)"
        return ("IMMEDIATE_LONG" if bias == "long" else "LIMIT_LONG"), bias
    if bias == "short":
        if stdir == "up" and macd is not None and macd > 0:
            return "SWINGLOW_SHORT", "FINOPB veto -> swing-low trigger (Gate B)"
        if daychg is not None and daychg < -3:
            return "NONE", f"corpse {daychg:.1f}% (Gate I)"
        return "IMMEDIATE_SHORT", "short"
    if bias == "short-on-breakdown":
        if daychg is not None and daychg < -3:
            return "NONE", f"corpse {daychg:.1f}% (Gate I)"
        return "BREAKDOWN_SHORT", "short-on-breakdown"
    return "NONE", bias


def decide_v2(rep):
    desk = rep.get("institutional_desk") or {}
    if "error" in desk:
        return "NONE", desk["error"]
    verdict = desk.get("computed_verdict_hint", "")
    side = desk.get("trade_side")
    gates = desk.get("validated_gates") or {}
    if gates.get("finopb_veto"):
        # verdict says "WAIT / SHORT-ON-BREAKDOWN only" — same swing-low trigger as v1's Gate B
        fails = desk.get("no_trade_filters_failed") or []
        if any("RVOL" in f for f in fails):
            return "NONE", "FINOPB but RVOL<0.8"
        return "SWINGLOW_SHORT", "FINOPB -> swing-low trigger (Gate B)"
    if verdict.startswith("WAIT") or verdict.startswith("NO TRADE"):
        return "NONE", verdict
    if gates.get("corpse_reject"):
        return "NONE", "corpse -> bounce only (Gate I)"
    if side == "long" and rs_veto_long(rep):
        return "NONE", "RS mild-weak long veto (L3 hard rule)"
    if verdict.startswith("BUY NOW"):
        return "IMMEDIATE_LONG", verdict
    if verdict.startswith("SHORT NOW"):
        return "IMMEDIATE_SHORT", verdict
    if verdict.startswith("BUY ON PULLBACK"):
        return "LIMIT_LONG", verdict
    if verdict.startswith("SHORT ON BREAKDOWN"):
        return "BREAKDOWN_SHORT", verdict
    return "NONE", verdict


# ---------------- main ----------------

def main():
    m1_raw = json.load(open(BARS_1M))["bars"]
    syms = sorted(m1_raw.keys())
    data = fetch_all(syms)

    grid = [f"{h:02d}:{m:02d}" for h in range(9, 15) for m in (0, 15, 30, 45)
            if f"{h:02d}:{m:02d}" >= "09:45" and f"{h:02d}:{m:02d}" <= "14:45"]
    rng = random.Random(20260730)
    cps = sorted(rng.sample(grid, 8))
    print(f"checkpoints (random, seed 20260730): {cps}\n")

    rows = []
    for s in syms:
        m15_all = data[s]["m15"]
        daily = data[s]["daily"]
        days = v2.group_by_day(m15_all)
        if DAY not in days:
            print(f"!! {s}: no {DAY} in yahoo 15m — skipped")
            continue
        dk = list(days.keys())
        di = dk.index(DAY)
        day15 = days[DAY]
        hist = [b for k in dk[max(0, di - HIST_DAYS):di] for b in days[k]]
        vwaps = running_vwap_series(day15)
        daily_hist = [b for b in daily if b.date[:10] < DAY]
        m1 = m1_raw[s]

        for cp in cps:
            today_now = [b for b in day15 if bar_time(b) < cp]
            if len(today_now) < 2:
                continue
            cp_idx = len(today_now)
            bars_now = hist + today_now
            dtrunc = daily_hist + [synth_daily(DAY, today_now)]
            mkt = market_ctx(data, cp)
            row = {"sym": s, "cp": cp}
            for tag, mod, decider in (("v1", v1, decide_v1), ("v2", v2, decide_v2)):
                try:
                    rep = mod.build_report(s, f"{s}.NS", "yahoo_intraday", list(bars_now),
                                           "15m", [], list(dtrunc), None, dict(mkt))
                    if tag == "v2":
                        rep["institutional_desk"] = v2.institutional_desk_block(rep)
                    act, why = decider(rep)
                    t = exec_action(act, rep, day15, vwaps, cp, m1, cp_idx) if act != "NONE" else {"skip": why}
                    t["why"] = why
                    t["bias"] = rep["intraday_structure"]["directional_bias"]
                    row[tag] = t
                except Exception as e:
                    row[tag] = {"skip": f"engine error {e}", "why": "error", "bias": "?"}
            rows.append(row)

    # ---------- report ----------
    def agg(tag, sel=None):
        ts = [r[tag] for r in (sel or rows) if r.get(tag) and "side" in r[tag]]
        if not ts:
            return "n=0"
        w = sum(1 for t in ts if t["res"] == "TARGET")
        st = sum(1 for t in ts if t["res"] == "STOP")
        e = len(ts) - w - st
        tot = sum(t["pnl"] for t in ts)
        rs = tot / 100 * NOTIONAL
        return (f"n={len(ts):2}  TGT={w} STOP={st} EOD={e}  win%={w / len(ts) * 100:3.0f}  "
                f"totP&L={tot:+.2f}%  (Rs {rs:+,.0f} @1L/trade)")

    print("=" * 100)
    print("PER-DECISION LOG   (entry = closed-bar last; same exit policy both sides)")
    print("=" * 100)
    for r in rows:
        line = f"{r['sym']:10} {r['cp']}  "
        for tag in ("v1", "v2"):
            t = r.get(tag, {})
            if "side" in t:
                line += (f"| {tag}: {t['side'][:1]}{'*' if t.get('kind') else ' '} "
                         f"e{t['entry']:.1f}->{t['res'][:4]:4} {t['pnl']:+5.2f}% ")
            else:
                line += f"| {tag}: --   {str(t.get('skip', ''))[:38]:38} "
        print(line)

    print()
    print("=" * 100)
    print("AGGREGATE — every (symbol, checkpoint) decision")
    print("=" * 100)
    print(f"  v1  {agg('v1')}")
    print(f"  v2  {agg('v2')}")

    # divergences
    div = [r for r in rows if ("side" in r.get("v1", {})) != ("side" in r.get("v2", {}))]
    print(f"\nDIVERGENT decisions (one traded, the other didn't): {len(div)}")
    for r in div:
        t1, t2 = r.get("v1", {}), r.get("v2", {})
        who = "v1" if "side" in t1 else "v2"
        t = t1 if "side" in t1 else t2
        other = t2 if "side" in t1 else t1
        print(f"  {r['sym']:10} {r['cp']}  {who} traded {t['side']} -> {t['res']} {t['pnl']:+.2f}%  "
              f"| other skipped: {str(other.get('skip', ''))[:50]}")

    # first-trade-per-symbol portfolio view
    print("\n" + "=" * 100)
    print("PORTFOLIO VIEW — first trade per symbol per skill (like live: one position per name)")
    print("=" * 100)
    for tag in ("v1", "v2"):
        first, seen = [], set()
        for r in rows:
            t = r.get(tag, {})
            if r["sym"] not in seen and "side" in t:
                seen.add(r["sym"])
                first.append(r)
        sel = [{"sym": r["sym"], tag: r[tag]} for r in first]
        print(f"  {tag}: {agg(tag, sel)}")
        for r in first:
            t = r[tag]
            print(f"     {r['sym']:10} {r['cp']} {t['side']:5} e{t['entry']:.2f} sl{t['stop']:.2f} "
                  f"tg{t['target']:.2f} -> {t['res']:6} {t['pnl']:+.2f}%  [{t['why'][:45]}]")

    json.dump(rows, open(os.path.join(HERE, "ab_v1_v2_0730.json"), "w"), default=str, indent=1)
    print(f"\nfull rows -> scratchpad/ab_v1_v2_0730.json")


if __name__ == "__main__":
    main()
