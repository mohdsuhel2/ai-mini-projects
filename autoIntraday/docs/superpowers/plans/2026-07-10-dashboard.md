# UI Dashboard (Phase 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Streamlit dashboard to see status (positions, decisions, job runs, P&L) and control the system (pause/resume, capital rules, paper/live mode), reading the same SQLite DB the scheduler writes.

**Architecture:** `store.py` gains a few cross-run read queries (SQL stays in the store). `dashboard_data.py` holds pure view functions over a `Store` (unit-tested). `dashboard.py` is a thin Streamlit render layer + config controls (manual-verified). The UI's only writes are config updates via the existing `update_config`; no broker/LLM calls.

**Tech Stack:** Python 3.10+, `streamlit`, standard-library `sqlite3`/`datetime`, `pytest`. Depends on the in-repo `store.py`.

## Global Constraints

- Only `store.py` touches SQL — the dashboard and its data layer call typed store methods.
- The dashboard makes NO broker or LLM calls (pure-store). Its only writes are config updates through the existing whitelist-guarded `update_config`.
- Open positions show plan (entry/target/stop), not live P&L; realized P&L only on closed positions.
- Money is `REAL`, timestamps ISO-8601 UTC — consistent with Phase 2.
- Switching `mode` to `live` from the UI requires an explicit confirmation step.
- View functions never raise on empty tables (empty list / zero / seeded default).

---

### Task 1: Store read queries for the dashboard

**Files:**
- Modify: `store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: existing `Store`, `_row_to_position`, `_row_to_decision`, `JobRun`, `Decision`, `Position`.
- Produces: `Store.get_recent_runs(self, limit=20) -> list[JobRun]` (newest id first); `Store.get_recent_decisions(self, limit=50) -> list[Decision]` (newest first); `Store.get_recent_positions(self, limit=50) -> list[Position]` (newest first); `Store.realized_pnl_total(self) -> float` (sum over CLOSED); `Store.realized_pnl_since(self, iso_date: str) -> float` (sum over positions CLOSED with `closed_at >= iso_date`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_get_recent_runs_newest_first_and_limited():
    store = Store(":memory:")
    ids = [store.start_run("paper") for _ in range(5)]
    recent = store.get_recent_runs(limit=3)
    assert [r.id for r in recent] == list(reversed(ids))[:3]


def test_get_recent_decisions_newest_first():
    store = Store(":memory:")
    run_id = store.start_run("paper")
    store.record_decision(run_id=run_id, symbol="A", action="BUY_NOW")
    store.record_decision(run_id=run_id, symbol="B", action="WAIT")
    recent = store.get_recent_decisions(limit=10)
    assert [d.symbol for d in recent] == ["B", "A"]


def test_get_recent_positions_newest_first():
    store = Store(":memory:")
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1, entry_price=100.0)
    store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=1, entry_price=200.0)
    recent = store.get_recent_positions(limit=10)
    assert [p.symbol for p in recent] == ["B", "A"]


def test_realized_pnl_total_sums_closed_only():
    store = Store(":memory:")
    a = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0)
    store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=1, entry_price=100.0)  # open
    store.close_position(a, exit_price=110.0, exit_reason="TARGET", realized_pnl=100.0)
    assert store.realized_pnl_total() == 100.0   # B still open, not counted


def test_realized_pnl_total_zero_when_none_closed():
    store = Store(":memory:")
    assert store.realized_pnl_total() == 0.0


def test_realized_pnl_since_date_boundary():
    store = Store(":memory:")
    a = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1, entry_price=100.0)
    store.close_position(a, exit_price=150.0, exit_reason="TARGET", realized_pnl=50.0)
    # closed_at is an ISO UTC timestamp today; a far-past cutoff includes it, a far-future one excludes it
    assert store.realized_pnl_since("2000-01-01") == 50.0
    assert store.realized_pnl_since("2999-01-01") == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -k "recent or realized" -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'get_recent_runs'`

- [ ] **Step 3: Implement**

Add to `store.py` inside `Store` (reuse the existing `_row_to_position`/`_row_to_decision` mappers; construct `JobRun` inline to match `get_run`):

