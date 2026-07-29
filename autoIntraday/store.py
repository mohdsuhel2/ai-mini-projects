"""SQLite state store for autoIntraday — the only module that touches the database.

Persists config, job runs, decisions, positions, and orders. Later phases (orchestrator,
UI) call typed Store methods and never write SQL directly. See
docs/superpowers/specs/2026-07-09-data-store-design.md.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1

# Every strategy-scoped row (job_runs/positions/decisions/orders) carries a strategy_id. Legacy
# rows and the single-strategy default resolve to this id (the pre-existing V1 strategy), so a DB
# written before multi-strategy support behaves byte-identically. MUST match
# strategies.DEFAULT_STRATEGY_ID and the DDL DEFAULTs above.
DEFAULT_STRATEGY_ID = "intraday-v1"


class StoreError(Exception):
    """Wraps every error the store raises: constraint violations, unknown ids, bad state."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sid_where(strategy_id):
    """WHERE fragment + args for an optional strategy filter on a query that has no other WHERE.
    strategy_id=None -> no filter (all strategies), preserving the pre-multi-strategy behaviour."""
    return ("WHERE strategy_id = ?", [strategy_id]) if strategy_id else ("", [])


def _sid_and(strategy_id):
    """AND fragment + args for an optional strategy filter appended to an EXISTING WHERE clause."""
    return (" AND strategy_id = ?", [strategy_id]) if strategy_id else ("", [])


@dataclass
class Config:
    mode: str
    total_pool: float
    max_open_positions: int
    capital_per_position: float
    is_paused: bool
    updated_at: str
    primer_enabled: bool = False
    # Claude-window primer fire time (IST "HH:MM") — a throwaway call that starts the 5-hour usage
    # window early so it resets during trading. Configurable from Settings ▸ Schedule.
    primer_time: str = "07:30"
    # Multi-strategy runtime selection (the UI control surface). compare_strategies is a parsed
    # list here; it is persisted as a comma-separated TEXT column.
    compare_enabled: bool = False
    live_strategy: str = "intraday-v1"
    paper_strategy: str = "intraday-v1"
    compare_strategies: list = field(default_factory=lambda: ["intraday-v1", "intraday-v2"])
    # Early profit-taking (stop before the far target). Percentages are RETURN ON DEPLOYED MARGIN
    # (at LEVERAGE x, e.g. 7% return == a ~1.4% price move): book half at _partial_pct, exit the
    # whole position at _full_pct. Toggled off => let winners ride to the structural target.
    profit_book_enabled: bool = True
    profit_book_partial_pct: float = 7.0
    profit_book_full_pct: float = 15.0
    # Execution "breathing space" applied to Claude's levels before an order is placed: entry is
    # nudged toward price (fills easier), the stop is widened AWAY from entry (fewer noise
    # stop-outs; rupee risk unchanged since sizing uses the widened stop), and the target keeps
    # (100 - shave)% of the projected move. All as percentages.
    entry_tolerance_pct: float = 0.25
    stop_tolerance_pct: float = 0.35
    target_shave_pct: float = 10.0
    # R:R gate geometry. True (default): judge the entry's reward:risk on the RAW engine levels —
    # the execution margins above only shape the actual orders and never veto a trade by eroding
    # its R:R. False: re-gate on the POST-margin geometry (a shaved target / widened stop must
    # still clear MIN_RISK_REWARD after margins).
    rr_gate_pre_margin: bool = True
    # Exit placement (LIVE-only). Where the stop+target exit lives:
    #   db_only  — soft levels; the 5-min cycle market-exits when price hits them (default, = today)
    #   armed    — place the stop+target broker bracket when price is within arm_exit_band_pct of a leg
    #   on_fill  — place the broker bracket immediately when the entry fills, kept in sync as it trails
    # See docs/superpowers/specs/2026-07-28-exit-placement-modes-design.md. arm_exit_band_pct is the
    # 'armed' proximity band. arm_exit_enabled is retired (kept for back-compat; migrated to exit_mode).
    exit_mode: str = "db_only"
    arm_exit_enabled: bool = False
    arm_exit_band_pct: float = 1.0
    # Last-resort stop for any OPEN position the engine has not given one — an adopted manual
    # position, or a read that returned WAIT. Percent from entry. 0 disables the floor. Replaced
    # by the engine's structural stop as soon as it arrives, and never widened (ratchet rule).
    adopt_fallback_stop_pct: float = 1.0
    # Scale-into-strength (pyramiding into a persisting winner). OFF by default (opt-in). When the
    # engine re-affirms a STRONG same-side entry for pyramid_confirm_cycles consecutive cycles, add
    # pyramid_add_pct% of the per-position capital at market, up to pyramid_max_adds times (a
    # position may reach 1 + add_pct*max_adds/100 x its base capital). A pyramided position's
    # full-book rises to pyramid_full_pct (return-on-margin) so the extra capital can chase a bigger
    # move. The structural stop is never widened by an add. See the 2026-07-29 design spec.
    pyramid_enabled: bool = False
    pyramid_add_pct: float = 50.0
    pyramid_max_adds: int = 2
    pyramid_full_pct: float = 40.0
    pyramid_confirm_cycles: int = 2
    pyramid_min_quality: float = 80.0
    pyramid_min_confidence: float = 75.0


