"""Score the paired lifecycle A/B. Only the pairs that DISAGREE carry information."""
import json
from collections import Counter

rows = json.load(open("lifecycle_ab_results.json"))
n = len(rows)
changed = [r for r in rows if r["off"]["action"] != r["on"]["action"]]

off_total = sum(r["off_pnl"] for r in rows)
on_total = sum(r["on_pnl"] for r in rows)

print(f"pairs: {n}   decisions changed: {len(changed)} ({len(changed)/max(n,1)*100:.0f}%)\n")

print("=== whole sample (pct move, 1 unit per trade, no-trade = 0) ===")
print(f"  lifecycle OFF : {off_total:+.2f}%   trades {sum(1 for r in rows if r['off_side']!='-')}")
print(f"  lifecycle ON  : {on_total:+.2f}%   trades {sum(1 for r in rows if r['on_side']!='-')}")
print(f"  delta         : {on_total-off_total:+.2f}%")

if changed:
    d_off = sum(r["off_pnl"] for r in changed)
    d_on = sum(r["on_pnl"] for r in changed)
    print(f"\n=== the {len(changed)} pairs where it actually changed the call ===")
    print(f"  OFF would have made : {d_off:+.2f}%")
    print(f"  ON  actually made   : {d_on:+.2f}%")
    print(f"  delta               : {d_on-d_off:+.2f}%")
    better = sum(1 for r in changed if r["on_pnl"] > r["off_pnl"])
    worse = sum(1 for r in changed if r["on_pnl"] < r["off_pnl"])
    print(f"  changes that helped : {better} / hurt: {worse} / neutral: {len(changed)-better-worse}")
    print("\n  detail:")
    for r in sorted(changed, key=lambda x: x["on_pnl"] - x["off_pnl"]):
        act = r["actual"]["move_pct"] if r["actual"] else float("nan")
        print(f"   {r['symbol']:<11} {r['day']}  {r['lifecycle_stage']:>18}  "
              f"{r['off']['action']:<20}({r['off_pnl']:+.2f}) -> "
              f"{r['on']['action']:<20}({r['on_pnl']:+.2f})  "
              f"delta {r['on_pnl']-r['off_pnl']:+.2f}  actual {act:+.2f}%")

print("\n=== confidence / quality shift on the pairs that AGREED ===")
same = [r for r in rows if r["off"]["action"] == r["on"]["action"]]
if same:
    dc = sum((r["on"]["conf"] or 0) - (r["off"]["conf"] or 0) for r in same) / len(same)
    dq = sum((r["on"]["quality"] or 0) - (r["off"]["quality"] or 0) for r in same) / len(same)
    print(f"  mean confidence delta: {dc:+.1f}   mean quality delta: {dq:+.1f}   (n={len(same)})")

print("\n=== does the stage separate outcome at all? ===")
by = {}
for r in rows:
    if r["actual"]:
        by.setdefault(r["lifecycle_stage"], []).append(r["actual"]["move_pct"])
for st, v in sorted(by.items(), key=lambda x: -len(x[1])):
    dn = sum(1 for m in v if m < 0)
    print(f"  {st:>18}  n={len(v):<3} mean to 15:20 {sum(v)/len(v):+.3f}%  down {dn/len(v)*100:.0f}%")

print("\n=== action mix ===")
print("  OFF:", dict(Counter(r["off"]["action"] for r in rows)))
print("  ON :", dict(Counter(r["on"]["action"] for r in rows)))
