"""Live Intraday persistence — config, session state and trades.

Its own tables in the same SQLite file rather than columns bolted onto the 65k-line store.py:
this path is a separate strategy with a separate lifecycle, and keeping it isolated means the
LLM engines' ledger cannot be disturbed by it.

The tables are also the ONLY channel between the dashboard page and the trader daemon. The page
writes control flags; the loop re-reads them every iteration. So DISARM works even with the
dashboard closed, because it is persisted state rather than an in-memory signal.

See docs/superpowers/specs/2026-07-31-live-intraday-design.md.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_DB = os.path.expanduser("~/.autointraday/autointraday.db")

DEFAULTS: dict[str, Any] = {
    "armed": 0,
    "mode": "paper",
    "capital_per_trade": 30000.0,
    "poll_seconds": 2,
    "candle_minutes": 1,
    "min_rr": 1.5,
    "atr_mult": 1.5,
    "rr_target": 2.0,
    "min_stop_pct": 0.35,
    "rvol_floor": 1.5,
    "vwap_exit_candles": 2,
    "max_hold_minutes": 45,
    "daily_loss_cap": 5000.0,
    "abandon_after_minutes": 60,
    "max_spread_pct": 0.5,
    "select_at": "09:35",
    "no_new_entry_after": "14:30",
    "squareoff_at": "15:15",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS live_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    trading_date TEXT,
    symbol TEXT,
    selected_reason TEXT,
    signal_action TEXT,
    signal_reason TEXT,
    indicators_json TEXT,
    position_json TEXT,
    disarmed_reason TEXT,
    heartbeat_at TEXT
);
CREATE TABLE IF NOT EXISTS live_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trading_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    stop REAL,
    target REAL,
    exit_price REAL,
    exit_reason TEXT,
    pnl REAL,
    mode TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    entry_order_id TEXT,
    exit_order_id TEXT
);
"""


@dataclass
class LiveTrade:
    id: int
    trading_date: str
    symbol: str
    quantity: int
    entry_price: float
    stop: Optional[float]
    target: Optional[float]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    pnl: Optional[float]
    mode: str
    opened_at: str
    closed_at: Optional[str]
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveStore:
    def __init__(self, db_path: str = DEFAULT_DB):
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO live_state (id) VALUES (1)")
        for k, v in DEFAULTS.items():
            self.conn.execute("INSERT OR IGNORE INTO live_config (key, value) VALUES (?, ?)",
                              (k, str(v)))
        self.conn.commit()

    # ---- config -----------------------------------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        """Config with DEFAULTS' types restored — sqlite stores everything as TEXT."""
        rows = {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM live_config")}
        out: dict[str, Any] = {}
        for k, default in DEFAULTS.items():
            raw = rows.get(k, default)
            try:
                out[k] = type(default)(raw) if not isinstance(default, str) else str(raw)
            except (TypeError, ValueError):
                out[k] = default
        return out

    def set_config(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if k not in DEFAULTS:
                raise KeyError(f"unknown live config key: {k}")
            self.conn.execute("INSERT INTO live_config (key, value) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, str(v)))
        self.conn.commit()

    def is_armed(self) -> bool:
        return bool(self.get_config()["armed"])

    def disarm(self, reason: str) -> None:
        """Stop new entries and record why. Used by the kill switch and the loss cap alike."""
        self.set_config(armed=0)
        self.conn.execute("UPDATE live_state SET disarmed_reason = ? WHERE id = 1", (reason,))
        self.conn.commit()

    # ---- session state ----------------------------------------------------------------------
    def get_state(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM live_state WHERE id = 1").fetchone()
        return dict(row) if row else {}

    def update_state(self, **kwargs) -> None:
        allowed = {"trading_date", "symbol", "selected_reason", "signal_action", "signal_reason",
                   "indicators_json", "position_json", "disarmed_reason", "heartbeat_at"}
        bad = set(kwargs) - allowed
        if bad:
            raise KeyError(f"unknown live state field(s): {sorted(bad)}")
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        self.conn.execute(f"UPDATE live_state SET {sets} WHERE id = 1", tuple(kwargs.values()))
        self.conn.commit()

    def heartbeat(self) -> None:
        self.update_state(heartbeat_at=_now())

    # ---- trades -----------------------------------------------------------------------------
    def open_trade(self, trading_date: str, symbol: str, quantity: int, entry_price: float,
                   stop: Optional[float], target: Optional[float], mode: str,
                   entry_order_id: Optional[str] = None) -> int:
        """Open a trade. Refuses if one is already open — the single-position invariant lives
        here, in the store, so it holds regardless of which caller forgets to check."""
        if self.get_open_trade() is not None:
            raise ValueError("a live trade is already open — refusing to open a second")
        cur = self.conn.execute(
            "INSERT INTO live_trades (trading_date, symbol, quantity, entry_price, stop, target,"
            " mode, opened_at, entry_order_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (trading_date, symbol, quantity, entry_price, stop, target, mode, _now(),
             entry_order_id))
        self.conn.commit()
        return int(cur.lastrowid)

    def close_trade(self, trade_id: int, exit_price: float, reason: str,
                    exit_order_id: Optional[str] = None) -> float:
        row = self.conn.execute("SELECT * FROM live_trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"no live trade {trade_id}")
        pnl = (exit_price - row["entry_price"]) * row["quantity"]     # long-only
        self.conn.execute(
            "UPDATE live_trades SET exit_price = ?, exit_reason = ?, pnl = ?, closed_at = ?,"
            " exit_order_id = ? WHERE id = ?",
            (exit_price, reason, pnl, _now(), exit_order_id, trade_id))
        self.conn.commit()
        return pnl

    def get_open_trade(self) -> Optional[LiveTrade]:
        row = self.conn.execute(
            "SELECT * FROM live_trades WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        return LiveTrade(**dict(row)) if row else None

    def trades_for(self, trading_date: str) -> list[LiveTrade]:
        return [LiveTrade(**dict(r)) for r in self.conn.execute(
            "SELECT * FROM live_trades WHERE trading_date = ? ORDER BY id", (trading_date,))]

    def realized_pnl(self, trading_date: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS p FROM live_trades WHERE trading_date = ?"
            " AND closed_at IS NOT NULL", (trading_date,)).fetchone()
        return float(row["p"])

    def close(self) -> None:
        self.conn.close()
