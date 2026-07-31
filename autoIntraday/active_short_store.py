"""activeShort persistence — nightly picks, morning orders, and the paper gate.

Its own tables in the shared SQLite file, like live_store: a separate strategy with a separate
lifecycle that must not disturb the intraday ledgers.

The paper gate lives here rather than in the caller: `live_allowed()` is the single authority on
whether real money may be committed, so no job can forget to ask.

See docs/superpowers/specs/2026-07-31-active-short-design.md.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_DB = os.path.expanduser("~/.autointraday/autointraday.db")

DEFAULTS: dict[str, Any] = {
    "active_short_enabled": 0,
    "active_short_mode": "paper",
    "max_shorts": 4,
    "capital_per_short": 25000.0,
    "min_confidence": 70.0,
    "min_rvol": 1.5,
    "stop_pct": 1.5,
    "target_pct": 2.5,
    "max_gap_pct": 3.0,
    "scan_at": "16:00",
    "arm_at": "09:15",
    "arm_expiry": "11:00",
    "squareoff_at": "15:15",
    "paper_sessions_required": 10,
}

# A pick's lifecycle. PLANNED -> ARMED -> FILLED -> PROTECTED -> CLOSED, or ARMED -> EXPIRED
# (never triggered), or PLANNED -> SKIPPED (gapped too far / gate refused it at arm time).
STATUSES = ("PLANNED", "ARMED", "FILLED", "PROTECTED", "CLOSED", "EXPIRED", "SKIPPED")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS active_short_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS active_short_picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date TEXT NOT NULL,          -- the evening the scan ran
    trade_date TEXT NOT NULL,         -- the session the pick is for
    symbol TEXT NOT NULL,
    confidence REAL,
    confirmation_level REAL,          -- short is valid only BELOW this
    stop REAL,
    target REAL,
    rvol REAL,
    reason TEXT,
    rank INTEGER,
    status TEXT NOT NULL DEFAULT 'PLANNED',
    status_note TEXT,
    quantity INTEGER,
    entry_order_id TEXT,
    stop_order_id TEXT,
    target_order_id TEXT,
    fill_price REAL,
    exit_price REAL,
    pnl REAL,
    mode TEXT NOT NULL DEFAULT 'paper',
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS active_short_sessions (
    trade_date TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    picks INTEGER NOT NULL DEFAULT 0,
    triggered INTEGER NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    completed_at TEXT
);
"""


