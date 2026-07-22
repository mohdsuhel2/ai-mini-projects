# Data Store (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `store.py` — a `Store` class over a single SQLite connection that is the only module touching the DB, persisting config, job runs, decisions, positions, and orders for `autoIntraday`.

**Architecture:** One class, `Store(db_path)`. On init it enables foreign keys, creates the 5-table schema idempotently, stamps `PRAGMA user_version = 1`, and seeds the single config row. Typed dataclasses (`Config`, `JobRun`, `Decision`, `Position`, `Order`) are returned from read methods. Every error is a `StoreError`. Tests run against `Store(":memory:")`.

**Tech Stack:** Python 3.10+, standard-library `sqlite3`, `dataclasses`, `datetime`; `pytest`.

## Global Constraints

- Only `store.py` touches SQL — later phases call typed methods.
- Money is `REAL` (float), quantities are `INTEGER`, timestamps are ISO-8601 UTC text (`datetime.now(timezone.utc).isoformat()`).
- `PRAGMA foreign_keys = ON` on every connection; FK violations and unknown-id updates raise `StoreError`.
- `config` holds exactly one row, enforced by `CHECK (id = 1)`; `get_config()` on a fresh DB returns the seeded default, never `None`.
- Read methods return typed dataclasses, not raw tuples.
- Every error the module raises is a `StoreError`.
- `db_path` is a constructor argument (`":memory:"` in tests).

---

### Task 1: Scaffolding, schema init, `StoreError`, connection

**Files:**
- Create: `store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `StoreError(Exception)`; `SCHEMA_VERSION = 1`; `Store.__init__(self, db_path: str)` — opens a `sqlite3.Connection` with `row_factory = sqlite3.Row`, runs `PRAGMA foreign_keys = ON`, calls `self._init_schema()`, and (in a later task) seeds config. `Store._init_schema(self) -> None` creates all 5 tables with `CREATE TABLE IF NOT EXISTS` and sets `PRAGMA user_version = SCHEMA_VERSION`. `Store.close(self) -> None`. `Store._conn` is the connection attribute later tasks use.

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
import sqlite3

import pytest

from store import Store, StoreError, SCHEMA_VERSION


def test_init_creates_all_tables():
    store = Store(":memory:")
    names = {row["name"] for row in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"config", "job_runs", "decisions", "positions", "orders"} <= names


def test_init_sets_user_version():
    store = Store(":memory:")
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_foreign_keys_enabled():
    store = Store(":memory:")
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_is_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    Store(db).close()
    Store(db).close()  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'store'`

- [ ] **Step 3: Write minimal implementation**

Create `store.py`:

```python
"""SQLite state store for autoIntraday — the only module that touches the database.

Persists config, job runs, decisions, positions, and orders. Later phases (orchestrator,
UI) call typed Store methods and never write SQL directly. See
docs/superpowers/specs/2026-07-09-data-store-design.md.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 1


class StoreError(Exception):
    """Wraps every error the store raises: constraint violations, unknown ids, bad state."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    closed_at TEXT
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
"""


class Store:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Scaffold Store with schema init and StoreError"
```

---

### Task 2: Config dataclass, seeding, get/update

**Files:**
- Modify: `store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Store`, `StoreError`, `_utc_now` from Task 1.
- Produces: `Config` dataclass (fields `mode: str, total_pool: float, max_open_positions: int, capital_per_position: float, is_paused: bool, updated_at: str`). `Store.__init__` now calls `self._seed_config()` after `_init_schema()`. `Store._seed_config(self) -> None` inserts the default row only if config is empty. `Store.get_config(self) -> Config`. `Store.update_config(self, **fields) -> Config` — updates only the given columns (whitelist: `mode, total_pool, max_open_positions, capital_per_position, is_paused`), refreshes `updated_at`, raises `StoreError` on an unknown field name, returns the new `Config`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_fresh_db_has_seeded_default_config():
    store = Store(":memory:")
    cfg = store.get_config()
    assert cfg.mode == "paper"
    assert cfg.total_pool == 0
    assert cfg.max_open_positions == 0
    assert cfg.capital_per_position == 0
    assert cfg.is_paused is False


def test_update_config_roundtrips():
    store = Store(":memory:")
    cfg = store.update_config(mode="live", total_pool=100000.0,
                              max_open_positions=5, capital_per_position=20000.0,
                              is_paused=True)
    assert cfg.mode == "live"
    assert cfg.total_pool == 100000.0
    assert cfg.max_open_positions == 5
    assert cfg.capital_per_position == 20000.0
    assert cfg.is_paused is True
    # persisted, not just returned
    assert store.get_config().max_open_positions == 5