```python
    def get_recent_runs(self, limit: int = 20) -> list["JobRun"]:
        rows = self._conn.execute(
            "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [JobRun(id=r["id"], started_at=r["started_at"], finished_at=r["finished_at"],
                       status=r["status"], mode=r["mode"], num_candidates=r["num_candidates"],
                       num_actions=r["num_actions"], error=r["error"], summary=r["summary"])
                for r in rows]

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -v`
Expected: PASS (all prior store tests + 6 new).

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "Add store read queries for the dashboard (recent runs/decisions/positions, realized P&L)"
```

---

### Task 2: `dashboard_data.py` — pure view functions

**Files:**
- Create: `dashboard_data.py`
- Test: `tests/test_dashboard_data.py`

**Interfaces:**
- Consumes: `Store` and its methods (`get_config`, `deployed_capital`, `count_open_positions`, `get_recent_positions`, `get_recent_decisions`, `get_recent_runs`, `realized_pnl_total`, `realized_pnl_since`).
- Produces: `header_view(store) -> dict`; `positions_view(store, limit=50) -> list[dict]`; `pnl_summary(store, today_iso) -> dict`; `decisions_view(store, limit=50) -> list[dict]`; `runs_view(store, limit=20) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_data.py`:

```python
from store import Store
from dashboard_data import (header_view, positions_view, pnl_summary, decisions_view,
                            runs_view)


def _seeded():
    store = Store(":memory:")
    store.update_config(mode="paper", total_pool=100000.0, max_open_positions=5,
                        capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=100,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0)  # 10000 deployed
    return store


def test_header_view_math():
    store = _seeded()
    h = header_view(store)
    assert h["mode"] == "paper"
    assert h["is_paused"] is False
    assert h["total_pool"] == 100000.0
    assert h["deployed_capital"] == 10000.0
    assert h["utilization_pct"] == 10.0     # 10000 / 100000
    assert h["open_count"] == 1
    assert h["max_open_positions"] == 5


def test_header_view_zero_pool_no_divzero():
    store = Store(":memory:")   # seeded default total_pool = 0
    h = header_view(store)
    assert h["utilization_pct"] == 0.0      # must not divide by zero


def test_positions_view_shape():
    store = _seeded()
    rows = positions_view(store)
    assert rows[0]["symbol"] == "A"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["target_price"] == 110.0
    assert rows[0]["realized_pnl"] is None


def test_pnl_summary_total_and_today():
    store = _seeded()
    a = store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=10,
                            entry_price=100.0)
    store.close_position(a, exit_price=120.0, exit_reason="TARGET", realized_pnl=200.0)
    s = pnl_summary(store, today_iso="2000-01-01")   # far-past cutoff → today == total
    assert s["realized_total"] == 200.0
    assert s["realized_today"] == 200.0
    assert s["open_count"] == 1                       # A still open


def test_decisions_view_newest_first():
    store = _seeded()
    run_id = store.start_run("paper")
    store.record_decision(run_id=run_id, symbol="X", action="BUY_NOW", score=80,
                          reason="strong")
    store.record_decision(run_id=run_id, symbol="Y", action="WAIT", score=40, reason="no edge")
    rows = decisions_view(store)
    assert rows[0]["symbol"] == "Y" and rows[0]["action"] == "WAIT"
    assert rows[1]["symbol"] == "X" and rows[1]["score"] == 80


def test_runs_view_shape():
    store = _seeded()
    run_id = store.start_run("paper")
    store.finish_run(run_id, "SUCCESS", num_candidates=3, num_actions=1, summary="1 entry")
    rows = runs_view(store)
    assert rows[0]["status"] == "SUCCESS"
    assert rows[0]["num_candidates"] == 3
    assert rows[0]["summary"] == "1 entry"