_CONFIG_FIELDS = ("mode", "total_pool", "max_open_positions",
                  "capital_per_position", "is_paused", "primer_enabled", "primer_time",
                  "compare_enabled", "live_strategy", "paper_strategy", "compare_strategies",
                  "profit_book_enabled", "profit_book_partial_pct", "profit_book_full_pct",
                  "entry_tolerance_pct", "stop_tolerance_pct", "target_shave_pct",
                  "rr_gate_pre_margin", "exit_mode", "arm_exit_enabled", "arm_exit_band_pct",
                  "adopt_fallback_stop_pct", "pyramid_enabled", "pyramid_add_pct",
                  "pyramid_max_adds", "pyramid_full_pct", "pyramid_confirm_cycles",
                  "pyramid_min_quality", "pyramid_min_confidence")


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
    # Scale-into-strength (pyramiding): pyramid_count = adds performed on this position
    # (0..pyramid_max_adds); pyramid_signal_count = consecutive cycles the engine re-affirmed a
    # STRONG same-side entry, reset on any miss and after each add (orchestrator._maybe_pyramid).
    pyramid_count: int = 0
    pyramid_signal_count: int = 0
    # Armed broker exit order ids + their limit price (LIVE-only): a resting SELL LIMIT placed at
    # the broker when price neared the partial / full profit level. The price lets a detected fill
    # be booked deterministically (a LIMIT fills at its price or better). Cleared on fill or cancel.
    armed_partial_order_id: str | None = None
    armed_full_order_id: str | None = None
    armed_partial_price: float | None = None
    armed_full_price: float | None = None
    # Broker exit bracket (LIVE eager modes): the resting stop (SL_M) + target (LIMIT) order ids and
    # their prices at Groww. Software OCO — one leg filling cancels the other. Cleared on fill/cancel.
    broker_stop_order_id: str | None = None
    broker_stop_price: float | None = None
    broker_target_order_id: str | None = None
    broker_target_price: float | None = None
    # Pinned to eager bracket management regardless of the global exit_mode. Set when reconcile
    # cancels a user's own resting exit order: the bot must REPLACE that protection with its own
    # broker bracket, never merely remove it.
    force_bracket: bool = False
    strategy_id: str = DEFAULT_STRATEGY_ID


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
    updated_at TEXT NOT NULL,
    compare_enabled INTEGER NOT NULL DEFAULT 0,
    live_strategy TEXT NOT NULL DEFAULT 'intraday-v1',
    paper_strategy TEXT NOT NULL DEFAULT 'intraday-v1',
    compare_strategies TEXT NOT NULL DEFAULT 'intraday-v1,intraday-v2',
    profit_book_enabled INTEGER NOT NULL DEFAULT 1,
    profit_book_partial_pct REAL NOT NULL DEFAULT 7.0,
    profit_book_full_pct REAL NOT NULL DEFAULT 15.0,
    entry_tolerance_pct REAL NOT NULL DEFAULT 0.25,
    stop_tolerance_pct REAL NOT NULL DEFAULT 0.35,
    target_shave_pct REAL NOT NULL DEFAULT 10.0,
    rr_gate_pre_margin INTEGER NOT NULL DEFAULT 1,
    arm_exit_enabled INTEGER NOT NULL DEFAULT 0,
    arm_exit_band_pct REAL NOT NULL DEFAULT 1.0,
    exit_mode TEXT NOT NULL DEFAULT 'db_only',
    adopt_fallback_stop_pct REAL NOT NULL DEFAULT 1.0,
    pyramid_enabled INTEGER NOT NULL DEFAULT 0,
    pyramid_add_pct REAL NOT NULL DEFAULT 50.0,
    pyramid_max_adds INTEGER NOT NULL DEFAULT 2,
    pyramid_full_pct REAL NOT NULL DEFAULT 40.0,
    pyramid_confirm_cycles INTEGER NOT NULL DEFAULT 2,
    pyramid_min_quality REAL NOT NULL DEFAULT 80.0,
    pyramid_min_confidence REAL NOT NULL DEFAULT 75.0
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
    summary TEXT,
    strategy_id TEXT NOT NULL DEFAULT 'intraday-v1'
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
    reverse_signal_count INTEGER NOT NULL DEFAULT 0,
    pyramid_count INTEGER NOT NULL DEFAULT 0,
    pyramid_signal_count INTEGER NOT NULL DEFAULT 0,
    armed_partial_order_id TEXT,
    armed_full_order_id TEXT,
    armed_partial_price REAL,
    armed_full_price REAL,
    broker_stop_order_id TEXT,
    broker_stop_price REAL,
    broker_target_order_id TEXT,
    broker_target_price REAL,
    force_bracket INTEGER NOT NULL DEFAULT 0,
    strategy_id TEXT NOT NULL DEFAULT 'intraday-v1'
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
    raw_json TEXT,
    strategy_id TEXT NOT NULL DEFAULT 'intraday-v1'
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
    raw_json TEXT,
    strategy_id TEXT NOT NULL DEFAULT 'intraday-v1'
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
        if "pyramid_count" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN pyramid_count "
                               "INTEGER NOT NULL DEFAULT 0")
        if "pyramid_signal_count" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN pyramid_signal_count "
                               "INTEGER NOT NULL DEFAULT 0")
        if "armed_partial_order_id" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN armed_partial_order_id TEXT")
        if "armed_full_order_id" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN armed_full_order_id TEXT")
        if "armed_partial_price" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN armed_partial_price REAL")
        if "armed_full_price" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN armed_full_price REAL")
        if "broker_stop_order_id" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN broker_stop_order_id TEXT")
        if "broker_stop_price" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN broker_stop_price REAL")
        if "broker_target_order_id" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN broker_target_order_id TEXT")
        if "broker_target_price" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN broker_target_price REAL")
        if "force_bracket" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN force_bracket INTEGER "
                               "NOT NULL DEFAULT 0")
        # Multi-strategy isolation: legacy rows in the four strategy-scoped tables backfill to the
        # pre-existing V1 strategy so a pre-multi-strategy DB is unchanged in behaviour.
        for table in ("job_runs", "positions", "decisions", "orders"):
            tcols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if tcols and "strategy_id" not in tcols:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN strategy_id TEXT NOT NULL "
                    f"DEFAULT '{DEFAULT_STRATEGY_ID}'")
        ccols = {r["name"] for r in self._conn.execute("PRAGMA table_info(config)")}
        if "primer_enabled" not in ccols:
            self._conn.execute(
                "ALTER TABLE config ADD COLUMN primer_enabled INTEGER NOT NULL DEFAULT 0")
        if "primer_time" not in ccols:
            self._conn.execute(
                "ALTER TABLE config ADD COLUMN primer_time TEXT NOT NULL DEFAULT '07:30'")
        if "compare_enabled" not in ccols:
            self._conn.execute(
                "ALTER TABLE config ADD COLUMN compare_enabled INTEGER NOT NULL DEFAULT 0")
        if "live_strategy" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN live_strategy TEXT NOT NULL "
                               "DEFAULT 'intraday-v1'")
        if "paper_strategy" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN paper_strategy TEXT NOT NULL "
                               "DEFAULT 'intraday-v1'")
        if "compare_strategies" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN compare_strategies TEXT NOT NULL "
                               "DEFAULT 'intraday-v1,intraday-v2'")
        if "profit_book_enabled" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN profit_book_enabled INTEGER NOT "
                               "NULL DEFAULT 1")
        if "profit_book_partial_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN profit_book_partial_pct REAL NOT "
                               "NULL DEFAULT 7.0")
        if "profit_book_full_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN profit_book_full_pct REAL NOT "
                               "NULL DEFAULT 15.0")
        if "entry_tolerance_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN entry_tolerance_pct REAL NOT "
                               "NULL DEFAULT 0.25")
        if "stop_tolerance_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN stop_tolerance_pct REAL NOT "
                               "NULL DEFAULT 0.35")
        if "target_shave_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN target_shave_pct REAL NOT "
                               "NULL DEFAULT 10.0")
        if "rr_gate_pre_margin" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN rr_gate_pre_margin INTEGER NOT "
                               "NULL DEFAULT 1")
        if "arm_exit_enabled" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN arm_exit_enabled INTEGER NOT "
                               "NULL DEFAULT 0")
        if "arm_exit_band_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN arm_exit_band_pct REAL NOT "
                               "NULL DEFAULT 1.0")
        if "exit_mode" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN exit_mode TEXT NOT NULL "
                               "DEFAULT 'db_only'")
            # one-time carry-over: a previously-enabled armed exit becomes the 'armed' mode
            if "arm_exit_enabled" in ccols:
                self._conn.execute("UPDATE config SET exit_mode='armed' WHERE arm_exit_enabled=1")
        if "adopt_fallback_stop_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN adopt_fallback_stop_pct REAL "
                               "NOT NULL DEFAULT 1.0")
        for col, ddl in (("pyramid_enabled", "INTEGER NOT NULL DEFAULT 0"),
                         ("pyramid_add_pct", "REAL NOT NULL DEFAULT 50.0"),
                         ("pyramid_max_adds", "INTEGER NOT NULL DEFAULT 2"),
                         ("pyramid_full_pct", "REAL NOT NULL DEFAULT 40.0"),
                         ("pyramid_confirm_cycles", "INTEGER NOT NULL DEFAULT 2"),
                         ("pyramid_min_quality", "REAL NOT NULL DEFAULT 80.0"),
                         ("pyramid_min_confidence", "REAL NOT NULL DEFAULT 75.0")):
            if col not in ccols:
                self._conn.execute(f"ALTER TABLE config ADD COLUMN {col} {ddl}")
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
        raw = (r["compare_strategies"] or "").strip()
        compare_strategies = [s.strip() for s in raw.split(",") if s.strip()]
        return Config(mode=r["mode"], total_pool=r["total_pool"],
                      max_open_positions=r["max_open_positions"],
                      capital_per_position=r["capital_per_position"],
                      is_paused=bool(r["is_paused"]), updated_at=r["updated_at"],
                      primer_enabled=bool(r["primer_enabled"]),
                      primer_time=(r["primer_time"] if "primer_time" in r.keys() else "07:30"),
                      compare_enabled=bool(r["compare_enabled"]),
                      live_strategy=r["live_strategy"], paper_strategy=r["paper_strategy"],
                      compare_strategies=compare_strategies,
                      profit_book_enabled=bool(r["profit_book_enabled"]),
                      profit_book_partial_pct=r["profit_book_partial_pct"],
                      profit_book_full_pct=r["profit_book_full_pct"],
                      entry_tolerance_pct=r["entry_tolerance_pct"],
                      stop_tolerance_pct=r["stop_tolerance_pct"],
                      target_shave_pct=r["target_shave_pct"],
                      rr_gate_pre_margin=bool(r["rr_gate_pre_margin"]),
                      arm_exit_enabled=bool(r["arm_exit_enabled"]),
                      arm_exit_band_pct=r["arm_exit_band_pct"],
                      exit_mode=r["exit_mode"],
                      adopt_fallback_stop_pct=r["adopt_fallback_stop_pct"],
                      pyramid_enabled=bool(r["pyramid_enabled"]),
                      pyramid_add_pct=r["pyramid_add_pct"],
                      pyramid_max_adds=r["pyramid_max_adds"],
                      pyramid_full_pct=r["pyramid_full_pct"],
                      pyramid_confirm_cycles=r["pyramid_confirm_cycles"],
                      pyramid_min_quality=r["pyramid_min_quality"],
                      pyramid_min_confidence=r["pyramid_min_confidence"])

    def update_config(self, **fields) -> Config:
        for key in fields:
            if key not in _CONFIG_FIELDS:
                raise StoreError(f"unknown config field: {key}")
        if "compare_strategies" in fields and isinstance(fields["compare_strategies"], (list, tuple)):
            fields["compare_strategies"] = ",".join(fields["compare_strategies"])
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
            values.append(_utc_now())
            self._conn.execute(f"UPDATE config SET {sets}, updated_at = ? WHERE id = 1", values)
            self._conn.commit()
        return self.get_config()

    def start_run(self, mode: str, strategy_id: str = DEFAULT_STRATEGY_ID) -> int:
        cur = self._conn.execute(
            "INSERT INTO job_runs (started_at, status, mode, strategy_id) "
            "VALUES (?, 'RUNNING', ?, ?)", (_utc_now(), mode, strategy_id))
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
            reverse_signal_count=r["reverse_signal_count"] or 0,
            pyramid_count=(r["pyramid_count"] or 0) if "pyramid_count" in r.keys() else 0,
            pyramid_signal_count=(
                (r["pyramid_signal_count"] or 0) if "pyramid_signal_count" in r.keys() else 0),
            armed_partial_order_id=r["armed_partial_order_id"],
            armed_full_order_id=r["armed_full_order_id"],
            armed_partial_price=r["armed_partial_price"],
            armed_full_price=r["armed_full_price"],
            broker_stop_order_id=r["broker_stop_order_id"],
            broker_stop_price=r["broker_stop_price"],
            broker_target_order_id=r["broker_target_order_id"],
            broker_target_price=r["broker_target_price"],
            force_bracket=bool(r["force_bracket"]),
            strategy_id=(r["strategy_id"] if "strategy_id" in r.keys() else DEFAULT_STRATEGY_ID))

    def open_position(self, symbol: str, exchange: str, side: str, quantity: int,
                      entry_price: float, target_price: float | None = None,
                      stop_loss: float | None = None, entry_order_id: str | None = None,
                      oco_order_id: str | None = None, mode: str = "paper",
                      status: str = "OPEN", trigger_kind: str | None = None,
                      entry_quality: float | None = None,
                      strategy_id: str = DEFAULT_STRATEGY_ID) -> int:
        """Create a position. status='OPEN' fills immediately (market entry); status='PENDING'
        is a resting order that occupies a slot + capital but is not yet in the market — a later
        cycle activates it (fill) or cancels it (see activate_position/cancel_position)."""
        cur = self._conn.execute(
            "INSERT INTO positions (symbol, exchange, side, quantity, entry_price, "
            "target_price, stop_loss, status, entry_order_id, oco_order_id, mode, opened_at, "
            "trigger_kind, entry_quality, strategy_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, exchange, side, quantity, entry_price, target_price, stop_loss,
             status, entry_order_id, oco_order_id, mode, _utc_now(), trigger_kind,
             entry_quality, strategy_id))
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

    def set_pyramid_signal_count(self, position_id: int, count: int) -> None:
        """Track consecutive STRONG same-side re-affirms on an OPEN position, so a pyramid add
        needs pyramid_confirm_cycles in a row rather than firing on one read (see orchestrator)."""
        self._conn.execute(
            "UPDATE positions SET pyramid_signal_count = ? WHERE id = ? AND status = 'OPEN'",
            (count, position_id))
        self._conn.commit()

    def record_pyramid_add(self, position_id: int) -> None:
        """Book one scale-into-strength add: increment pyramid_count and reset the persistence
        counter so the position must re-persist before it can add again. Quantity/avg entry are
        blended separately via add_to_position; the structural stop is left untouched."""
        self._conn.execute(
            "UPDATE positions SET pyramid_count = pyramid_count + 1, pyramid_signal_count = 0 "
            "WHERE id = ? AND status = 'OPEN'", (position_id,))
        self._conn.commit()

    def set_bracket_leg(self, position_id: int, which: str, order_id: str | None,
                        price: float | None = None) -> None:
        """Record (order_id + price) or clear (both None) a broker bracket leg on an OPEN position.
        which is 'stop' or 'target'. Cleared on fill/cancel so the next cycle can (re)place it."""
        id_col, px_col = {"stop": ("broker_stop_order_id", "broker_stop_price"),
                          "target": ("broker_target_order_id", "broker_target_price")}[which]
        self._conn.execute(
            f"UPDATE positions SET {id_col} = ?, {px_col} = ? WHERE id = ? AND status = 'OPEN'",
            (order_id, price, position_id))
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

    def update_position_size(self, position_id: int, quantity: int, entry_price: float) -> None:
        """Sync an OPEN position's size AND blended cost basis to broker reality (a manual ADD
        detected by reconcile). entry_price is the broker's reported average — the true cost
        basis — so booked P&L stays honest. Protective levels are deliberately untouched: the
        ratchet rule in the orchestrator owns them."""
        cur = self._conn.execute(
            "UPDATE positions SET quantity = ?, entry_price = ? WHERE id = ? AND status = 'OPEN'",
            (quantity, entry_price, position_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown open position id (or not open): {position_id}")

    def set_force_bracket(self, position_id: int) -> None:
        """Pin an OPEN position to eager bracket management regardless of the global exit_mode.
        Set when reconcile takes over a user's own resting exit order — cancelling their stop
        while exit_mode is 'db_only' would otherwise leave the position barer than before."""
        self._conn.execute(
            "UPDATE positions SET force_bracket = 1 WHERE id = ? AND status = 'OPEN'",
            (position_id,))
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

    def get_open_positions(self, strategy_id: str = DEFAULT_STRATEGY_ID) -> list["Position"]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' AND strategy_id = ? ORDER BY id",
            (strategy_id,)).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_pending_positions(self, strategy_id: str = DEFAULT_STRATEGY_ID) -> list["Position"]:
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'PENDING' AND strategy_id = ? ORDER BY id",
            (strategy_id,)).fetchall()
        return [self._row_to_position(r) for r in rows]

    def count_open_positions(self, strategy_id: str = DEFAULT_STRATEGY_ID) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status = 'OPEN' AND strategy_id = ?",
            (strategy_id,)).fetchone()["n"]

    def deployed_capital(self, strategy_id: str = DEFAULT_STRATEGY_ID) -> float:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(quantity * entry_price), 0) AS c "
            "FROM positions WHERE status = 'OPEN' AND strategy_id = ?", (strategy_id,)).fetchone()
        return float(r["c"])

    def count_committed_positions(self, strategy_id: str = DEFAULT_STRATEGY_ID) -> int:
        """OPEN + PENDING — every slot currently spoken for (a resting order reserves a slot)."""
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status IN ('OPEN', 'PENDING') "
            "AND strategy_id = ?", (strategy_id,)).fetchone()["n"]

    def committed_capital(self, strategy_id: str = DEFAULT_STRATEGY_ID) -> float:
        """Capital tied up in OPEN + PENDING positions — reserved so resting orders can't
        over-commit the pool."""
        r = self._conn.execute(
            "SELECT COALESCE(SUM(quantity * entry_price), 0) AS c "
            "FROM positions WHERE status IN ('OPEN', 'PENDING') AND strategy_id = ?",
            (strategy_id,)).fetchone()
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
                        raw_json: str | None = None,
                        strategy_id: str = DEFAULT_STRATEGY_ID) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO decisions (run_id, symbol, action, score, reason, entry_price, "
                "target_price, stop_loss, position_id, created_at, raw_json, strategy_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, symbol, action, score, reason, entry_price, target_price,
                 stop_loss, position_id, _utc_now(), raw_json, strategy_id))
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
                     position_id: int | None = None, raw_json: str | None = None,
                     strategy_id: str = DEFAULT_STRATEGY_ID) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO orders (broker_order_id, position_id, symbol, transaction_type, "
                "quantity, order_type, price, status, mode, placed_at, raw_json, strategy_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (broker_order_id, position_id, symbol, transaction_type, quantity,
                 order_type, price, status, mode, _utc_now(), raw_json, strategy_id))
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

    def get_recent_runs(self, limit: int = 20,
                        strategy_id: str | None = None) -> list["JobRun"]:
        where, args = _sid_where(strategy_id)
        rows = self._conn.execute(
            f"SELECT * FROM job_runs {where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_recent_decisions(self, limit: int = 50,
                             strategy_id: str | None = None) -> list["Decision"]:
        where, args = _sid_where(strategy_id)
        rows = self._conn.execute(
            f"SELECT * FROM decisions {where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def get_recent_positions(self, limit: int = 50,
                             strategy_id: str | None = None) -> list["Position"]:
        where, args = _sid_where(strategy_id)
        rows = self._conn.execute(
            f"SELECT * FROM positions {where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._row_to_position(r) for r in rows]

    def strategy_ids_present(self) -> list[str]:
        """Distinct strategy_ids that actually have positions — lets the dashboard show a strategy
        selector only when more than one strategy has traded (so single-strategy stays unchanged)."""
        rows = self._conn.execute(
            "SELECT DISTINCT strategy_id FROM positions ORDER BY strategy_id").fetchall()
        return [r["strategy_id"] for r in rows]

    def positions_for_strategy(self, strategy_id: str, status: str | None = None,
                               limit: int | None = None) -> list["Position"]:
        """All of one strategy's positions, optionally filtered by status, oldest first (so a
        closed list forms a natural equity curve). Used by the compare analytics."""
        sql = "SELECT * FROM positions WHERE strategy_id = ?"
        args: list = [strategy_id]
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [self._row_to_position(r) for r in self._conn.execute(sql, args).fetchall()]

    def decisions_for_strategy(self, strategy_id: str, limit: int = 300) -> list["Decision"]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE strategy_id = ? ORDER BY id DESC LIMIT ?",
            (strategy_id, limit)).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def realized_pnl_total(self, strategy_id: str | None = None) -> float:
        sid, a = _sid_and(strategy_id)
        r = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM positions "
            "WHERE status = 'CLOSED'" + sid, a).fetchone()
        return float(r["p"])

    def realized_pnl_since(self, iso_date: str,
                           strategy_id: str = DEFAULT_STRATEGY_ID) -> float:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM positions "
            "WHERE status = 'CLOSED' AND closed_at >= ? AND strategy_id = ?",
            (iso_date, strategy_id)).fetchone()
        return float(r["p"])

    def get_runs_between(self, start_iso: str, end_iso: str, limit: int = 100,
                         strategy_id: str | None = None) -> list["JobRun"]:
        sid, a = _sid_and(strategy_id)
        rows = self._conn.execute(
            "SELECT * FROM job_runs WHERE started_at >= ? AND started_at < ?" + sid +
            " ORDER BY id DESC LIMIT ?", (start_iso, end_iso, *a, limit)).fetchall()
        return [self._row_to_run(r) for r in rows]

    def get_decisions_between(self, start_iso: str, end_iso: str, limit: int = 200,
                              strategy_id: str | None = None) -> list["Decision"]:
        sid, a = _sid_and(strategy_id)
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE created_at >= ? AND created_at < ?" + sid +
            " ORDER BY id DESC LIMIT ?", (start_iso, end_iso, *a, limit)).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def get_closed_positions_between(self, start_iso: str, end_iso: str, limit: int = 100,
                                     strategy_id: str | None = None) -> list["Position"]:
        sid, a = _sid_and(strategy_id)
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'CLOSED' AND closed_at >= ? "
            "AND closed_at < ?" + sid + " ORDER BY id DESC LIMIT ?",
            (start_iso, end_iso, *a, limit)).fetchall()
        return [self._row_to_position(r) for r in rows]

    def realized_pnl_between(self, start_iso: str, end_iso: str,
                             strategy_id: str | None = None) -> float:
        sid, a = _sid_and(strategy_id)
        r = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS p FROM positions "
            "WHERE status = 'CLOSED' AND closed_at >= ? AND closed_at < ?" + sid,
            (start_iso, end_iso, *a)).fetchone()
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
                            end_iso: str | None = None,
                            strategy_id: str | None = None) -> dict:
        """Aggregate stats over CLOSED positions: the numbers that say whether the strategy
        works (win rate, average win/loss, expectancy per trade). All-time by default; pass a
        closed_at window for a single day."""
        clause, params = self._closed_window(start_iso, end_iso)
        sid, a = _sid_and(strategy_id)
        clause, params = clause + sid, params + tuple(a)
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
                              end_iso: str | None = None,
                              strategy_id: str | None = None) -> list[dict]:
        clause, params = self._closed_window(start_iso, end_iso)
        sid, a = _sid_and(strategy_id)
        clause, params = clause + sid, params + tuple(a)
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