def test_update_config_partial():
    store = Store(":memory:")
    store.update_config(total_pool=50000.0)
    cfg = store.get_config()
    assert cfg.total_pool == 50000.0
    assert cfg.mode == "paper"  # untouched


def test_update_config_unknown_field_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown config field"):
        store.update_config(bogus=1)


def test_config_second_row_rejected():
    store = Store(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO config (id, mode, total_pool, max_open_positions, "
            "capital_per_position, is_paused, updated_at) VALUES "
            "(2, 'paper', 0, 0, 0, 0, 'now')")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'get_config'`

- [ ] **Step 3: Implement**

Add near the top of `store.py` (after `_utc_now`):

```python
from dataclasses import dataclass


@dataclass
class Config:
    mode: str
    total_pool: float
    max_open_positions: int
    capital_per_position: float
    is_paused: bool
    updated_at: str


_CONFIG_FIELDS = ("mode", "total_pool", "max_open_positions",
                  "capital_per_position", "is_paused")
```

Add to `Store.__init__`, right after `self._init_schema()`:

```python
        self._seed_config()
```

Add methods to `Store`:

```python
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
                      is_paused=bool(r["is_paused"]), updated_at=r["updated_at"])

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Add config dataclass, seeding, get/update_config"
```

---

### Task 3: Job runs — `JobRun`, `start_run`, `finish_run`

**Files:**
- Modify: `store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Store`, `StoreError`, `_utc_now`.
- Produces: `JobRun` dataclass (fields `id: int, started_at: str, finished_at: str | None, status: str, mode: str, num_candidates: int | None, num_actions: int | None, error: str | None, summary: str | None`). `Store.start_run(self, mode: str) -> int` — inserts a row with `status='RUNNING'`, `started_at=_utc_now()`, returns the new id. `Store.finish_run(self, run_id: int, status: str, num_candidates: int | None = None, num_actions: int | None = None, error: str | None = None, summary: str | None = None) -> None` — sets `finished_at`, `status`, and the given fields; raises `StoreError` if `run_id` doesn't exist. `Store.get_run(self, run_id: int) -> JobRun` — raises `StoreError` if not found.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_start_run_creates_running_row():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    run = store.get_run(run_id)
    assert run.id == run_id
    assert run.status == "RUNNING"
    assert run.mode == "paper"
    assert run.started_at is not None
    assert run.finished_at is None


def test_finish_run_sets_fields():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    store.finish_run(run_id, status="SUCCESS", num_candidates=12, num_actions=2,
                     summary="ok")
    run = store.get_run(run_id)
    assert run.status == "SUCCESS"
    assert run.num_candidates == 12
    assert run.num_actions == 2
    assert run.summary == "ok"
    assert run.finished_at is not None


def test_finish_unknown_run_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown run"):
        store.finish_run(999, status="SUCCESS")


def test_get_unknown_run_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown run"):
        store.get_run(999)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'start_run'`

- [ ] **Step 3: Implement**

Add the dataclass near the other dataclasses in `store.py`:

```python
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
```

