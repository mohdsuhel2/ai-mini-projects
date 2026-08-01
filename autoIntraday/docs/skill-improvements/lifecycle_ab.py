"""Paired A/B backtest of the trend-lifecycle wiring.

The question is narrow and the design answers exactly it: for the SAME symbol, SAME date, SAME
point-in-time payload, does adding the `trend_lifecycle` block change what the skill decides, and
are the changed decisions better or worse?

Pairing is the whole point. Both arms see identical data, identical prompt, identical model — the
only difference is the one key I added. Anything that moves is attributable to it.

Point-in-time throughout: the v2 engine runs with --asof, WebSearch is off (a model searching a
past date sees the future), and the outcome comes from bars the skill never sees.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import subprocess

sys.path.insert(0, "/Users/mohdsuhel/ai-mini-projects/autoIntraday")
from orchestrator import _with_lifecycle
from skill_decision_engine import SkillDecisionEngine

PY_SA = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python"
ENGINE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_intraday_2.py"
SKILL = os.path.expanduser("~/.claude/skills/intraday-analyst-2/SKILL.md")
MODEL = os.environ.get("BT_MODEL", "claude-opus-4-8")
BARS1M = json.load(open("bars1m_bt.json"))

ENTRY_ACTIONS = ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT", "BUY_ON_VWAP_RETEST")
SHORT_ACTIONS = ("SHORT_NOW", "SELL_NOW", "SHORT_ON_BREAKDOWN")
DECIDE_AT = os.environ.get("BT_TIME", "11:00")
BOOK = {"open_positions": 0, "max_positions": 5}


def payload(sym, asof):
    r = subprocess.run([PY_SA, ENGINE, "-s", sym, "--source", "yahoo", "--asof", asof],
                       capture_output=True, text=True, timeout=900)
    if not r.stdout.strip():
        raise RuntimeError(r.stderr[-200:])
    return json.loads(r.stdout)


def decide(sym, p):
    eng = SkillDecisionEngine(use_web_search=False, skill_path=SKILL, model=MODEL)
    d = eng.decide(sym, p, position=None, book=BOOK)
    return {"action": d.action, "conf": d.confidence, "quality": d.trade_quality,
            "entry": d.entry, "stop": d.stop_loss, "target": d.target1, "rr": d.risk_reward}


def side_of(action):
    a = str(action).upper()
    return "LONG" if a in ENTRY_ACTIONS else ("SHORT" if a in SHORT_ACTIONS else "-")


def outcome(sym, day, frm=DECIDE_AT, to="15:20"):
    bars = (BARS1M.get(sym) or {}).get(day) or []
    mn = lambda t: int(t.split(":")[0]) * 60 + int(t.split(":")[1])
    seg = [b for b in bars if mn(frm) <= mn(b["t"]) <= mn(to)]
    if len(seg) < 5:
        return None
    e, x = seg[0]["c"], seg[-1]["c"]
    return {"entry": e, "exit": x, "move_pct": (x - e) / e * 100,
            "max_up_pct": (max(b["h"] for b in seg) - e) / e * 100,
            "max_dn_pct": (min(b["l"] for b in seg) - e) / e * 100}


def pnl(side, o):
    if not o or side == "-":
        return 0.0                      # no trade taken is a real, correct outcome: zero
    return o["move_pct"] if side == "LONG" else -o["move_pct"]


def one(spec):
    sym, day = spec
    asof = f"{day} {DECIDE_AT}"
    t0 = time.time()
    try:
        base = payload(sym, asof)
        annotated = _with_lifecycle(lambda s: base)(sym)
        lc = annotated.get("trend_lifecycle")
        a, b = decide(sym, base), decide(sym, annotated)
        o = outcome(sym, day)
        row = {"symbol": sym, "day": day, "asof": asof,
               "lifecycle_stage": (lc or {}).get("stage"),
               "lifecycle_prob": (lc or {}).get("reversal_probability"),
               "off": a, "on": b, "actual": o,
               "off_side": side_of(a["action"]), "on_side": side_of(b["action"]),
               "off_pnl": pnl(side_of(a["action"]), o), "on_pnl": pnl(side_of(b["action"]), o),
               "secs": round(time.time() - t0)}
        flag = "CHANGED" if a["action"] != b["action"] else "same"
        print(f"[{sym} {day}] {lc and lc['stage']:>18} | off={a['action']:<20} "
              f"on={b['action']:<20} {flag:<8} actual={o and o['move_pct']:+.2f}% "
              f"({row['secs']}s)", flush=True)
        return row
    except Exception as e:
        print(f"[{sym} {day}] FAILED {type(e).__name__}: {str(e)[:140]}", flush=True)
        return None


if __name__ == "__main__":
    syms = ["RELIANCE", "KALYANKJIL", "INDUSINDBK"]
    days = sorted((BARS1M["RELIANCE"]).keys())[-12:]
    specs = [(s, d) for d in days for s in syms]
    print(f"{len(specs)} pairs ({len(specs)*2} skill calls) at {DECIDE_AT}, model {MODEL}",
          flush=True)
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = [r for r in ex.map(one, specs) if r]
    json.dump(rows, open("lifecycle_ab_results.json", "w"), indent=1)
    print(f"\nwrote {len(rows)} pairs")
