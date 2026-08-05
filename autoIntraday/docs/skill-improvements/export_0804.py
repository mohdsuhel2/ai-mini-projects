"""Full export of the 2026-08-04 trading day for skill evaluation."""
import json, os, sqlite3, urllib.parse, urllib.request, collections
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-08-04"
DB = os.path.expanduser("~/.autointraday/autointraday.db")
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row


def ist(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).astimezone(IST)
    except Exception:
        return None


def hm(s):
    t = ist(s)
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else None


def on_day(s, day=DAY):
    t = ist(s)
    return bool(t) and t.date().isoformat() == day


# ---- positions --------------------------------------------------------------------------------
positions, syms = [], set()
for r in c.execute("SELECT * FROM positions ORDER BY id"):
    opened_today = on_day(r["opened_at"])
    closed_today = on_day(r["closed_at"])
    if not (opened_today or closed_today):
        continue
    d = dict(r)
    d.pop("raw_json", None)
    o, cl = ist(r["opened_at"]), ist(r["closed_at"])
    d["opened_at_ist"], d["closed_at_ist"] = hm(r["opened_at"]), hm(r["closed_at"])
    d["holding_minutes"] = round((cl - o).total_seconds() / 60) if (o and cl) else None
    # A position OPENED on a previous day and squared off this morning is carried P&L, not this
    # day's trading. Keeping them apart is the whole point — the headline number hides it.
    d["opened_on_this_day"] = opened_today
    d["pnl_belongs_to_this_day"] = opened_today
    if r["entry_price"] and r["stop_loss"]:
        d["initial_risk_pct"] = round(abs(r["entry_price"] - r["stop_loss"]) / r["entry_price"] * 100, 3)
    if r["entry_price"] and r["target_price"]:
        d["target_distance_pct"] = round(abs(r["target_price"] - r["entry_price"]) / r["entry_price"] * 100, 3)
    positions.append(d)
    syms.add(r["symbol"])

# ---- decisions --------------------------------------------------------------------------------
decisions = []
for r in c.execute("SELECT * FROM decisions ORDER BY id"):
    if not on_day(r["created_at"]):
        continue
    d = dict(r)
    d["at_ist"] = hm(r["created_at"])
    raw = d.pop("raw_json", None)
    try:
        d["skill_output"] = json.loads(raw) if raw else None
    except Exception:
        d["skill_output"] = raw
    decisions.append(d)
    syms.add(r["symbol"])

# ---- orders -----------------------------------------------------------------------------------
orders = []
for r in c.execute("SELECT * FROM orders ORDER BY id"):
    if not on_day(r["placed_at"]):
        continue
    d = dict(r)
    d["at_ist"] = hm(r["placed_at"])
    raw = d.pop("raw_json", None)
    try:
        d["broker_raw"] = json.loads(raw) if raw else None
    except Exception:
        d["broker_raw"] = raw
    orders.append(d)

# ---- cycles -----------------------------------------------------------------------------------
cycles = []
for r in c.execute("SELECT * FROM job_runs ORDER BY id"):
    if not on_day(r["started_at"]):
        continue
    cycles.append({"run_id": r["id"], "started_ist": hm(r["started_at"]),
                   "finished_ist": hm(r["finished_at"]), "status": r["status"],
                   "candidates": r["num_candidates"], "actions": r["num_actions"],
                   "summary": r["summary"], "error": r["error"]})

# ---- market data ------------------------------------------------------------------------------
def bars(sym, interval):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(sym + ".NS", safe="") + f"?interval={interval}&range=1mo")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        res = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        out = []
        for i, t in enumerate(res["timestamp"]):
            if q["close"][i] is None:
                continue
            dt = datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(IST)
            if dt.date().isoformat() != DAY:
                continue
            out.append({"t": dt.strftime("%H:%M"), "o": q["open"][i], "h": q["high"][i],
                        "l": q["low"][i], "c": q["close"][i], "v": q["volume"][i]})
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


bars_5m = {s: bars(s, "5m") for s in sorted(syms)}
bars_15m = {s: bars(s, "15m") for s in sorted(syms)}