Add methods to `Store`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Add job_runs: JobRun, start_run, finish_run, get_run"
```

---

### Task 4: Positions — `Position`, open/close, open-position queries, aggregates

**Files:**
- Modify: `store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Store`, `StoreError`, `_utc_now`.
- Produces: `Position` dataclass (fields `id, symbol, exchange, side, quantity, entry_price, target_price, stop_loss, status, entry_order_id, oco_order_id, exit_price, exit_reason, realized_pnl, mode, opened_at, closed_at`). `Store.open_position(self, symbol, exchange, side, quantity, entry_price, target_price=None, stop_loss=None, entry_order_id=None, oco_order_id=None, mode="paper") -> int` (inserts with `status='OPEN'`, `opened_at=_utc_now()`, returns id). `Store.close_position(self, position_id, exit_price, exit_reason, realized_pnl) -> None` (sets status CLOSED, exit fields, `closed_at`; raises `StoreError` if unknown). `Store.get_position(self, position_id) -> Position` (raises if unknown). `Store.get_open_positions(self) -> list[Position]`. `Store.count_open_positions(self) -> int`. `Store.deployed_capital(self) -> float` (sum of `quantity * entry_price` over OPEN positions).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_open_position_roundtrips():
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG",
                              quantity=10, entry_price=2400.0, target_price=2500.0,
                              stop_loss=2350.0, entry_order_id="PAPER-1", mode="paper")
    p = store.get_position(pid)
    assert p.symbol == "RELIANCE"
    assert p.status == "OPEN"
    assert p.quantity == 10
    assert p.entry_price == 2400.0
    assert p.target_price == 2500.0
    assert p.entry_order_id == "PAPER-1"
    assert p.closed_at is None


def test_close_position_sets_exit_fields():
    store = Store(":memory:")
    pid = store.open_position(symbol="TCS", exchange="NSE", side="LONG",
                              quantity=5, entry_price=3800.0)
    store.close_position(pid, exit_price=3850.0, exit_reason="TARGET",
                         realized_pnl=250.0)
    p = store.get_position(pid)
    assert p.status == "CLOSED"
    assert p.exit_price == 3850.0
    assert p.exit_reason == "TARGET"
    assert p.realized_pnl == 250.0
    assert p.closed_at is not None


def test_close_unknown_position_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown position"):
        store.close_position(999, exit_price=1.0, exit_reason="MANUAL", realized_pnl=0.0)


def test_get_open_positions_excludes_closed():
    store = Store(":memory:")
    a = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1,
                            entry_price=100.0)
    b = store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=1,
                            entry_price=200.0)
    store.close_position(b, exit_price=210.0, exit_reason="TARGET", realized_pnl=10.0)
    open_syms = {p.symbol for p in store.get_open_positions()}
    assert open_syms == {"A"}
    assert store.count_open_positions() == 1


def test_deployed_capital_sums_open_only():
    store = Store(":memory:")
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0)   # 1000
    store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=2,
                        entry_price=500.0)   # 1000
    closed = store.open_position(symbol="C", exchange="NSE", side="LONG", quantity=5,
                                 entry_price=400.0)
    store.close_position(closed, exit_price=410.0, exit_reason="TARGET", realized_pnl=50.0)
    assert store.deployed_capital() == 2000.0


def test_deployed_capital_zero_when_no_open():
    store = Store(":memory:")
    assert store.deployed_capital() == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'open_position'`

- [ ] **Step 3: Implement**

Add the dataclass:

```python
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
```

Add a private row-mapper and the methods to `Store`:

```python
    @staticmethod
    def _row_to_position(r) -> "Position":
        return Position(
            id=r["id"], symbol=r["symbol"], exchange=r["exchange"], side=r["side"],
            quantity=r["quantity"], entry_price=r["entry_price"],
            target_price=r["target_price"], stop_loss=r["stop_loss"], status=r["status"],
            entry_order_id=r["entry_order_id"], oco_order_id=r["oco_order_id"],
            exit_price=r["exit_price"], exit_reason=r["exit_reason"],
            realized_pnl=r["realized_pnl"], mode=r["mode"], opened_at=r["opened_at"],
            closed_at=r["closed_at"])

    def open_position(self, symbol: str, exchange: str, side: str, quantity: int,
                      entry_price: float, target_price: float | None = None,
                      stop_loss: float | None = None, entry_order_id: str | None = None,
                      oco_order_id: str | None = None, mode: str = "paper") -> int:
        cur = self._conn.execute(
            "INSERT INTO positions (symbol, exchange, side, quantity, entry_price, "
            "target_price, stop_loss, status, entry_order_id, oco_order_id, mode, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)",
            (symbol, exchange, side, quantity, entry_price, target_price, stop_loss,
             entry_order_id, oco_order_id, mode, _utc_now()))
        self._conn.commit()
        return cur.lastrowid

    def close_position(self, position_id: int, exit_price: float, exit_reason: str,
                       realized_pnl: float) -> None:
        cur = self._conn.execute(
            "UPDATE positions SET status = 'CLOSED', exit_price = ?, exit_reason = ?, "
            "realized_pnl = ?, closed_at = ? WHERE id = ? AND status = 'OPEN'",
            (exit_price, exit_reason, realized_pnl, _utc_now(), position_id))
        self._conn.commit()
        if cur.rowcount == 0:
            raise StoreError(f"unknown or already-closed position id: {position_id}")

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

    def count_open_positions(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE status = 'OPEN'").fetchone()["n"]

    def deployed_capital(self) -> float:
        r = self._conn.execute(
            "SELECT COALESCE(SUM(quantity * entry_price), 0) AS c "
            "FROM positions WHERE status = 'OPEN'").fetchone()
        return float(r["c"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Add positions: open/close, queries, capital aggregates"
```

