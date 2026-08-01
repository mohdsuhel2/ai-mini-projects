"""Per-call comparison: what each config said, what the tape then did, and who was right."""
import json, sys

d = json.load(open(sys.argv[1] if len(sys.argv) > 1
                   else "dayreplay_BALKRISIND_2026-07-31.json"))
rows, ARMS = d["rows"], ["baseline", "lifecycle", "exhaustion", "both"]
print(f"{d['symbol']}  {d['day']}   {len(rows)} decision points, real skill calls\n")

# ---- per-call ---------------------------------------------------------------------------------
print("time   price     to-close  to-1h  | " + " ".join(f"{a[:9]:<20}" for a in ARMS))
print("-" * 128)
for r in rows:
    o = r["actual"] or {}
    line = (f"{r['time']}  {o.get('px', 0):>8.1f}  {o.get('to_close_pct', 0):+7.2f}% "
            f"{o.get('to_1h_pct', 0):+6.2f}% |")
    for a in ARMS:
        v = r["arms"].get(a)
        line += f" {(v or {}).get('action', 'ERR')[:13]:<13}{(v or {}).get('q', '-'):>3}/" \
                f"{(v or {}).get('conf', '-'):<3}"
    print(line)

# ---- what the engines saw ---------------------------------------------------------------------
print("\n=== what the two context engines were saying (the extra input each arm got) ===")
print("time   lifecycle_stage      exhaustion opportunity          expected        bull/bear")
for r in rows:
    ex = r.get("exhaustion") or (None, None, None, None)
    print(f"{r['time']}  {str(r.get('lifecycle_stage')):<20} {str(ex[0]):<28} "
          f"{str(ex[1]):<15} {ex[2]}/{ex[3]}")

# ---- scoreboard -------------------------------------------------------------------------------
print("\n=== scoreboard (1 unit per trade, no-trade = 0, held to 15:20) ===")
print(f"{'arm':<12} {'trades':>6} {'longs':>6} {'shorts':>7} {'P&L%':>8} {'avg/trade':>10} "
      f"{'wins':>6} {'losses':>7}")
for a in ARMS:
    v = [r["arms"][a] for r in rows if r["arms"].get(a)]
    tr = [x for x in v if x["side"] != "-"]
    tot = sum(x["pnl_close"] for x in v)
    w = sum(1 for x in tr if x["pnl_close"] > 0)
    l = sum(1 for x in tr if x["pnl_close"] < 0)
    avg = (tot / len(tr)) if tr else 0.0
    print(f"{a:<12} {len(tr):>6} {sum(1 for x in tr if x['side']=='LONG'):>6} "
          f"{sum(1 for x in tr if x['side']=='SHORT'):>7} {tot:>+8.2f} {avg:>+10.2f} "
          f"{w:>6} {l:>7}")

# ---- where the configs actually disagreed -----------------------------------------------------
print("\n=== the calls where a config CHANGED the decision vs baseline ===")
changed = 0
for r in rows:
    b = r["arms"].get("baseline")
    if not b:
        continue
    for a in ARMS[1:]:
        v = r["arms"].get(a)
        if v and v["action"] != b["action"]:
            changed += 1
            delta = v["pnl_close"] - b["pnl_close"]
            verdict = "BETTER" if delta > 0.01 else ("WORSE" if delta < -0.01 else "same P&L")
            print(f"  {r['time']}  {a:<11} {b['action']:<15}({b['pnl_close']:+.2f}) -> "
                  f"{v['action']:<15}({v['pnl_close']:+.2f})  {verdict:<9} "
                  f"delta {delta:+.2f}%")
if not changed:
    print("  none — every config produced the identical decision at every point")

# ---- agreement --------------------------------------------------------------------------------
print(f"\n=== agreement with baseline ===")
for a in ARMS[1:]:
    same = sum(1 for r in rows if r["arms"].get(a) and r["arms"].get("baseline")
               and r["arms"][a]["action"] == r["arms"]["baseline"]["action"])
    n = sum(1 for r in rows if r["arms"].get(a) and r["arms"].get("baseline"))
    print(f"  {a:<12} {same}/{n} identical ({same/max(n,1)*100:.0f}%)")

# ---- the day's verdict ------------------------------------------------------------------------
best = max(ARMS, key=lambda a: sum(x["pnl_close"] for x in
                                   [r["arms"][a] for r in rows if r["arms"].get(a)]))
print(f"\nbest arm on this day: {best}")
print("NOTE: one stock, one day. This shows WHAT each config does and whether it breaks "
      "anything.\n      It is not enough evidence on its own to turn a flag on.")
