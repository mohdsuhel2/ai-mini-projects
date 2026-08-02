"""Combined view across every replayed stock-day: does either config earn its place?"""
import glob, json, statistics as st

ARMS = ["baseline", "lifecycle", "exhaustion", "both"]
files = sorted(glob.glob("dayreplay_*_2026-*.json"))
days = [json.load(open(f)) for f in files]

print(f"{len(days)} stock-days, "
      f"{sum(len(d['rows']) for d in days)} decision points, "
      f"{sum(len(d['rows']) for d in days) * 4} skill calls\n")

# ---- per-day scoreboard -----------------------------------------------------------------------
print(f"{'stock-day':<26} {'move':>7} | " + " ".join(f"{a:>11}" for a in ARMS))
print("-" * 82)
tot = {a: 0.0 for a in ARMS}
for d in days:
    rows = d["rows"]
    p0 = rows[0]["actual"]["px"]
    p1 = rows[-1]["actual"]["px"]
    line = f"{d['symbol'] + ' ' + d['day']:<26} {(p1-p0)/p0*100:>+6.2f}% |"
    for a in ARMS:
        v = [r["arms"][a] for r in rows if r["arms"].get(a)]
        s = sum(x["pnl_close"] for x in v)
        tot[a] += s
        line += f" {s:>+10.2f}"
    print(line)
print("-" * 82)
print(f"{'TOTAL':<26} {'':>7} |" + "".join(f" {tot[a]:>+10.2f}" for a in ARMS))

# ---- trade counts -----------------------------------------------------------------------------
print(f"\n{'arm':<12} {'trades':>7} {'longs':>6} {'shorts':>7} {'wins':>6} {'losses':>7} "
      f"{'avg/trade':>10} {'total':>8}")
for a in ARMS:
    v = [r["arms"][a] for d in days for r in d["rows"] if r["arms"].get(a)]
    tr = [x for x in v if x["side"] != "-"]
    w = sum(1 for x in tr if x["pnl_close"] > 0)
    l = sum(1 for x in tr if x["pnl_close"] < 0)
    avg = (sum(x["pnl_close"] for x in tr) / len(tr)) if tr else 0.0
    print(f"{a:<12} {len(tr):>7} {sum(1 for x in tr if x['side']=='LONG'):>6} "
          f"{sum(1 for x in tr if x['side']=='SHORT'):>7} {w:>6} {l:>7} {avg:>+10.2f} "
          f"{tot[a]:>+8.2f}")

# ---- every disagreement -----------------------------------------------------------------------
print("\n=== every call where a config CHANGED the decision vs baseline ===")
found = 0
for d in days:
    for r in d["rows"]:
        b = r["arms"].get("baseline")
        if not b:
            continue
        for a in ARMS[1:]:
            v = r["arms"].get(a)
            if v and v["action"] != b["action"]:
                found += 1
                delta = v["pnl_close"] - b["pnl_close"]
                verdict = "BETTER" if delta > 0.01 else ("WORSE" if delta < -0.01 else "no P&L")
                print(f"  {d['symbol']:<11} {d['day']} {r['time']}  {a:<11} "
                      f"{b['action']:<14}({b['pnl_close']:+.2f}) -> "
                      f"{v['action']:<14}({v['pnl_close']:+.2f})  {verdict:<7} {delta:+.2f}%")
if not found:
    print("  none")
print(f"\n  {found} changed calls out of "
      f"{sum(len(d['rows']) for d in days) * 3} config-vs-baseline comparisons")

# ---- what the exhaustion engine claimed, and whether it was right -----------------------------
print("\n=== exhaustion engine's own reversal calls (never acted on) ===")
calls = []
for d in days:
    for r in d["rows"]:
        ex = r.get("exhaustion")
        if not ex or ex[1] not in ("bearish_reversal", "bullish_reversal"):
            continue
        sgn = -1 if ex[1] == "bearish_reversal" else 1
        a = r["actual"]
        calls.append({"sym": d["symbol"], "day": d["day"], "t": r["time"], "opp": ex[0],
                      "dir": ex[1], "h1": sgn * a["to_1h_pct"], "cl": sgn * a["to_close_pct"]})
for c in calls:
    print(f"  {c['sym']:<11} {c['day']} {c['t']}  {c['opp']:<32} "
          f"1h {c['h1']:+.2f}%  close {c['cl']:+.2f}%")
for name, sel in (("LONG reversal calls", lambda c: c["dir"] == "bullish_reversal"),
                  ("SHORT reversal calls", lambda c: c["dir"] == "bearish_reversal")):
    v = [c for c in calls if sel(c)]
    if not v:
        print(f"  {name}: none fired")
        continue
    print(f"  {name}: n={len(v)}  1h {st.mean(c['h1'] for c in v):+.3f}%  "
          f"close {st.mean(c['cl'] for c in v):+.3f}%  "
          f"right-to-close {sum(1 for c in v if c['cl']>0)/len(v)*100:.0f}%")

# ---- agreement --------------------------------------------------------------------------------
print("\n=== agreement with baseline ===")
for a in ARMS[1:]:
    n = sum(1 for d in days for r in d["rows"]
            if r["arms"].get(a) and r["arms"].get("baseline"))
    same = sum(1 for d in days for r in d["rows"]
               if r["arms"].get(a) and r["arms"].get("baseline")
               and r["arms"][a]["action"] == r["arms"]["baseline"]["action"])
    print(f"  {a:<12} {same}/{n} identical ({same/max(n,1)*100:.0f}%)")

print("\n=== mean confidence ===")
for a in ARMS:
    v = [r["arms"][a]["conf"] for d in days for r in d["rows"] if r["arms"].get(a)]
    print(f"  {a:<12} {st.mean(v):.1f}")