---

### Task 5: Orders — `Order`, `record_order`, `update_order_status`, FK integrity

**Files:**
- Modify: `store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Store`, `StoreError`, `_utc_now`, and `open_position` (to create a valid parent in tests).
- Produces: `Order` dataclass (fields `id, broker_order_id, position_id, symbol, transaction_type, quantity, order_type, price, status, mode, placed_at, raw_json`). `Store.record_order(self, broker_order_id, symbol, transaction_type, quantity, order_type, price=None, status="PENDING", mode="paper", position_id=None, raw_json=None) -> int` — raises `StoreError` on FK violation (unknown `position_id`). `Store.update_order_status(self, broker_order_id, status) -> None` — raises `StoreError` if no order with that `broker_order_id`. `Store.get_order(self, order_id) -> Order` (by primary key; raises if unknown). `Store.get_orders_for_position(self, position_id) -> list[Order]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_record_order_roundtrips():
    store = Store(":memory:")
    oid = store.record_order(broker_order_id="PAPER-1", symbol="RELIANCE",
                             transaction_type="BUY", quantity=10, order_type="MARKET",
                             price=2400.0, status="COMPLETE", mode="paper")
    o = store.get_order(oid)
    assert o.broker_order_id == "PAPER-1"
    assert o.transaction_type == "BUY"
    assert o.quantity == 10
    assert o.price == 2400.0
    assert o.status == "COMPLETE"
    assert o.position_id is None


def test_record_order_links_to_position():
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG",
                              quantity=10, entry_price=2400.0)
    oid = store.record_order(broker_order_id="PAPER-1", symbol="RELIANCE",
                             transaction_type="BUY", quantity=10, order_type="MARKET",
                             position_id=pid, mode="paper")
    assert store.get_order(oid).position_id == pid
    linked = store.get_orders_for_position(pid)
    assert [o.broker_order_id for o in linked] == ["PAPER-1"]


def test_record_order_bad_position_fk_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="foreign key|unknown position"):
        store.record_order(broker_order_id="PAPER-1", symbol="X", transaction_type="BUY",
                           quantity=1, order_type="MARKET", position_id=999, mode="paper")


def test_update_order_status_roundtrips():
    store = Store(":memory:")
    store.record_order(broker_order_id="PAPER-OCO-1", symbol="RELIANCE",
                       transaction_type="SELL", quantity=10, order_type="OCO",
                       status="ACTIVE", mode="paper")
    store.update_order_status("PAPER-OCO-1", "TRIGGERED")
    # fetch via the position-less path: read by broker id through get_orders_for_position
    # is not applicable, so assert through a fresh query helper
    o = store._conn.execute(
        "SELECT status FROM orders WHERE broker_order_id = 'PAPER-OCO-1'").fetchone()
    assert o["status"] == "TRIGGERED"


def test_update_unknown_order_status_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown order"):
        store.update_order_status("NOPE-1", "COMPLETE")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'record_order'`

- [ ] **Step 3: Implement**

Add the dataclass:

```python
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
```

Add methods to `Store`:

```python
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
```

NOTE on FK enforcement: SQLite only raises on a bad FK when `PRAGMA foreign_keys = ON` (set in Task 1) AND the referenced column is a real key. The `position_id INTEGER REFERENCES positions(id)` FK from the Task 1 schema makes `record_order(position_id=999)` raise `sqlite3.IntegrityError`, which this method wraps as `StoreError`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Add orders: record_order, update_order_status, FK integrity"
```

---

### Task 6: Decisions — `Decision`, `record_decision`, `get_decisions_for_run`, FK integrity

**Files:**
- Modify: `store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Store`, `StoreError`, `_utc_now`, `start_run` (valid parent run), `open_position` (valid linked position).
- Produces: `Decision` dataclass (fields `id, run_id, symbol, action, score, reason, entry_price, target_price, stop_loss, position_id, created_at, raw_json`). `Store.record_decision(self, run_id, symbol, action, score=None, reason=None, entry_price=None, target_price=None, stop_loss=None, position_id=None, raw_json=None) -> int` — raises `StoreError` on FK violation (unknown `run_id` or `position_id`). `Store.get_decisions_for_run(self, run_id) -> list[Decision]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_record_decision_roundtrips():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    did = store.record_decision(run_id=run_id, symbol="RELIANCE", action="BUY",
                                score=0.82, reason="breakout", entry_price=2400.0,
                                target_price=2500.0, stop_loss=2350.0)
    decs = store.get_decisions_for_run(run_id)
    assert len(decs) == 1
    d = decs[0]
    assert d.id == did
    assert d.symbol == "RELIANCE"
    assert d.action == "BUY"
    assert d.score == 0.82
    assert d.target_price == 2500.0
    assert d.position_id is None


