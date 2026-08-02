"""How much does the production decision engine vary run-to-run on the SAME input?

This is the control every A/B in this session was missing. If the skill returns a different action
for a byte-identical payload some of the time, then a single call per arm cannot attribute a
changed decision to a config — the change may just be the sampler.

Eight repeats of one payload, nothing varying but the call itself.
"""
import json, os, subprocess, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/mohdsuhel/ai-mini-projects/autoIntraday")
from skill_decision_engine import SkillDecisionEngine

PY_SA = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python"
ENGINE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_intraday_2.py"
SKILL = os.path.expanduser("~/.claude/skills/intraday-analyst-2/SKILL.md")
BOOK = {"open_positions": 0, "max_positions": 5}
N = int(os.environ.get("N", "8"))

CASES = [("HINDUNILVR", "2026-07-28 09:45"),   # the control point that looked like a config win
         ("BALKRISIND", "2026-07-31 11:00")]   # a point where the arms all agreed


def call(args):
    sym, p, i = args
    try:
        eng = SkillDecisionEngine(use_web_search=False, skill_path=SKILL, model="claude-opus-4-8")
        d = eng.decide(sym, p, position=None, book=BOOK)
        return {"action": d.action, "q": d.trade_quality, "conf": d.confidence,
                "entry": d.entry, "stop": d.stop_loss}
    except Exception as e:
        return {"action": f"ERR:{type(e).__name__}", "q": None, "conf": None}


for sym, asof in CASES:
    r = subprocess.run([PY_SA, ENGINE, "-s", sym, "--source", "yahoo", "--asof", asof],
                       capture_output=True, text=True, timeout=900)
    p = json.loads(r.stdout)
    with ThreadPoolExecutor(max_workers=3) as ex:
        out = list(ex.map(call, [(sym, p, i) for i in range(N)]))
    acts = Counter(o["action"] for o in out)
    confs = [o["conf"] for o in out if o["conf"] is not None]
    quals = [o["q"] for o in out if o["q"] is not None]
    print(f"\n{sym} @ {asof}  — {N} calls, ONE identical payload")
    print(f"  actions   : {dict(acts)}")
    print(f"  distinct  : {len(acts)}  "
          f"-> {'DETERMINISTIC' if len(acts) == 1 else 'NON-DETERMINISTIC'}")
    if confs:
        print(f"  confidence: {min(confs)}-{max(confs)} (spread {max(confs)-min(confs)})")
    if quals:
        print(f"  quality   : {min(quals)}-{max(quals)} (spread {max(quals)-min(quals)})")
    ent = [o.get("entry") for o in out if o.get("entry")]
    if ent:
        print(f"  entry     : {min(ent)}-{max(ent)}")