# Store methods that are scoped to a single strategy's ledger — ScopedStore injects strategy_id
# into these (both the inserts that TAG a row and the aggregates/lists that FILTER by it).
_SCOPED_METHODS = frozenset({
    "start_run", "record_decision", "record_order", "open_position",
    "get_open_positions", "get_pending_positions", "count_open_positions",
    "count_committed_positions", "committed_capital", "deployed_capital", "realized_pnl_since",
    # dashboard/history readers — scoped so a ScopedStore view shows one strategy's ledger
    "get_recent_positions", "get_recent_decisions", "get_recent_runs", "realized_pnl_total",
    "get_runs_between", "get_decisions_between", "get_closed_positions_between",
    "realized_pnl_between", "performance_summary", "exit_reason_breakdown",
})


class ScopedStore:
    """A Store view bound to one strategy_id. It injects that id into the strategy-scoped methods
    (so an Orchestrator's existing `self.store.*` calls operate on one strategy's isolated ledger)
    and delegates every other method to the underlying Store unchanged. This is how the trading
    engine stays strategy-agnostic with near-zero churn: hand it a ScopedStore instead of a Store.
    Two ScopedStores over the same DB with different ids share nothing at the ledger level."""

    def __init__(self, store: Store, strategy_id: str = DEFAULT_STRATEGY_ID):
        self._store = store
        self.strategy_id = strategy_id

    def __getattr__(self, name):
        attr = getattr(self._store, name)
        if name in _SCOPED_METHODS and callable(attr):
            def scoped(*args, **kwargs):
                kwargs.setdefault("strategy_id", self.strategy_id)
                return attr(*args, **kwargs)
            return scoped
        return attr
