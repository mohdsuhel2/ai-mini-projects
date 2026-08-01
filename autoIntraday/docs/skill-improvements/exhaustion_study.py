"""Does exhaustion actually precede reversal? The stage-to-direction study.

This is the measurement I said was missing when the lifecycle went in. It asks the only question
that matters about this engine:

  - when BULLISH exhaustion is terminal, does price actually FALL over the next few candles?
  - when BEARISH exhaustion is terminal, does price actually RISE?

Point-in-time by construction: at bar i the engine sees bars[:i+1] and nothing else. Forward
return is measured from the close of bar i, over the next 3 and 6 bars, which is the "next several
5-minute or 15-minute candles" horizon the spec names.

Indicators are computed locally from the same bars, so no lookahead can leak in through a
precomputed series.
"""
import json, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, "/Users/mohdsuhel/ai-mini-projects/autoIntraday")
from exhaustion_engine import analyse
from reversal_radar import Candle

BARS = json.load(open("universe_5m.json"))
TF = int(sys.argv[1]) if len(sys.argv) > 1 else 15        # minutes per candle
AGG = TF // 5


def to_tf(bars):
    out, buf = [], []
    for b in bars:
        buf.append(b)
        if len(buf) == AGG:
            out.append(Candle(buf[0]["t"], buf[0]["o"], max(x["h"] for x in buf),
                              min(x["l"] for x in buf), buf[-1]["c"],
                              sum(x.get("v") or 0 for x in buf)))
            buf = []
    return out


def rsi_series(closes, n=14):
    out = []
    for j in range(len(closes)):
        w = closes[:j + 1]
        if len(w) < n + 1:
            out.append(None); continue
        g = l = 0.0
        for a, b in zip(w[-n - 1:-1], w[-n:]):
            d = b - a
            g += max(0.0, d); l += max(0.0, -d)
        out.append(100.0 if l == 0 else 100 - 100 / (1 + (g / n) / (l / n)))
    return [x for x in out if x is not None]


def ema(vals, n):
    if not vals:
        return None
    k, e = 2 / (n + 1), vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def macd_hist(closes):
    if len(closes) < 26:
        return None
    out = []
    for j in range(26, len(closes) + 1):
        w = closes[:j]
        line = ema(w, 12) - ema(w, 26)
        out.append(line)
    if len(out) < 9:
        return None
    return [out[i] - ema(out[max(0, i - 8):i + 1], 9) for i in range(len(out))]


rows = []
for sym, days in BARS.items():
    for day, raw in sorted(days.items()):
        c = to_tf(raw)
        if len(c) < 14:
            continue
        closes_all = [x.c for x in c]
        for i in range(10, len(c) - 1):
            win = c[:i + 1]
            cl = closes_all[:i + 1]
            tp_v = [( (x.h + x.l + x.c) / 3 * (x.v or 0) ) for x in win]
            vol = sum(x.v or 0 for x in win)
            vwap = (sum(tp_v) / vol) if vol else None
            atr = st.mean([x.h - x.l for x in win[-14:]]) or None
            sd = st.pstdev(cl[-20:]) if len(cl) >= 20 else None
            mid = st.mean(cl[-20:]) if len(cl) >= 20 else None
            bb = ((cl[-1] - (mid - 2 * sd)) / (4 * sd)) if (sd and sd > 0) else None
            vols = [x.v for x in win if x.v]
            rvol = (vols[-1] / st.mean(vols[:-1])) if len(vols) >= 4 and st.mean(vols[:-1]) else None
            hi_i = max(range(len(win)), key=lambda k: win[k].h)
            lo_i = min(range(len(win)), key=lambda k: win[k].l)

            out = analyse(win, rsi_series=rsi_series(cl), macd_hist=macd_hist(cl), vwap=vwap,
                          ema20=ema(cl[-20:], 20), ema50=ema(cl, 50) if len(cl) >= 30 else None,
                          ema200=None, atr=atr, bb_percent_b=bb, rvol=rvol,
                          bars_since_day_high=len(win) - 1 - hi_i,
                          bars_since_day_low=len(win) - 1 - lo_i)
            s = out["summary"]
            px = win[-1].c
            fwd3 = (c[min(i + 3, len(c) - 1)].c - px) / px * 100
            fwd6 = (c[min(i + 6, len(c) - 1)].c - px) / px * 100
            eod = (c[-1].c - px) / px * 100
            rows.append({"sym": sym, "day": day, "i": i,
                         "trend": s["current_trend"], "strength": s["trend_strength"],
                         "bull": s["bullish_exhaustion_score"], "bear": s["bearish_exhaustion_score"],
                         "bull_stage": out["bullish_exhaustion"]["stage"],
                         "bear_stage": out["bearish_exhaustion"]["stage"],
                         "bull_fams": out["bullish_exhaustion"]["family_count"],
                         "bear_fams": out["bearish_exhaustion"]["family_count"],
                         "expected": s["expected_direction"], "opp": s["opportunity"],
                         "rev": s["reversal_probability"], "conf": s["confidence"],
                         "fwd3": fwd3, "fwd6": fwd6, "eod": eod})

