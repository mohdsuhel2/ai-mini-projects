"""SQLite state store for autoIntraday — the only module that touches the database.

Persists config, job runs, decisions, positions, and orders. Later phases (orchestrator,
UI) call typed Store methods and never write SQL directly. See
docs/superpowers/specs/2026-07-09-data-store-design.md.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1


class StoreError(Exception):
    """Wraps every error the store raises: constraint violations, unknown ids, bad state."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Config:
    mode: str
    total_pool: float
    max_open_positions: int
    capital_per_position: float
    is_paused: bool
    updated_at: str
    primer_enabled: bool = False


_CONFIG_FIELDS = ("mode", "total_pool", "max_open_positions",
                  "capital_per_position", "is_paused", "primer_enabled")


@dataclass
class JobRun:
    id: int
    started_at: str
    finished_at: str | None
    status: str
    mode: str
    num_candidates: int | None
    num_actions: int | None
    error: str | None
    summary: str | None


@dataclass
class Position:
    id: int
    symbol: str
    exchange: str
    side: str
    quantity: int
    entry_price: float
    target_price: float | None
    stop_loss: float | None
    status: str
    entry_order_id: str | None
    oco_order_id: str | None
    exit_price: float | None
    exit_reason: str | None
    realized_pnl: float | None
    mode: str
    opened_at: str
    closed_at: str | None
    # For resting (PENDING) entries: how the level triggers. 'LIMIT' = fills when price comes
    # DOWN/BACK to the level (pullback); 'STOP' = fills when price breaks THROUGH the level
    # (breakout). None for immediate entries.
    trigger_kind: str | None = None
    # The engine's trade_quality at entry — used to scale the partial-profit-book trigger (a
    # higher-quality trade is let to run a little further before booking).
    entry_quality: float | None = None
    # Partial profit-book bookkeeping: booked_pnl accumulates realized P&L from partial exits
    # while the position is still OPEN; partial_booked is set once so we book at most one slice.
    booked_pnl: float = 0.0
    partial_booked: bool = False
    # Consecutive cycles the exit engine has returned a conviction-clearing reverse signal. A
    # SIGNAL exit fires only once this reaches EXIT_CONFIRM_CYCLES (see orchestrator).
    reverse_signal_count: int = 0


@dataclass
class Decision:
    id: int
    run_id: int
    symbol: str
    action: str
    score: float | None
    reason: str | None
    entry_price: float | None
    target_price: float | None
    stop_loss: float | None
    position_id: int | None
    created_at: str
    raw_json: str | None