def test_views_empty_db_do_not_raise():
    store = Store(":memory:")
    assert positions_view(store) == []
    assert decisions_view(store) == []
    assert runs_view(store) == []
    assert pnl_summary(store, "2000-01-01") == {"realized_total": 0.0, "realized_today": 0.0,
                                                "open_count": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard_data'`

- [ ] **Step 3: Implement `dashboard_data.py`**

```python
"""Pure view functions for the dashboard — take a Store, return plain Python. No Streamlit,
no broker, no LLM, so they are fully unit-testable. See
docs/superpowers/specs/2026-07-10-dashboard-design.md."""
from __future__ import annotations


def header_view(store) -> dict:
    cfg = store.get_config()
    deployed = store.deployed_capital()
    util = round(deployed / cfg.total_pool * 100, 1) if cfg.total_pool else 0.0
    return {
        "mode": cfg.mode, "is_paused": cfg.is_paused, "total_pool": cfg.total_pool,
        "deployed_capital": deployed, "utilization_pct": util,
        "open_count": store.count_open_positions(),
        "max_open_positions": cfg.max_open_positions,
        "capital_per_position": cfg.capital_per_position,
    }


def positions_view(store, limit: int = 50) -> list[dict]:
    return [
        {"symbol": p.symbol, "side": p.side, "quantity": p.quantity,
         "entry_price": p.entry_price, "target_price": p.target_price, "stop_loss": p.stop_loss,
         "status": p.status, "exit_price": p.exit_price, "exit_reason": p.exit_reason,
         "realized_pnl": p.realized_pnl, "opened_at": p.opened_at, "closed_at": p.closed_at}
        for p in store.get_recent_positions(limit)
    ]


def pnl_summary(store, today_iso: str) -> dict:
    return {
        "realized_total": store.realized_pnl_total(),
        "realized_today": store.realized_pnl_since(today_iso),
        "open_count": store.count_open_positions(),
    }


def decisions_view(store, limit: int = 50) -> list[dict]:
    return [
        {"symbol": d.symbol, "action": d.action, "score": d.score, "reason": d.reason,
         "entry_price": d.entry_price, "target_price": d.target_price,
         "stop_loss": d.stop_loss, "created_at": d.created_at}
        for d in store.get_recent_decisions(limit)
    ]


def runs_view(store, limit: int = 20) -> list[dict]:
    return [
        {"id": r.id, "started_at": r.started_at, "finished_at": r.finished_at,
         "status": r.status, "mode": r.mode, "num_candidates": r.num_candidates,
         "num_actions": r.num_actions, "summary": r.summary, "error": r.error}
        for r in store.get_recent_runs(limit)
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_data.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard_data.py tests/test_dashboard_data.py
git commit -m "Add dashboard_data pure view functions"
```

---

### Task 3: `dashboard.py` Streamlit app + config controls + README

**Files:**
- Create: `dashboard.py`
- Modify: `requirements.txt`
- Modify: `README.md`

**Interfaces:**
- Consumes: `dashboard_data.*`, `store.Store`, `store.update_config`.
- Produces: the runnable Streamlit app (no new API for other phases).

- [ ] **Step 1: Add `streamlit` to `requirements.txt`**

Append `streamlit` on its own line, then install:

```bash
.venv/bin/pip install streamlit
```

- [ ] **Step 2: Write `dashboard.py`**

```python
"""autoIntraday dashboard — Streamlit UI over the SQLite store. Read-only except config
(pause/resume, capital rules, paper/live). No broker/LLM calls. Thin render layer over
dashboard_data view functions. Run: streamlit run dashboard.py

See docs/superpowers/specs/2026-07-10-dashboard-design.md."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from dashboard_data import (decisions_view, header_view, pnl_summary, positions_view,
                            runs_view)
from store import Store

DB_PATH = os.environ.get(
    "AUTOINTRADAY_DB", os.path.expanduser("~/.autointraday/autointraday.db"))


def _store() -> Store:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return Store(DB_PATH)


def main() -> None:
    st.set_page_config(page_title="autoIntraday", layout="wide")
    st.title("autoIntraday — status & controls")
    store = _store()

    h = header_view(store)
    mode_label = "🔴 LIVE" if h["mode"] == "live" else "🟢 PAPER"
    paused_label = "⏸ PAUSED" if h["is_paused"] else "▶ ACTIVE"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", mode_label)
    c2.metric("Status", paused_label)
    c3.metric("Pool used", f"₹{h['deployed_capital']:,.0f} / ₹{h['total_pool']:,.0f}",
              f"{h['utilization_pct']}%")
    c4.metric("Open positions", f"{h['open_count']} / {h['max_open_positions']}")

    with st.sidebar:
        st.header("Controls")
        # Pause / resume
        paused = st.toggle("Paused (kill switch)", value=h["is_paused"])
        if paused != h["is_paused"]:
            store.update_config(is_paused=paused)
            st.rerun()

        # Capital rules
        st.subheader("Capital rules")
        total_pool = st.number_input("Total pool (₹)", min_value=0.0, value=float(h["total_pool"]),
                                     step=1000.0)
        max_pos = st.number_input("Max open positions", min_value=0,
                                  value=int(h["max_open_positions"]), step=1)
        cap_pos = st.number_input("Capital per position (₹)", min_value=0.0,
                                  value=float(h["capital_per_position"]), step=1000.0)
        if st.button("Save capital rules"):
            store.update_config(total_pool=total_pool, max_open_positions=int(max_pos),
                                capital_per_position=cap_pos)
            st.success("Saved.")
            st.rerun()

        # Mode toggle — live requires explicit confirmation
        st.subheader("Mode")
        if h["mode"] == "paper":
            confirm = st.checkbox("I understand LIVE places REAL orders")
            if st.button("Switch to LIVE", disabled=not confirm):
                store.update_config(mode="live")
                st.rerun()
        else:
            if st.button("Switch back to PAPER"):
                store.update_config(mode="paper")
                st.rerun()

    # P&L
    today_iso = datetime.now(timezone.utc).date().isoformat()
    pnl = pnl_summary(store, today_iso)
    p1, p2 = st.columns(2)
    p1.metric("Realized P&L (total)", f"₹{pnl['realized_total']:,.2f}")
    p2.metric("Realized P&L (today)", f"₹{pnl['realized_today']:,.2f}")

    # Open positions
    st.subheader("Open positions")
    open_rows = [r for r in positions_view(store) if r["status"] == "OPEN"]
    st.dataframe(open_rows, use_container_width=True) if open_rows else st.caption("none")

    # Decisions
    st.subheader("Recent decisions")
    st.dataframe(decisions_view(store), use_container_width=True)

    # Runs
    st.subheader("Job runs")
    st.dataframe(runs_view(store), use_container_width=True)

    if st.button("Refresh"):
        st.rerun()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Confirm the app parses/imports and the full suite passes**

Run: `.venv/bin/python -c "import ast; ast.parse(open('dashboard.py').read()); print('dashboard parses OK')"`
Run: `.venv/bin/python -c "import dashboard_data; print('data layer imports OK')"`
Run: `.venv/bin/python -m pytest -q`
Expected: parse OK, import OK, full suite green.

- [ ] **Step 4: Add a Phase 6 section to `README.md`**

Append to `README.md`:

```markdown
## Phase 6: Dashboard

`dashboard.py` is a Streamlit UI over the SQLite store — see open positions, decisions, job
runs, and realized P&L, and control pause/resume, the capital rules, and paper/live mode.
Read-only except config; no broker/LLM calls. `dashboard_data.py` holds the pure view
functions. See `docs/superpowers/specs/2026-07-10-dashboard-design.md`.

### Run

\`\`\`bash
.venv/bin/pip install -r requirements.txt      # installs streamlit
AUTOINTRADAY_DB=~/.autointraday/autointraday.db .venv/bin/streamlit run dashboard.py
\`\`\`

Point `AUTOINTRADAY_DB` at the same DB the scheduler writes (default is that path). Switching
to LIVE mode requires ticking the confirmation box first. Open-position rows show the plan
(entry/target/stop); realized P&L appears on closed positions.

### Test

\`\`\`bash
.venv/bin/python -m pytest tests/test_dashboard_data.py -v
\`\`\`
```

- [ ] **Step 5: Commit**

```bash
git add dashboard.py requirements.txt README.md
git commit -m "Add Streamlit dashboard with config controls and Phase 6 README"
```

---

## Self-Review Notes

- **Spec coverage:** store cross-run read queries (Task 1) · pure view functions incl. header utilization math, positions/decisions/runs shapes, P&L totals, empty-DB safety (Task 2) · Streamlit app with header, pause/resume + capital-rule + live-confirmation controls, positions/decisions/runs tables, P&L (Task 3) · README run/test docs (Task 3). All spec sections map to a task.
- **Type consistency:** the view functions use the exact `Config`/`Position`/`Decision`/`JobRun` field names from Phase 2; the new store methods reuse `_row_to_position`/`_row_to_decision` and match `get_run`'s `JobRun` construction; `dashboard.py` calls only `dashboard_data.*` + `store.update_config` with whitelisted keys (`is_paused`/`total_pool`/`max_open_positions`/`capital_per_position`/`mode`).
- **No placeholders:** every step has complete runnable code. `dashboard.py` is manual-verified (Streamlit render layer) — its logic lives in the unit-tested `dashboard_data.py`; the plan verifies it parses/imports rather than unit-testing Streamlit. Expected test counts: +6 store, +8 dashboard_data.
- **Safety:** the UI's only writes go through `update_config` (whitelist-guarded in Phase 2); no broker/LLM calls; live-mode is gated behind a confirmation checkbox; `header_view` guards against divide-by-zero on a zero pool.