json.dump(rows, open(f"exhaustion_study_{TF}m.json", "w"))
print(f"{len(rows)} readings, {TF}m candles, {len(BARS)} symbols\n")


def table(title, key, rows_, want, order=None):
    """`want` is the sign we EXPECT if the engine works: -1 means price should fall."""
    print(f"=== {title} ===")
    g = defaultdict(list)
    for r in rows_:
        g[r[key]].append(r)
    keys = order or sorted(g, key=lambda k: -len(g[k]))
    for k in keys:
        v = g.get(k) or []
        if len(v) < 15:
            continue
        f3 = st.mean(r["fwd3"] for r in v)
        f6 = st.mean(r["fwd6"] for r in v)
        hit = sum(1 for r in v if (r["fwd6"] < 0) == (want < 0)) / len(v) * 100
        print(f"  {str(k):<28} n={len(v):<5} fwd3 {f3:+.3f}%  fwd6 {f6:+.3f}%  "
              f"correct-direction {hit:.0f}%")
    print()


BULL_ORDER = ["fresh_trend", "healthy_trend", "mature_trend", "early_exhaustion",
              "distribution", "high_probability_reversal"]
BEAR_ORDER = ["fresh_trend", "healthy_trend", "mature_trend", "early_exhaustion",
              "accumulation", "high_probability_reversal"]

print("Baseline: mean fwd6 across everything = "
      f"{st.mean(r['fwd6'] for r in rows):+.3f}%  "
      f"(down {sum(1 for r in rows if r['fwd6'] < 0)/len(rows)*100:.0f}%)\n")

table("BULLISH exhaustion stage -> should FALL", "bull_stage", rows, -1, BULL_ORDER)
table("BEARISH exhaustion stage -> should RISE", "bear_stage", rows, +1, BEAR_ORDER)
table("expected_direction", "expected", rows, -1)
table("opportunity", "opp", rows, -1)

print("=== the two calls the engine actually makes ===")
for name, sel, want in (
        ("bearish_reversal (short)", lambda r: r["expected"] == "bearish_reversal", -1),
        ("bullish_reversal (long)", lambda r: r["expected"] == "bullish_reversal", +1),
        ("continuation_up", lambda r: r["expected"] == "continuation_up", +1),
        ("continuation_down", lambda r: r["expected"] == "continuation_down", -1)):
    v = [r for r in rows if sel(r)]
    if not v:
        print(f"  {name:<26} n=0"); continue
    edge = st.mean(r["fwd6"] for r in v) * (1 if want > 0 else -1)
    hit = sum(1 for r in v if (r["fwd6"] > 0) == (want > 0)) / len(v) * 100
    print(f"  {name:<26} n={len(v):<5} edge {edge:+.3f}%/trade  right {hit:.0f}%")

print("\n=== does confluence help? (bearish_reversal calls by family count) ===")
for f in range(0, 7):
    v = [r for r in rows if r["expected"] == "bullish_reversal" and r["bear_fams"] == f]
    if len(v) < 15:
        continue
    print(f"  {f} families  n={len(v):<5} fwd6 {st.mean(r['fwd6'] for r in v):+.3f}%  "
          f"up {sum(1 for r in v if r['fwd6']>0)/len(v)*100:.0f}%")