@dataclass
class Pick:
    id: int
    scan_date: str
    trade_date: str
    symbol: str
    confidence: Optional[float]
    confirmation_level: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    rvol: Optional[float]
    reason: Optional[str]
    rank: Optional[int]
    status: str
    status_note: Optional[str]
    quantity: Optional[int]
    entry_order_id: Optional[str]
    stop_order_id: Optional[str]
    target_order_id: Optional[str]
    fill_price: Optional[float]
    exit_price: Optional[float]
    pnl: Optional[float]
    mode: str
    created_at: str
    updated_at: Optional[str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActiveShortStore:
    def __init__(self, db_path: str = DEFAULT_DB):
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        for k, v in DEFAULTS.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO active_short_config (key, value) VALUES (?, ?)", (k, str(v)))
        self.conn.commit()

    # ---- config ------------------------------------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        rows = {r["key"]: r["value"]
                for r in self.conn.execute("SELECT key, value FROM active_short_config")}
        out: dict[str, Any] = {}
        for k, default in DEFAULTS.items():
            raw = rows.get(k, default)
            try:
                out[k] = str(raw) if isinstance(default, str) else type(default)(raw)
            except (TypeError, ValueError):
                out[k] = default
        return out

    def set_config(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if k not in DEFAULTS:
                raise KeyError(f"unknown activeShort config key: {k}")
            self.conn.execute(
                "INSERT INTO active_short_config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, str(v)))
        self.conn.commit()

    # ---- the paper gate ----------------------------------------------------------------------
    def completed_paper_sessions(self) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS n FROM active_short_sessions "
            "WHERE mode = 'paper' AND completed_at IS NOT NULL").fetchone()
        return int(r["n"])

    def live_allowed(self) -> tuple[bool, str]:
        """Whether real money may be committed, and why not if not.

        The single authority on this — jobs ask here rather than each re-deriving the rule, so no
        code path can commit capital by forgetting to check. Next-session direction prediction is
        close to a coin flip; the paper period exists to measure whether this signal has an edge
        before it is funded.
        """
        cfg = self.get_config()
        if cfg["active_short_mode"] != "live":
            return False, "mode is paper"
        need = int(cfg["paper_sessions_required"])
        have = self.completed_paper_sessions()
        if have < need:
            return False, (f"paper gate: {have}/{need} paper sessions recorded — "
                           f"live refused until the signal has a measurable hit rate")
        return True, "live allowed"

    # ---- picks -------------------------------------------------------------------------------
    def add_pick(self, scan_date: str, trade_date: str, symbol: str, confidence: float,
                 confirmation_level: float, stop: float, target: float,
                 rvol: Optional[float] = None, reason: Optional[str] = None,
                 rank: Optional[int] = None, mode: str = "paper") -> int:
        cur = self.conn.execute(
            "INSERT INTO active_short_picks (scan_date, trade_date, symbol, confidence,"
            " confirmation_level, stop, target, rvol, reason, rank, mode, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (scan_date, trade_date, symbol.upper(), confidence, confirmation_level, stop, target,
             rvol, reason, rank, mode, _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def picks_for(self, trade_date: str, status: Optional[str] = None) -> list[Pick]:
        sql = "SELECT * FROM active_short_picks WHERE trade_date = ?"
        args: list[Any] = [trade_date]
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY COALESCE(rank, 999), id"
        return [Pick(**dict(r)) for r in self.conn.execute(sql, args)]

    def update_pick(self, pick_id: int, **fields) -> None:
        allowed = {"status", "status_note", "quantity", "entry_order_id", "stop_order_id",
                   "target_order_id", "fill_price", "exit_price", "pnl"}
        bad = set(fields) - allowed
        if bad:
            raise KeyError(f"unknown pick field(s): {sorted(bad)}")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"invalid status {fields['status']!r}; expected one of {STATUSES}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE active_short_picks SET {sets}, updated_at = ? WHERE id = ?",
                          (*fields.values(), _now(), pick_id))
        self.conn.commit()

    def get_pick(self, pick_id: int) -> Optional[Pick]:
        r = self.conn.execute("SELECT * FROM active_short_picks WHERE id = ?", (pick_id,)).fetchone()
        return Pick(**dict(r)) if r else None

    # ---- sessions ----------------------------------------------------------------------------
    def record_session(self, trade_date: str, mode: str, picks: int) -> None:
        self.conn.execute(
            "INSERT INTO active_short_sessions (trade_date, mode, picks) VALUES (?,?,?) "
            "ON CONFLICT(trade_date) DO UPDATE SET mode = excluded.mode, picks = excluded.picks",
            (trade_date, mode, picks))
        self.conn.commit()

    def complete_session(self, trade_date: str) -> None:
        """Close the books on a session — this is what counts toward the paper gate."""
        rows = self.picks_for(trade_date)
        triggered = sum(1 for p in rows if p.status in ("FILLED", "PROTECTED", "CLOSED"))
        pnl = sum(p.pnl or 0.0 for p in rows)
        self.conn.execute(
            "UPDATE active_short_sessions SET triggered = ?, realized_pnl = ?, completed_at = ? "
            "WHERE trade_date = ?", (triggered, pnl, _now(), trade_date))
        self.conn.commit()

    def sessions(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM active_short_sessions ORDER BY trade_date DESC LIMIT ?", (limit,))]

    def close(self) -> None:
        self.conn.close()