@dataclass
class Order:
    id: int
    broker_order_id: str
    position_id: int | None
    symbol: str
    transaction_type: str
    quantity: int
    order_type: str
    price: float | None
    status: str
    mode: str
    placed_at: str
    raw_json: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL,
    total_pool REAL NOT NULL,
    max_open_positions INTEGER NOT NULL,
    capital_per_position REAL NOT NULL,
    is_paused INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    num_candidates INTEGER,
    num_actions INTEGER,
    error TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    target_price REAL,
    stop_loss REAL,
    status TEXT NOT NULL,
    entry_order_id TEXT,
    oco_order_id TEXT,
    exit_price REAL,
    exit_reason TEXT,
    realized_pnl REAL,
    mode TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    trigger_kind TEXT,
    entry_quality REAL,
    booked_pnl REAL NOT NULL DEFAULT 0,
    partial_booked INTEGER NOT NULL DEFAULT 0,
    reverse_signal_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES job_runs(id),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    score REAL,
    reason TEXT,
    entry_price REAL,
    target_price REAL,
    stop_loss REAL,
    position_id INTEGER REFERENCES positions(id),
    created_at TEXT NOT NULL,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_order_id TEXT NOT NULL,
    position_id INTEGER REFERENCES positions(id),
    symbol TEXT NOT NULL,
    transaction_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    price REAL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    placed_at TEXT NOT NULL,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS holdings (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER,
    avg_price REAL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    num_holdings INTEGER,
    error TEXT,
    pid INTEGER
);
CREATE TABLE IF NOT EXISTS swing_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES swing_runs(id),
    symbol TEXT NOT NULL,
    quantity INTEGER,
    avg_price REAL,
    status TEXT DEFAULT 'DONE',
    analyzed_at TEXT,
    swing_action TEXT, swing_conviction INTEGER, swing_target REAL, swing_stop REAL,
    swing_rationale TEXT,
    ss_action TEXT, ss_conviction INTEGER, ss_target REAL, ss_stop REAL, ss_rationale TEXT
);
"""


class Store:
    def __init__(self, db_path: str):
        # check_same_thread=False so the connection survives being used across threads — the
        # Streamlit dashboard runs reruns/callbacks on different threads than the one that
        # opened the connection. Access is still effectively serialized (the scheduler is a
        # single-threaded process; the dashboard serializes script runs per session), so this
        # is safe. Without it, a config write from the UI raises a cross-thread ProgrammingError
        # and takes the Streamlit server down.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL + busy_timeout so a reader (dashboard) and a writer (scheduler) — or two
        # dashboard connections — don't collide with "database is locked". Only meaningful for
        # a file DB; harmless for :memory:.
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
        except sqlite3.DatabaseError:
            pass
        self._init_schema()
        self._seed_config()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def _migrate(self) -> None:
        """Additive migrations for DBs created before a column existed (CREATE TABLE IF NOT
        EXISTS does not add columns to existing tables)."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(positions)")}
        if "trigger_kind" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN trigger_kind TEXT")
        if "entry_quality" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN entry_quality REAL")
        if "booked_pnl" not in cols:
            self._conn.execute(
                "ALTER TABLE positions ADD COLUMN booked_pnl REAL NOT NULL DEFAULT 0")
        if "partial_booked" not in cols:
            self._conn.execute(
                "ALTER TABLE positions ADD COLUMN partial_booked INTEGER NOT NULL DEFAULT 0")
        if "reverse_signal_count" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN reverse_signal_count "
                               "INTEGER NOT NULL DEFAULT 0")
        ccols = {r["name"] for r in self._conn.execute("PRAGMA table_info(config)")}
        if "primer_enabled" not in ccols:
            self._conn.execute(
                "ALTER TABLE config ADD COLUMN primer_enabled INTEGER NOT NULL DEFAULT 0")
        vcols = {r["name"] for r in self._conn.execute("PRAGMA table_info(swing_verdicts)")}
        if vcols and "status" not in vcols:
            self._conn.execute(
                "ALTER TABLE swing_verdicts ADD COLUMN status TEXT DEFAULT 'DONE'")
        if vcols and "analyzed_at" not in vcols:
            self._conn.execute("ALTER TABLE swing_verdicts ADD COLUMN analyzed_at TEXT")
        rcols = {r["name"] for r in self._conn.execute("PRAGMA table_info(swing_runs)")}
        if rcols and "pid" not in rcols:
            self._conn.execute("ALTER TABLE swing_runs ADD COLUMN pid INTEGER")

    def _seed_config(self) -> None:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM config").fetchone()
        if row["n"] == 0:
            self._conn.execute(
                "INSERT INTO config (id, mode, total_pool, max_open_positions, "
                "capital_per_position, is_paused, updated_at) "
                "VALUES (1, 'paper', 0, 0, 0, 0, ?)", (_utc_now(),))
            self._conn.commit()

    def get_config(self) -> Config:
        r = self._conn.execute("SELECT * FROM config WHERE id = 1").fetchone()
        return Config(mode=r["mode"], total_pool=r["total_pool"],
                      max_open_positions=r["max_open_positions"],
                      capital_per_position=r["capital_per_position"],
                      is_paused=bool(r["is_paused"]), updated_at=r["updated_at"],
                      primer_enabled=bool(r["primer_enabled"]))

    def update_config(self, **fields) -> Config:
        for key in fields:
            if key not in _CONFIG_FIELDS:
                raise StoreError(f"unknown config field: {key}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
            values.append(_utc_now())
            self._conn.execute(f"UPDATE config SET {sets}, updated_at = ? WHERE id = 1", values)
            self._conn.commit()
        return self.get_config()

    def start_run(self, mode: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO job_runs (started_at, status, mode) VALUES (?, 'RUNNING', ?)",
            (_utc_now(), mode))
        self._conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, num_candidates: int | None = None,
                   num_actions: int | None = None, error: str | None = None,
                   summary: str | None = None) -> None:
        cur = self._conn.execute(
            "UPDATE job_runs SET finished_at = ?, status = ?, num_candidates = ?, "
            "num_actions = ?, error = ?, summary = ? WHERE id = ?",
            (_utc_now(), status, num_candidates, num_actions, error, summary, run_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown run id: {run_id}")

    def get_run(self, run_id: int) -> JobRun:
        r = self._conn.execute("SELECT * FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            raise StoreError(f"unknown run id: {run_id}")
        return JobRun(id=r["id"], started_at=r["started_at"], finished_at=r["finished_at"],
                      status=r["status"], mode=r["mode"],
                      num_candidates=r["num_candidates"], num_actions=r["num_actions"],
                      error=r["error"], summary=r["summary"])

    @staticmethod
    def _row_to_position(r) -> "Position":
        return Position(
            id=r["id"], symbol=r["symbol"], exchange=r["exchange"], side=r["side"],
            quantity=r["quantity"], entry_price=r["entry_price"],
            target_price=r["target_price"], stop_loss=r["stop_loss"], status=r["status"],
            entry_order_id=r["entry_order_id"], oco_order_id=r["oco_order_id"],
            exit_price=r["exit_price"], exit_reason=r["exit_reason"],
            realized_pnl=r["realized_pnl"], mode=r["mode"], opened_at=r["opened_at"],
            closed_at=r["closed_at"], trigger_kind=r["trigger_kind"],
            entry_quality=r["entry_quality"], booked_pnl=r["booked_pnl"] or 0.0,
            partial_booked=bool(r["partial_booked"]),
            reverse_signal_count=r["reverse_signal_count"] or 0)

    def open_position(self, symbol: str, exchange: str, side: str, quantity: int,
                      entry_price: float, target_price: float | None = None,
                      stop_loss: float | None = None, entry_order_id: str | None = None,
                      oco_order_id: str | None = None, mode: str = "paper",
                      status: str = "OPEN", trigger_kind: str | None = None,
                      entry_quality: float | None = None) -> int:
        """Create a position. status='OPEN' fills immediately (market entry); status='PENDING'
        is a resting order that occupies a slot + capital but is not yet in the market — a later
        cycle activates it (fill) or cancels it (see activate_position/cancel_position)."""
        cur = self._conn.execute(
            "INSERT INTO positions (symbol, exchange, side, quantity, entry_price, "
            "target_price, stop_loss, status, entry_order_id, oco_order_id, mode, opened_at, "
            "trigger_kind, entry_quality) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, exchange, side, quantity, entry_price, target_price, stop_loss,
             status, entry_order_id, oco_order_id, mode, _utc_now(), trigger_kind,
             entry_quality))
        self._conn.commit()
        return cur.lastrowid

    def book_partial(self, position_id: int, sell_quantity: int, slice_pnl: float,
                     new_stop: float | None = None) -> None:
        """Book a PARTIAL profit exit on an OPEN position: shrink the quantity by sell_quantity,
        accumulate slice_pnl into booked_pnl, flag partial_booked (so we book at most one slice),
        and optionally move the stop (to breakeven on the runner). The remaining quantity keeps
        running; its final close adds booked_pnl to the total realized P&L (see close_position)."""
        r = self._conn.execute(
            "SELECT quantity FROM positions WHERE id = ? AND status = 'OPEN'",
            (position_id,)).fetchone()
        if r is None:
            raise StoreError(f"unknown open position id (or not open): {position_id}")
        new_qty = int(r["quantity"]) - int(sell_quantity)
        if new_qty < 1:
            raise StoreError(f"partial book would leave < 1 share: {position_id}")
        if new_stop is None:
            self._conn.execute(
                "UPDATE positions SET quantity = ?, booked_pnl = booked_pnl + ?, "
                "partial_booked = 1 WHERE id = ?", (new_qty, slice_pnl, position_id))
        else:
            self._conn.execute(
                "UPDATE positions SET quantity = ?, booked_pnl = booked_pnl + ?, "
                "partial_booked = 1, stop_loss = ? WHERE id = ?",
                (new_qty, slice_pnl, new_stop, position_id))
        self._conn.commit()

    def activate_position(self, position_id: int, entry_price: float,
                          oco_order_id: str | None = None) -> None:
        """Fill a PENDING position: flip it to OPEN at the actual fill price, attach its OCO."""
        cur = self._conn.execute(
            "UPDATE positions SET status = 'OPEN', entry_price = ?, oco_order_id = ?, "
            "opened_at = ? WHERE id = ? AND status = 'PENDING'",
            (entry_price, oco_order_id, _utc_now(), position_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown pending position id (or not pending): {position_id}")

    def cancel_position(self, position_id: int, reason: str) -> None:
        """Cancel a PENDING position that never filled (e.g. price never reached the level by
        square-off). Frees its reserved slot + capital."""
        cur = self._conn.execute(
            "UPDATE positions SET status = 'CANCELLED', exit_reason = ?, closed_at = ? "
            "WHERE id = ? AND status = 'PENDING'",
            (reason, _utc_now(), position_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown pending position id (or not pending): {position_id}")

    def update_position_levels(self, position_id: int, stop_loss: float | None,
                               target_price: float | None) -> None:
        """Adjust the stop/target of an OPEN position (trailing). Caller enforces the ratchet
        rule; this just persists the new levels the exit engine reads next cycle."""
        cur = self._conn.execute(
            "UPDATE positions SET stop_loss = ?, target_price = ? "
            "WHERE id = ? AND status = 'OPEN'",
            (stop_loss, target_price, position_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown open position id (or not open): {position_id}")

    def set_reverse_signal_count(self, position_id: int, count: int) -> None:
        """Track consecutive conviction-clearing reverse (exit) signals on an OPEN position, so a
        SIGNAL exit needs EXIT_CONFIRM_CYCLES in a row rather than firing on one noisy read."""
        self._conn.execute(
            "UPDATE positions SET reverse_signal_count = ? WHERE id = ? AND status = 'OPEN'",
            (count, position_id))
        self._conn.commit()

    def update_pending_order(self, position_id: int, entry_price: float,
                             stop_loss: float | None, target_price: float | None,
                             quantity: int, entry_order_id: str | None) -> None:
        """Refresh a still-resting PENDING order's rest level / stop / target / quantity and
        (live) its replaced broker order id. Only touches PENDING rows — an order that already
        filled or was cancelled between the read and here is left alone."""
        cur = self._conn.execute(
            "UPDATE positions SET entry_price = ?, stop_loss = ?, target_price = ?, "
            "quantity = ?, entry_order_id = ? WHERE id = ? AND status = 'PENDING'",
            (entry_price, stop_loss, target_price, quantity, entry_order_id, position_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown pending position id (or not pending): {position_id}")

    def add_to_position(self, position_id: int, add_quantity: int, add_price: float) -> float:
        """Scale into an OPEN position: blend the add into a weighted-average entry and grow the
        quantity. Stop/target are deliberately LEFT UNCHANGED — a scale-in must never widen the
        stop (that is the averaging-down trap). Returns the new average entry price."""
        r = self._conn.execute(
            "SELECT quantity, entry_price FROM positions WHERE id = ? AND status = 'OPEN'",
            (position_id,)).fetchone()
        if r is None:
            raise StoreError(f"unknown open position id (or not open): {position_id}")
        old_qty, old_entry = int(r["quantity"]), float(r["entry_price"])
        new_qty = old_qty + int(add_quantity)
        new_avg = (old_qty * old_entry + int(add_quantity) * float(add_price)) / new_qty
        self._conn.execute("UPDATE positions SET quantity = ?, entry_price = ? WHERE id = ?",
                           (new_qty, new_avg, position_id))
        self._conn.commit()
        return new_avg

    def update_position_quantity(self, position_id: int, quantity: int) -> None:
        """Sync a position's quantity to broker reality (manual partial exit detected by
        reconcile). The manually-sold slice's P&L is NOT booked — its fill price is unknown."""
        self._conn.execute("UPDATE positions SET quantity = ? WHERE id = ?",
                           (quantity, position_id))
        self._conn.commit()

    def close_position(self, position_id: int, exit_price: float, exit_reason: str,
                       realized_pnl: float) -> None:
        """Close an OPEN position. `realized_pnl` is the P&L of the FINAL slice (the remaining
        quantity); any profit already banked by a partial book (booked_pnl) is added on, so
        realized_pnl on the row is always the position's full lifetime P&L."""
        cur = self._conn.execute(
            "UPDATE positions SET status = 'CLOSED', exit_price = ?, exit_reason = ?, "
            "realized_pnl = ? + COALESCE(booked_pnl, 0), closed_at = ? WHERE id = ? "
            "AND status = 'OPEN'",
            (exit_price, exit_reason, realized_pnl, _utc_now(), position_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown position id (or already closed): {position_id}")

    def get_position(self, position_id: int) -> Position:
        r = self._conn.execute("SELECT * FROM positions WHERE id = ?",
                               (position_id,)).fetchone()
        if r is None:
            raise StoreError(f"unknown position id: {position_id}")
        return self._row_to_position(r)

    def get_open_positions(self) -> list["Position"]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY id").fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_pending_positions(self) -> list["Position"]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'PENDING' ORDER BY id").fetchall()
        return [self._row_to_position(r) for r in rows]

    def count_open_positions(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status = 'OPEN'").fetchone()["n"]

    def deployed_capital(self) -> float:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(quantity * entry_price), 0) AS c "
            "FROM positions WHERE status = 'OPEN'").fetchone()
        return float(r["c"])

    def count_committed_positions(self) -> int:
        """OPEN + PENDING — every slot currently spoken for (a resting order reserves a slot)."""
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status IN ('OPEN', 'PENDING')"
        ).fetchone()["n"]

    def committed_capital(self) -> float:
        """Capital tied up in OPEN + PENDING positions — reserved so resting orders can't
        over-commit the pool."""
        r = self._conn.execute(
            "SELECT COALESCE(SUM(quantity * entry_price), 0) AS c "
            "FROM positions WHERE status IN ('OPEN', 'PENDING')").fetchone()
        return float(r["c"])

    @staticmethod
    def _row_to_decision(r) -> "Decision":
        return Decision(
            id=r["id"], run_id=r["run_id"], symbol=r["symbol"], action=r["action"],
            score=r["score"], reason=r["reason"], entry_price=r["entry_price"],
            target_price=r["target_price"], stop_loss=r["stop_loss"],
            position_id=r["position_id"], created_at=r["created_at"], raw_json=r["raw_json"])

    def record_decision(self, run_id: int, symbol: str, action: str,
                        score: float | None = None, reason: str | None = None,
                        entry_price: float | None = None, target_price: float | None = None,
                        stop_loss: float | None = None, position_id: int | None = None,
                        raw_json: str | None = None) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO decisions (run_id, symbol, action, score, reason, entry_price, "
                "target_price, stop_loss, position_id, created_at, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, symbol, action, score, reason, entry_price, target_price,
                 stop_loss, position_id, _utc_now(), raw_json))
            self._conn.commit()
        except sqlite3.IntegrityError as e:
            raise StoreError(f"foreign key / integrity error recording decision: {e}") from e
        return cur.lastrowid

    def get_decisions_for_run(self, run_id: int) -> list["Decision"]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [self._row_to_decision(r) for r in rows]

    @staticmethod
    def _row_to_order(r) -> "Order":
        return Order(
            id=r["id"], broker_order_id=r["broker_order_id"], position_id=r["position_id"],
            symbol=r["symbol"], transaction_type=r["transaction_type"],
            quantity=r["quantity"], order_type=r["order_type"], price=r["price"],
            status=r["status"], mode=r["mode"], placed_at=r["placed_at"],
            raw_json=r["raw_json"])

    def record_order(self, broker_order_id: str, symbol: str, transaction_type: str,
                     quantity: int, order_type: str, price: float | None = None,
                     status: str = "PENDING", mode: str = "paper",
                     position_id: int | None = None, raw_json: str | None = None) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO orders (broker_order_id, position_id, symbol, transaction_type, "
                "quantity, order_type, price, status, mode, placed_at, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (broker_order_id, position_id, symbol, transaction_type, quantity,
                 order_type, price, status, mode, _utc_now(), raw_json))
            self._conn.commit()
        except sqlite3.IntegrityError as e:
            raise StoreError(f"foreign key / integrity error recording order: {e}") from e
        return cur.lastrowid

    def update_order_status(self, broker_order_id: str, status: str) -> None:
        cur = self._conn.execute(
            "UPDATE orders SET status = ? WHERE broker_order_id = ?",
            (status, broker_order_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown order broker_order_id: {broker_order_id}")

    def get_order(self, order_id: int) -> Order:
        r = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if r is None:
            raise StoreError(f"unknown order id: {order_id}")
        return self._row_to_order(r)

    def get_orders_for_position(self, position_id: int) -> list["Order"]:
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE position_id = ? ORDER BY id", (position_id,)).fetchall()
        return [self._row_to_order(r) for r in rows]

    @staticmethod
    def _row_to_run(r) -> "JobRun":
        return JobRun(id=r["id"], started_at=r["started_at"], finished_at=r["finished_at"],
                      status=r["status"], mode=r["mode"], num_candidates=r["num_candidates"],
                      num_actions=r["num_actions"], error=r["error"], summary=r["summary"])

    def get_recent_runs(self, limit: int = 20) -> list["JobRun"]:
        rows = self._conn.execute(
            "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_recent_decisions(self, limit: int = 50) -> list["Decision"]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def get_recent_positions(self, limit: int = 50) -> list["Position"]:
        rows = self._conn.execute(
            "SELECT * FROM positions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_position(r) for r in rows]

    def realized_pnl_total(self) -> float:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM positions "
            "WHERE status = 'CLOSED'").fetchone()
        return float(r["p"])

    def realized_pnl_since(self, iso_date: str) -> float:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM positions "
            "WHERE status = 'CLOSED' AND closed_at >= ?", (iso_date,)).fetchone()
        return float(r["p"])

    def get_runs_between(self, start_iso: str, end_iso: str, limit: int = 100) -> list["JobRun"]:
        rows = self._conn.execute(
            "SELECT * FROM job_runs WHERE started_at >= ? AND started_at < ? "
            "ORDER BY id DESC LIMIT ?", (start_iso, end_iso, limit)).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_decisions_between(self, start_iso: str, end_iso: str,
                              limit: int = 200) -> list["Decision"]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE created_at >= ? AND created_at < ? "
            "ORDER BY id DESC LIMIT ?", (start_iso, end_iso, limit)).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def get_closed_positions_between(self, start_iso: str, end_iso: str,
                                     limit: int = 100) -> list["Position"]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'CLOSED' AND closed_at >= ? "
            "AND closed_at < ? ORDER BY id DESC LIMIT ?",
            (start_iso, end_iso, limit)).fetchall()
        return [self._row_to_position(r) for r in rows]

    def realized_pnl_between(self, start_iso: str, end_iso: str) -> float:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM positions "
            "WHERE status = 'CLOSED' AND closed_at >= ? AND closed_at < ?",
            (start_iso, end_iso)).fetchone()
        return float(r["p"])

    def activity_summary(self, start_iso: str, end_iso: str) -> dict:
        """Tally the operations the bot performed in a window. Buys/sells come from real broker
        orders; entries/exits/cancels from the position lifecycle (unambiguous); added (scale-in)
        / adjusted (stop-target trail or refresh) / adopted from the decision log."""
        def _c(sql: str, args: tuple) -> int:
            return int(self._conn.execute(sql, args).fetchone()["n"])

        w = (start_iso, end_iso)
        return {
            # OCO is a protective bracket, not a directional buy/sell the bot "did" — exclude it.
            "buys": _c("SELECT COUNT(*) n FROM orders WHERE transaction_type = 'BUY' "
                       "AND order_type != 'OCO' AND placed_at >= ? AND placed_at < ?", w),
            "sells": _c("SELECT COUNT(*) n FROM orders WHERE transaction_type = 'SELL' "
                        "AND order_type != 'OCO' AND placed_at >= ? AND placed_at < ?", w),
            "entries": _c("SELECT COUNT(*) n FROM positions WHERE status IN ('OPEN', 'CLOSED') "
                          "AND opened_at >= ? AND opened_at < ?", w),
            "exits": _c("SELECT COUNT(*) n FROM positions WHERE status = 'CLOSED' "
                        "AND closed_at >= ? AND closed_at < ?", w),
            "cancels": _c("SELECT COUNT(*) n FROM positions WHERE status = 'CANCELLED' "
                          "AND closed_at >= ? AND closed_at < ?", w),
            "added": _c("SELECT COUNT(*) n FROM decisions WHERE action = 'ADD' "
                        "AND created_at >= ? AND created_at < ?", w),
            "adjusted": _c("SELECT COUNT(*) n FROM decisions WHERE action = 'ADJUSTED' "
                           "AND created_at >= ? AND created_at < ?", w),
            "adopted": _c("SELECT COUNT(*) n FROM decisions WHERE action = 'ADOPTED' "
                          "AND created_at >= ? AND created_at < ?", w),
        }

    @staticmethod
    def _closed_window(start_iso, end_iso) -> tuple[str, tuple]:
        """WHERE fragment + params restricting to CLOSED positions, optionally within a
        closed_at window (both None = all-time)."""
        if start_iso is not None and end_iso is not None:
            return (" AND closed_at >= ? AND closed_at < ?", (start_iso, end_iso))
        return ("", ())

    def performance_summary(self, start_iso: str | None = None,
                            end_iso: str | None = None) -> dict:
        """Aggregate stats over CLOSED positions: the numbers that say whether the strategy
        works (win rate, average win/loss, expectancy per trade). All-time by default; pass a
        closed_at window for a single day."""
        clause, params = self._closed_window(start_iso, end_iso)
        r = self._conn.execute(
            "SELECT COUNT(*) AS n, "
            "       COALESCE(SUM(realized_pnl > 0), 0) AS wins, "
            "       COALESCE(SUM(realized_pnl), 0) AS total, "
            "       AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) AS avg_win, "
            "       AVG(CASE WHEN realized_pnl <= 0 THEN realized_pnl END) AS avg_loss "
            "FROM positions WHERE status = 'CLOSED'" + clause, params).fetchone()
        n, wins = int(r["n"]), int(r["wins"])
        win_rate = round(wins / n * 100, 1) if n else 0.0
        avg_win = float(r["avg_win"]) if r["avg_win"] is not None else 0.0
        avg_loss = float(r["avg_loss"]) if r["avg_loss"] is not None else 0.0
        expectancy = round((wins / n) * avg_win + ((n - wins) / n) * avg_loss, 2) if n else 0.0
        return {"trades": n, "wins": wins, "losses": n - wins, "win_rate_pct": win_rate,
                "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
                "expectancy_per_trade": expectancy, "total_pnl": round(float(r["total"]), 2)}

    def exit_reason_breakdown(self, start_iso: str | None = None,
                              end_iso: str | None = None) -> list[dict]:
        clause, params = self._closed_window(start_iso, end_iso)
        rows = self._conn.execute(
            "SELECT exit_reason, COUNT(*) AS n, COALESCE(SUM(realized_pnl), 0) AS pnl "
            "FROM positions WHERE status = 'CLOSED'" + clause +
            " GROUP BY exit_reason ORDER BY n DESC", params).fetchall()
        return [{"exit_reason": r["exit_reason"], "count": r["n"],
                 "total_pnl": round(float(r["pnl"]), 2)} for r in rows]

    # 30 days is a HARD floor: this method never deletes anything newer, so a mis-click can't
    # wipe recent history. `days` defaults to 30 and the dashboard never overrides it.
    PURGE_MIN_DAYS = 30

    def purge_old_history(self, now: datetime | None = None, days: int = PURGE_MIN_DAYS) -> dict:
        """Delete history OLDER THAN `days` (>= 30, clamped): job runs, decisions, orders, and
        only TERMINAL positions (CLOSED / CANCELLED). OPEN and PENDING positions are NEVER
        deleted regardless of age — they are live money. Config is never touched. Deletes
        children before parents so foreign keys stay intact. Returns per-table delete counts."""
        days = max(self.PURGE_MIN_DAYS, int(days))
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
        counts = {}
        counts["decisions"] = self._conn.execute(
            "DELETE FROM decisions WHERE created_at < ?", (cutoff,)).rowcount
        counts["orders"] = self._conn.execute(
            "DELETE FROM orders WHERE placed_at < ?", (cutoff,)).rowcount
        # only terminal positions, and only those no surviving decision/order still references
        counts["positions"] = self._conn.execute(
            "DELETE FROM positions WHERE status IN ('CLOSED', 'CANCELLED') AND closed_at < ? "
            "AND id NOT IN (SELECT position_id FROM decisions WHERE position_id IS NOT NULL) "
            "AND id NOT IN (SELECT position_id FROM orders WHERE position_id IS NOT NULL)",
            (cutoff,)).rowcount
        counts["job_runs"] = self._conn.execute(
            "DELETE FROM job_runs WHERE started_at < ? "
            "AND id NOT IN (SELECT run_id FROM decisions)", (cutoff,)).rowcount
        self._conn.commit()
        return counts

    # ---- swing holdings analysis (fully separate from trading) -----------------------------

    def replace_holdings(self, holdings: list[dict]) -> None:
        """Persist the latest holdings snapshot (replaces the previous one) so the Swing page
        shows the last-loaded holdings without re-hitting Groww on every open."""
        now = _utc_now()
        self._conn.execute("DELETE FROM holdings")
        for h in holdings:
            self._conn.execute(
                "INSERT INTO holdings (symbol, quantity, avg_price, fetched_at) "
                "VALUES (?,?,?,?)", (h["symbol"], h.get("quantity"), h.get("avg_price"), now))
        self._conn.commit()

    def get_holdings(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT symbol, quantity, avg_price FROM holdings ORDER BY symbol").fetchall()
        return [dict(r) for r in rows]

    def holdings_fetched_at(self) -> str | None:
        r = self._conn.execute("SELECT MAX(fetched_at) AS t FROM holdings").fetchone()
        return r["t"] if r and r["t"] else None

    def start_swing_run(self) -> int:
        cur = self._conn.execute(
            "INSERT INTO swing_runs (started_at, status) VALUES (?, 'RUNNING')", (_utc_now(),))
        self._conn.commit()
        return cur.lastrowid

    def finish_swing_run(self, run_id: int, status: str, num_holdings: int = 0,
                         error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE swing_runs SET finished_at = ?, status = ?, num_holdings = ?, error = ? "
            "WHERE id = ?", (_utc_now(), status, num_holdings, error, run_id))
        self._conn.commit()

    def set_swing_pid(self, run_id: int, pid: int) -> None:
        """Record the OS pid of the analysis subprocess so the dashboard can stop it later."""
        self._conn.execute("UPDATE swing_runs SET pid = ? WHERE id = ?", (pid, run_id))
        self._conn.commit()

    def stop_swing_run(self, run_id: int) -> int | None:
        """Mark the run STOPPED and reset the mid-flight stock (ANALYZING) back to PENDING so a
        Resume re-does it from scratch. Returns the stored pid (None if never set) so the caller
        can signal the process."""
        pid_row = self._conn.execute(
            "SELECT pid FROM swing_runs WHERE id = ?", (run_id,)).fetchone()
        self._conn.execute(
            "UPDATE swing_verdicts SET status = 'PENDING' "
            "WHERE run_id = ? AND status = 'ANALYZING'", (run_id,))
        self._conn.execute(
            "UPDATE swing_runs SET status = 'STOPPED', finished_at = ? WHERE id = ?",
            (_utc_now(), run_id))
        self._conn.commit()
        return pid_row["pid"] if pid_row else None

    def resume_swing_run(self, run_id: int) -> list[dict]:
        """Flip a STOPPED run back to RUNNING and return its still-PENDING holdings (symbol/qty/
        avg_price) for the job to process. DONE/ERROR rows are left untouched."""
        self._conn.execute(
            "UPDATE swing_runs SET status = 'RUNNING', finished_at = NULL WHERE id = ?",
            (run_id,))
        self._conn.commit()
        rows = self._conn.execute(
            "SELECT symbol, quantity, avg_price FROM swing_verdicts "
            "WHERE run_id = ? AND status = 'PENDING' ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def seed_swing_verdicts(self, run_id: int, holdings: list[dict]) -> None:
        """Insert one PENDING row per holding up front, so the UI can show a full progress table
        that fills in as each stock is analyzed."""
        for h in holdings:
            self._conn.execute(
                "INSERT INTO swing_verdicts (run_id, symbol, quantity, avg_price, status) "
                "VALUES (?,?,?,?,'PENDING')",
                (run_id, h["symbol"], h.get("quantity"), h.get("avg_price")))
        self._conn.commit()

    def update_swing_verdict(self, run_id: int, symbol: str, status: str,
                             swing: dict | None = None, shortswing: dict | None = None) -> None:
        """Move one holding's row to `status` (ANALYZING / DONE / ERROR) and, when the verdict is
        ready, write its swing + short-swing legs. Terminal states (DONE / ERROR) stamp
        analyzed_at with the completion time; ANALYZING leaves the prior stamp untouched."""
        stamp = _utc_now() if status in ("DONE", "ERROR") else None
        if swing is None and shortswing is None:
            # COALESCE so a non-terminal transition (ANALYZING) keeps any existing stamp.
            self._conn.execute(
                "UPDATE swing_verdicts SET status = ?, analyzed_at = COALESCE(?, analyzed_at) "
                "WHERE run_id = ? AND symbol = ?",
                (status, stamp, run_id, symbol))
        else:
            sw, ss = swing or {}, shortswing or {}
            self._conn.execute(
                "UPDATE swing_verdicts SET status = ?, analyzed_at = ?, swing_action = ?, "
                "swing_conviction = ?, swing_target = ?, swing_stop = ?, swing_rationale = ?, "
                "ss_action = ?, ss_conviction = ?, ss_target = ?, ss_stop = ?, ss_rationale = ? "
                "WHERE run_id = ? AND symbol = ?",
                (status, stamp, sw.get("action"), sw.get("conviction"), sw.get("target"),
                 sw.get("stop"), sw.get("rationale"), ss.get("action"), ss.get("conviction"),
                 ss.get("target"), ss.get("stop"), ss.get("rationale"), run_id, symbol))
        self._conn.commit()

    def swing_progress(self, run_id: int) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) n FROM swing_verdicts WHERE run_id = ? GROUP BY status",
            (run_id,)).fetchall()
        by = {r["status"]: r["n"] for r in rows}
        total = sum(by.values())
        done = by.get("DONE", 0) + by.get("ERROR", 0)
        return {"total": total, "done": done, "pending": by.get("PENDING", 0),
                "analyzing": by.get("ANALYZING", 0), "errors": by.get("ERROR", 0)}

    def get_swing_runs(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM swing_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def latest_swing_run(self) -> dict | None:
        r = self._conn.execute(
            "SELECT * FROM swing_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def get_swing_verdicts(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM swing_verdicts WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
