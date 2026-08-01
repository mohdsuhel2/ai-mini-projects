"""Re-run only the cells that failed transiently, with retry, and merge back.

The failures had empty stderr and the identical call succeeded on retry — rate limiting from
parallel workers, not a payload problem. Serial with backoff so it cannot recur.
"""
import json, os, subprocess, sys, time

sys.path.insert(0, "/Users/mohdsuhel/ai-mini-projects/autoIntraday")
from orchestrator import _with_exhaustion, _with_lifecycle
from skill_decision_engine import SkillDecisionEngine

PY_SA = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python"
ENGINE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_intraday_2.py"
SKILL = os.path.expanduser("~/.claude/skills/intraday-analyst-2/SKILL.md")
ARMS = {"baseline": (False, False), "lifecycle": (True, False),
        "exhaustion": (False, True), "both": (True, True)}
LONG = ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT", "BUY_ON_VWAP_RETEST")
SHORT = ("SHORT_NOW", "SELL_NOW", "SHORT_ON_BREAKDOWN")

PATH = sys.argv[1] if len(sys.argv) > 1 else "dayreplay_BALKRISIND_2026-07-31.json"
d = json.load(open(PATH))
SYM, DAY = d["symbol"], d["day"]

missing = [(r["time"], a) for r in d["rows"] for a in ARMS if not r["arms"].get(a)]
print(f"{len(missing)} cells to fill: {missing}\n")


def side_of(a):
    a = str(a).upper()
    return "LONG" if a in LONG else ("SHORT" if a in SHORT else "-")


for t, arm in missing:
    row = next(r for r in d["rows"] if r["time"] == t)
    o = row["actual"]
    p_raw = subprocess.run([PY_SA, ENGINE, "-s", SYM, "--source", "yahoo",
                            "--asof", f"{DAY} {t}"], capture_output=True, text=True, timeout=900)
    base = json.loads(p_raw.stdout)
    l_on, e_on = ARMS[arm]
    p = _with_exhaustion(_with_lifecycle(lambda s: base, lambda: l_on), lambda: e_on)(SYM)
    for attempt in range(4):
        try:
            eng = SkillDecisionEngine(use_web_search=False, skill_path=SKILL,
                                      model="claude-opus-4-8")
            dec = eng.decide(SYM, p, position=None, book={"open_positions": 0, "max_positions": 5})
            side = side_of(dec.action)
            row["arms"][arm] = {
                "action": dec.action, "q": dec.trade_quality, "conf": dec.confidence,
                "entry": dec.entry, "stop": dec.stop_loss, "t1": dec.target1,
                "rr": dec.risk_reward, "side": side,
                "pnl_close": 0.0 if side == "-" else (o["to_close_pct"] if side == "LONG"
                                                      else -o["to_close_pct"]),
                "pnl_1h": 0.0 if side == "-" else (o["to_1h_pct"] if side == "LONG"
                                                   else -o["to_1h_pct"])}
            print(f"  {t}/{arm} -> {dec.action}", flush=True)
            break
        except Exception as e:
            print(f"  {t}/{arm} attempt {attempt+1} failed: {str(e)[:80]}", flush=True)
            time.sleep(5 * (attempt + 1))
    time.sleep(2)

json.dump(d, open(PATH, "w"), indent=1)
left = [(r["time"], a) for r in d["rows"] for a in ARMS if not r["arms"].get(a)]
print(f"\nremaining gaps: {left or 'none'}")
