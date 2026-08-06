"""Skill Lab persistence — shadow skill runs that never touch the broker.

Its own tables in the shared SQLite file, like active_short_store and live_store: a separate
lifecycle that must not disturb the trading ledgers. Nothing written here is ever read by the
order path, and the runner has no broker client at all — that is the point of the feature.

Why it exists: by 2026-08-06 there are four intraday skills (analyst v1/v2/v3, breakout) and no
way to compare them on live data. Every judgement so far has come from backtests on a payload the
live system does not even use. This records what each skill WOULD have said, on the same
point-in-time data, with no money at risk.

`rr_geometric` is stored alongside the skill's self-reported `risk_reward` because on 2026-08-04
those two diverged 3-15x on the v1 payload (APARINDS: claimed 1.34, its own levels implied 0.09).
A comparison that trusts the reported number would rank the skills by how boldly they round up.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

DEFAULT_DB = os.path.expanduser("~/.autointraday/autointraday.db")
IST = timezone(timedelta(hours=5, minutes=30))

UNIVERSE_MODES = ("screener", "watchlist", "book")
RUN_STATUSES = ("RUNNING", "SUCCESS", "FAILED", "SKIPPED")

DEFAULTS: dict[str, Any] = {
    "observe_enabled": 0,
    "skills": "",                 # csv of skill ids; empty = nothing to do
    "start_time": "09:45",
    "end_time": "15:00",
    "interval_min": 30,
    "universe_mode": "screener",
    "watchlist": "",              # csv of symbols, used when universe_mode = watchlist
    "max_symbols": 5,
    # A hard ceiling on skill calls per trading day. The observer shares the Claude usage window
    # with the LIVE trading cycle, so an over-wide config could starve the thing that actually
    # makes money. When the budget is spent the run is recorded as SKIPPED, never queued.
    "daily_call_budget": 200,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observe_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observe_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    skills TEXT,
    symbols TEXT,
    calls INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS observe_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT,
    trade_quality REAL,
    confidence REAL,
    entry REAL,
    stop_loss REAL,
    target1 REAL,
    risk_reward REAL,
    rr_geometric REAL,
    latency_ms INTEGER,
    error TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_observe_dec_date ON observe_decisions(trade_date);
CREATE INDEX IF NOT EXISTS ix_observe_dec_run ON observe_decisions(run_id);
CREATE INDEX IF NOT EXISTS ix_observe_runs_date ON observe_runs(trade_date);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def geometric_rr(entry, stop, target, action: str) -> Optional[float]:
    """Reward:risk the LEVELS actually express, as opposed to the number the skill reports.

    Returns None when a leg is missing or the geometry is degenerate (risk or reward <= 0), which
    is itself the finding worth recording — a decision whose target sits on the wrong side of its
    entry is broken, not merely thin.
    """
    if entry is None or stop is None or target is None:
        return None
    short = str(action or "").upper() in ("SHORT_NOW", "SELL_NOW", "SHORT_ON_BREAKDOWN")
    risk, reward = (stop - entry, entry - target) if short else (entry - stop, target - entry)
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


class ObserveStore:
    """Shadow-run storage. Holds no broker handle and exposes no order methods, by design."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- config --------------------------------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        rows = {r["key"]: r["value"] for r in self._conn.execute("SELECT * FROM observe_config")}
        out: dict[str, Any] = {}
        for k, default in DEFAULTS.items():
            raw = rows.get(k, default)
            if isinstance(default, int) and not isinstance(default, bool):
                try:
                    raw = int(raw)
                except (TypeError, ValueError):
                    raw = default
            out[k] = raw
        return out

    def set_config(self, **kwargs) -> None:
        unknown = set(kwargs) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"unknown observe config keys: {sorted(unknown)}")
        with self._conn:
            for k, v in kwargs.items():
                self._conn.execute(
                    "INSERT INTO observe_config(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))

    def skills(self) -> list[str]:
        raw = (self.get_config().get("skills") or "").strip()
        return [s.strip() for s in raw.split(",") if s.strip()]

    def watchlist(self) -> list[str]:
        raw = (self.get_config().get("watchlist") or "").strip()
        return [s.strip().upper() for s in raw.replace("\n", ",").split(",") if s.strip()]

    # ---- budget --------------------------------------------------------------------------
    def calls_today(self, trade_date: Optional[str] = None) -> int:
        d = trade_date or today_ist()
        r = self._conn.execute(
            "SELECT COALESCE(SUM(calls),0) AS n FROM observe_runs WHERE trade_date=?", (d,)
        ).fetchone()
        return int(r["n"] or 0)

    def budget_left(self, trade_date: Optional[str] = None) -> int:
        return max(0, int(self.get_config()["daily_call_budget"]) - self.calls_today(trade_date))

    # ---- runs ----------------------------------------------------------------------------
    def start_run(self, skills: list[str], symbols: list[str],
                  trade_date: Optional[str] = None) -> int:
        d = trade_date or today_ist()
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO observe_runs(started_at,status,trade_date,skills,symbols) "
                "VALUES(?,?,?,?,?)",
                (_now(), "RUNNING", d, ",".join(skills), ",".join(symbols)))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, calls: int = 0, errors: int = 0,
                   note: Optional[str] = None, error: Optional[str] = None) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(f"bad run status {status!r}")
        with self._conn:
            self._conn.execute(
                "UPDATE observe_runs SET finished_at=?,status=?,calls=?,errors=?,note=?,error=? "
                "WHERE id=?", (_now(), status, calls, errors, note, error, run_id))

    def record_skipped(self, reason: str, trade_date: Optional[str] = None) -> int:
        """A run that never called anything — budget spent, disabled, or nothing to do. Recorded
        rather than silent, so a quiet day is distinguishable from a broken scheduler."""
        rid = self.start_run([], [], trade_date)
        self.finish_run(rid, "SKIPPED", note=reason)
        return rid

    def runs(self, trade_date: Optional[str] = None, limit: int = 100) -> list[dict]:
        if trade_date:
            q = ("SELECT * FROM observe_runs WHERE trade_date=? ORDER BY id DESC LIMIT ?",
                 (trade_date, limit))
        else:
            q = ("SELECT * FROM observe_runs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in self._conn.execute(*q)]

    # ---- decisions -----------------------------------------------------------------------
    def record(self, run_id: int, skill_id: str, symbol: str, decision=None,
               error: Optional[str] = None, latency_ms: Optional[int] = None,
               trade_date: Optional[str] = None) -> int:
        d = trade_date or today_ist()
        action = getattr(decision, "action", None)
        entry = getattr(decision, "entry", None)
        stop = getattr(decision, "stop_loss", None)
        t1 = getattr(decision, "target1", None)
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO observe_decisions(run_id,trade_date,created_at,skill_id,symbol,"
                "action,trade_quality,confidence,entry,stop_loss,target1,risk_reward,"
                "rr_geometric,latency_ms,error,raw_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, d, _now(), skill_id, symbol, action,
                 getattr(decision, "trade_quality", None), getattr(decision, "confidence", None),
                 entry, stop, t1, getattr(decision, "risk_reward", None),
                 geometric_rr(entry, stop, t1, action or ""), latency_ms, error,
                 getattr(decision, "raw_response", None)))
        return int(cur.lastrowid)

    def decisions(self, trade_date: Optional[str] = None, skill_id: Optional[str] = None,
                  limit: int = 2000) -> list[dict]:
        where, args = [], []
        if trade_date:
            where.append("trade_date=?"); args.append(trade_date)
        if skill_id:
            where.append("skill_id=?"); args.append(skill_id)
        sql = "SELECT * FROM observe_decisions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self._conn.execute(sql, args)]

    def dates(self, limit: int = 60) -> list[str]:
        return [r["trade_date"] for r in self._conn.execute(
            "SELECT DISTINCT trade_date FROM observe_decisions ORDER BY trade_date DESC LIMIT ?",
            (limit,))]

    def close(self) -> None:
        self._conn.close()