# ---- outcome per position (from bars the skill never saw) --------------------------------------
def mn(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


for p in positions:
    b = bars_5m.get(p["symbol"])
    o = ist(p["opened_at"])
    if not isinstance(b, list) or not b or not o or not p["opened_on_this_day"]:
        continue
    start = o.strftime("%H:%M")
    seg = [x for x in b if mn(start) <= mn(x["t"]) <= mn("15:20")]
    if len(seg) < 2:
        continue
    e = p["entry_price"]
    long_ = p["side"] == "LONG"
    mfe = (max(x["h"] for x in seg) - e) / e * 100 if long_ else (e - min(x["l"] for x in seg)) / e * 100
    mae = (min(x["l"] for x in seg) - e) / e * 100 if long_ else (e - max(x["h"] for x in seg)) / e * 100
    to_close = (seg[-1]["c"] - e) / e * 100 * (1 if long_ else -1)
    p["market_outcome"] = {
        "max_favourable_pct": round(mfe, 3), "max_adverse_pct": round(mae, 3),
        "to_squareoff_pct": round(to_close, 3),
        "hit_its_target": bool(p["target_price"] and (
            max(x["h"] for x in seg) >= p["target_price"] if long_
            else min(x["l"] for x in seg) <= p["target_price"])),
        "hit_its_stop": bool(p["stop_loss"] and (
            min(x["l"] for x in seg) <= p["stop_loss"] if long_
            else max(x["h"] for x in seg) >= p["stop_loss"])),
        "pnl_if_held_to_squareoff_inr": round(to_close / 100 * e * p["quantity"], 2),
    }

# ---- summary ----------------------------------------------------------------------------------
own = [p for p in positions if p["opened_on_this_day"]]
carried = [p for p in positions if not p["opened_on_this_day"]]
own_closed = [p for p in own if p["status"] == "CLOSED"]
own_pnl = sum(p["realized_pnl"] or 0 for p in own_closed)
carried_pnl = sum(p["realized_pnl"] or 0 for p in carried)
still_open = [p["symbol"] for p in own if p["status"] == "OPEN"]

cfg = dict(c.execute("SELECT * FROM config LIMIT 1").fetchone())

export = {
    "export": {
        "generated_for": "intraday-analyst-2 skill evaluation — why 2026-08-04 went badly",
        "trading_date": DAY,
        "timezone": "Asia/Kolkata (IST); *_ist fields are IST, raw DB timestamps are UTC",
        "source": "~/.autointraday/autointraday.db + Yahoo intraday bars the skill never saw",
        "caveats": [
            "CRITICAL: the live system runs the intraday-analyst-2 SKILL but feeds it the V1 "
            "ENGINE payload (indicators.py hardcodes StockAnalayze/stock_analyze_intraday.py). "
            "The v1 payload has NO institutional_desk, NO recent_bars and NO indicators."
            "rsi_series. Consequences: _full_exit_target() cannot upgrade target1 to the desk "
            "ceiling so every exit leg rests at the practical first objective (~0.3-0.9%); the "
            "R:R gate judges geometry against that same near target; and reversal_radar returns "
            "insufficient_bars so its gates never fire. Judge the decisions with that in mind.",
            "Only 42 of ~66 scheduled cycles ran (first 09:26, last 14:51). The Mac sleeps after "
            "1 minute idle on battery and launchd cannot fire while asleep, so missed cycles are "
            "coalesced into one run on wake. Gaps are NOT the skill's doing.",
            "realized_pnl for a position opened on a PREVIOUS day is carried P&L. "
            "pnl_belongs_to_this_day marks which is which — the headline total hides it.",
            "4 positions are still status=OPEN in the DB. These are MIS; the broker auto-squares "
            "off ~15:20, so they were flattened in the market and the DB simply never recorded "
            "it. Their true exit is unknown to the system; market_outcome shows what the tape did.",
            "exit_reason=BROKER_SYNC means reconcile found no matching broker position and closed "
            "the DB row. It is not a decision the skill made.",
            "market_outcome is computed from 5m bars AFTER the entry — the skill never saw any "
            "of it. It is the yardstick, not an input.",
        ],
    },
    "session": {
        "mode": cfg.get("mode"), "live_strategy": cfg.get("live_strategy"),
        "skill": "~/.claude/skills/intraday-analyst-2/SKILL.md",
        "engine_actually_used": "StockAnalayze/stock_analyze_intraday.py  (V1 — see caveat)",
        "engine_the_skill_expects": "StockAnalayze/stock_analyze_intraday_2.py  (V2)",
        "decision_timeframe": "15m",
        "schedule": "09:36 -> 15:01 IST every 5 min (66 cycles); 42 actually ran",
        "square_off": "Groww auto-flattens MIS ~15:20",
    },
    "config": cfg,
    "summary": {
        "positions_opened_this_day": len(own),
        "positions_carried_in_from_previous_day": len(carried),
        "realized_pnl_this_days_own_trades": round(own_pnl, 2),
        "realized_pnl_carried_in": round(carried_pnl, 2),
        "realized_pnl_headline_total": round(own_pnl + carried_pnl, 2),
        "headline_note": "the positive headline is entirely carried P&L from 08-03; this day's "
                         "own trading lost money",
        "wins": sum(1 for p in own_closed if (p["realized_pnl"] or 0) > 0),
        "losses": sum(1 for p in own_closed if (p["realized_pnl"] or 0) < 0),
        "still_open_in_db": still_open,
        "exit_reasons": dict(collections.Counter(p["exit_reason"] for p in own_closed)),
        "symbols_traded": sorted({p["symbol"] for p in own}),
        "cycles_run": len(cycles),
        "cycles_expected": 66,
        "decisions_recorded": len(decisions),
        "decision_actions": dict(collections.Counter(d["action"] for d in decisions)),
        "orders_placed": len(orders),
    },
    "positions": positions,
    "decisions": decisions,
    "orders": orders,
    "cycles": cycles,
    "market_data": {"note": "regular session 09:15-15:30 IST; bar stamps are bucket STARTS",
                    "bars_5m": bars_5m, "bars_15m": bars_15m},
}

out = f"docs/skill-improvements/{DAY}-trading-day.json"
os.makedirs(os.path.dirname(out), exist_ok=True)
json.dump(export, open(out, "w"), indent=1, default=str)
print(f"wrote {out}  ({os.path.getsize(out)/1024:.0f} KB)")
print(f"  own trades P&L      : {own_pnl:+.2f}")
print(f"  carried in from 08-03: {carried_pnl:+.2f}")
print(f"  headline             : {own_pnl+carried_pnl:+.2f}")
print(f"  positions {len(own)} own / {len(carried)} carried; still OPEN in DB: {still_open}")
print(f"  bars fetched for {sum(1 for v in bars_5m.values() if isinstance(v, list) and v)}/{len(syms)} symbols")
