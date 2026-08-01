"""The horizon the live book actually trades: decision -> square-off, not decision -> 6 bars.

Also splits the long-reversal call by confluence, because the 15m run showed the family count
mattering more than the score did.
"""
import json, statistics as st, sys

rows = json.load(open(f"exhaustion_study_{sys.argv[1] if len(sys.argv)>1 else 15}m.json"))
base = st.mean(r["eod"] for r in rows)
print(f"{len(rows)} readings.  baseline mean move to session end: {base:+.3f}%\n")


def show(name, sel, want):
    v = [r for r in rows if sel(r)]
    if len(v) < 10:
        print(f"  {name:<38} n={len(v)} — too few to read")
        return
    for horizon in ("fwd3", "fwd6", "eod"):
        m = st.mean(r[horizon] for r in v)
        edge = m if want > 0 else -m
        hit = sum(1 for r in v if (r[horizon] > 0) == (want > 0)) / len(v) * 100
        print(f"  {name if horizon=='fwd3' else '':<38} {horizon:<5} n={len(v):<5} "
              f"edge {edge:+.3f}%  right {hit:.0f}%")
    print()


print("=== LONG reversal calls (bearish exhaustion) — the new half ===")
show("all bullish_reversal", lambda r: r["expected"] == "bullish_reversal", +1)
for f in range(3, 7):
    show(f"  ... with {f} confluence families",
         lambda r, f=f: r["expected"] == "bullish_reversal" and r["bear_fams"] == f, +1)
show("high_conviction_long_reversal",
     lambda r: r["opp"] == "high_conviction_long_reversal", +1)

print("=== SHORT reversal calls (bullish exhaustion) — duplicates the old radar ===")
show("all bearish_reversal", lambda r: r["expected"] == "bearish_reversal", -1)
for f in range(3, 7):
    show(f"  ... with {f} confluence families",
         lambda r, f=f: r["expected"] == "bearish_reversal" and r["bull_fams"] == f, -1)

print("=== continuation calls, for contrast ===")
show("continuation_up", lambda r: r["expected"] == "continuation_up", +1)
show("continuation_down", lambda r: r["expected"] == "continuation_down", -1)

print("=== raw bearish-exhaustion score deciles -> forward move (should RISE) ===")
sc = sorted(rows, key=lambda r: r["bear"])
n = len(sc) // 10
for d in range(10):
    v = sc[d * n:(d + 1) * n]
    if not v:
        continue
    print(f"  decile {d+1:<2} score {v[0]['bear']:>5.1f}-{v[-1]['bear']:>5.1f}  "
          f"n={len(v):<5} fwd6 {st.mean(r['fwd6'] for r in v):+.3f}%  "
          f"eod {st.mean(r['eod'] for r in v):+.3f}%")