def test_record_decision_links_position():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    pid = store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                              entry_price=3800.0)
    store.record_decision(run_id=run_id, symbol="TCS", action="BUY", position_id=pid)
    assert store.get_decisions_for_run(run_id)[0].position_id == pid


def test_record_decision_bad_run_fk_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="foreign key|integrity"):
        store.record_decision(run_id=999, symbol="X", action="SKIP")


def test_get_decisions_for_run_ordered_and_scoped():
    store = Store(":memory:")
    r1 = store.start_run(mode="paper")
    r2 = store.start_run(mode="paper")
    store.record_decision(run_id=r1, symbol="A", action="BUY")
    store.record_decision(run_id=r1, symbol="B", action="SKIP")
    store.record_decision(run_id=r2, symbol="C", action="BUY")
    assert [d.symbol for d in store.get_decisions_for_run(r1)] == ["A", "B"]
    assert [d.symbol for d in store.get_decisions_for_run(r2)] == ["C"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'record_decision'`

- [ ] **Step 3: Implement**

Add the dataclass:

```python
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
```

Add methods to `Store`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (28 tests)

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Add decisions: record_decision, get_decisions_for_run, FK integrity"
```

---

### Task 7: README section + full-suite smoke

**Files:**
- Modify: `README.md`
- Test: (none new — this task documents and verifies the whole Phase 2 suite)

**Interfaces:**
- Consumes: everything above. Produces: no new API.

- [ ] **Step 1: Run the full test suite (both phases) to confirm nothing regressed**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — Phase 1's `tests/test_groww_client.py` (29) and Phase 2's `tests/test_store.py` (28) all green.

- [ ] **Step 2: Add a Phase 2 section to `README.md`**

Append to `README.md`:

```markdown
## Phase 2: Data store

`store.py` is a SQLite-backed `Store` — the only module that touches the database. It
persists config (paper/live mode, pool size, capital limits, pause flag), job runs,
decisions, positions, and orders. See
`docs/superpowers/specs/2026-07-09-data-store-design.md`.

\`\`\`python
from store import Store
store = Store("autointraday.db")   # ":memory:" in tests
cfg = store.update_config(mode="paper", total_pool=100000, max_open_positions=5,
                          capital_per_position=20000)
run_id = store.start_run(mode=cfg.mode)
# ... record decisions, open/close positions, record orders ...
store.finish_run(run_id, status="SUCCESS", num_candidates=12, num_actions=2)
\`\`\`

### Test

\`\`\`bash
.venv/bin/python -m pytest tests/test_store.py -v
\`\`\`
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document Phase 2 data store in README"
```

---

## Self-Review Notes

- **Spec coverage:** config single-row + get/update (Task 2) · job_runs (Task 3) · positions with open/close + `count_open_positions`/`deployed_capital` aggregates Phase 4 needs (Task 4) · orders as durable source of truth + `update_order_status` (Task 5) · decisions linked to runs and positions (Task 6) · schema idempotent init + FK on + user_version (Task 1) · REAL money / INTEGER qty / ISO-UTC timestamps, `StoreError`-only errors, dataclass returns, `db_path` constructor arg, config `CHECK (id=1)` (throughout). All spec sections map to a task.
- **Type consistency:** dataclass field names match the `SELECT *` column names in every `_row_to_*` mapper; `open_position`/`record_order`/`record_decision` parameter names match their INSERT columns; the `Position`/`Order`/`Decision`/`JobRun`/`Config` dataclasses are defined once and returned consistently.
- **No placeholders:** every step has complete runnable code and exact expected test counts (4 → 9 → 13 → 19 → 24 → 28).
- **FK note:** FK enforcement depends on `PRAGMA foreign_keys = ON` (Task 1) — Tasks 5 and 6 rely on it for the bad-FK tests; called out explicitly in Task 5.
