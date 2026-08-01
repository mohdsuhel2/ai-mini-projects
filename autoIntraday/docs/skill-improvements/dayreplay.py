"""One stock, one whole day, every 15 minutes, four config arms, real skill calls.

For each decision point the SAME point-in-time payload is sent to the production decision engine
four times — with the two context engines off, each on alone, and both on. Everything else is
byte-identical, so any difference in the decision belongs to the config and nothing else.

Point-in-time throughout: the v2 engine runs with --asof, WebSearch is disabled (a model
searching a past date sees the future), and every outcome comes from bars the skill never saw.

Outcomes are measured on the trade the skill actually claims to be making: enter now, hold to the
15:20 square-off. The +1h column is there to show whether a call was early or simply wrong.
"""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/mohdsuhel/ai-mini-projects/autoIntraday")
from orchestrator import _with_exhaustion, _with_lifecycle
from skill_decision_engine import SkillDecisionEngine

PY_SA = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python"
ENGINE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze_intraday_2.py"
SKILL = os.path.expanduser("~/.claude/skills/intraday-analyst-2/SKILL.md")
MODEL = os.environ.get("BT_MODEL", "claude-opus-4-8")

SYM = os.environ.get("BT_SYM", "BALKRISIND")
DAY = os.environ.get("BT_DAY", "2026-07-31")
TIMES = [f"{h:02d}:{m:02d}" for h in range(9, 15) for m in (0, 15, 30, 45)
         if (h, m) >= (9, 45) and (h, m) <= (15, 0)] + ["15:00"]
TIMES = sorted(set(t for t in TIMES if "09:45" <= t <= "15:00"))

BARS = json.load(open("universe_5m.json"))[SYM][DAY]
LONG = ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT", "BUY_ON_VWAP_RETEST")
SHORT = ("SHORT_NOW", "SELL_NOW", "SHORT_ON_BREAKDOWN")
BOOK = {"open_positions": 0, "max_positions": 5}

ARMS = {
    "baseline":   (False, False),      # what production runs today
    "lifecycle":  (True, False),
    "exhaustion": (False, True),
    "both":       (True, True),
}


def mn(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def price_at(t):
    seg = [b for b in BARS if mn(b["t"]) <= mn(t)]
    return seg[-1]["c"] if seg else None


def outcome(t):
    """What the tape did after the decision — the skill never sees any of this."""
    px = price_at(t)
    seg = [b for b in BARS if mn(t) <= mn(b["t"]) <= mn("15:20")]
    if not px or len(seg) < 2:
        return None
    hr = [b for b in seg if mn(b["t"]) <= mn(t) + 60] or seg
    return {"px": px,
            "to_close_pct": (seg[-1]["c"] - px) / px * 100,
            "to_1h_pct": (hr[-1]["c"] - px) / px * 100,
            "max_up_pct": (max(b["h"] for b in seg) - px) / px * 100,
            "max_dn_pct": (min(b["l"] for b in seg) - px) / px * 100}


def payload(asof):
    r = subprocess.run([PY_SA, ENGINE, "-s", SYM, "--source", "yahoo", "--asof", asof],
                       capture_output=True, text=True, timeout=900)
    if not r.stdout.strip():
        raise RuntimeError(r.stderr[-200:])
    return json.loads(r.stdout)


def variant(base, lifecycle_on, exhaustion_on):
    f = _with_exhaustion(_with_lifecycle(lambda s: base, lambda: lifecycle_on),
                         lambda: exhaustion_on)
    return f(SYM)


def side_of(a):
    a = str(a).upper()
    return "LONG" if a in LONG else ("SHORT" if a in SHORT else "-")


def pnl(side, o, field="to_close_pct"):
    if not o or side == "-":
        return 0.0
    return o[field] if side == "LONG" else -o[field]


def one(t):
    asof = f"{DAY} {t}"
    try:
        base = payload(asof)
    except Exception as e:
        print(f"[{t}] payload failed: {e}", flush=True)
        return None
    o = outcome(t)
    row = {"time": t, "actual": o, "arms": {}}
    lc = ex = None
    for arm, (l_on, e_on) in ARMS.items():
        p = variant(base, l_on, e_on)
        if arm == "lifecycle":
            lc = (p.get("trend_lifecycle") or {}).get("stage")
        if arm == "exhaustion":
            s = (p.get("trend_exhaustion") or {}).get("summary") or {}
            ex = (s.get("opportunity"), s.get("expected_direction"),
                  s.get("bullish_exhaustion_score"), s.get("bearish_exhaustion_score"))
        try:
            eng = SkillDecisionEngine(use_web_search=False, skill_path=SKILL, model=MODEL)
            d = eng.decide(SYM, p, position=None, book=BOOK)
            row["arms"][arm] = {"action": d.action, "q": d.trade_quality, "conf": d.confidence,
                                "entry": d.entry, "stop": d.stop_loss, "t1": d.target1,
                                "rr": d.risk_reward, "side": side_of(d.action),
                                "pnl_close": pnl(side_of(d.action), o),
                                "pnl_1h": pnl(side_of(d.action), o, "to_1h_pct")}
        except Exception as e:
            print(f"[{t}/{arm}] FAILED {type(e).__name__}: {str(e)[:100]}", flush=True)
            row["arms"][arm] = None
    row["lifecycle_stage"], row["exhaustion"] = lc, ex
    acts = " | ".join(f"{a[:4]}={(row['arms'][a] or {}).get('action','ERR')[:14]}" for a in ARMS)
    print(f"[{t}] {acts}  actual->close {o and o['to_close_pct']:+.2f}%", flush=True)
    return row


if __name__ == "__main__":
    print(f"{SYM} {DAY}: {len(TIMES)} decision points x {len(ARMS)} arms "
          f"= {len(TIMES)*len(ARMS)} skill calls, model {MODEL}\n", flush=True)
    with ThreadPoolExecutor(max_workers=5) as ex_:
        rows = [r for r in ex_.map(one, TIMES) if r]
    json.dump({"symbol": SYM, "day": DAY, "rows": rows},
              open(f"dayreplay_{SYM}_{DAY}.json", "w"), indent=1)
    print(f"\nwrote {len(rows)} decision points")
