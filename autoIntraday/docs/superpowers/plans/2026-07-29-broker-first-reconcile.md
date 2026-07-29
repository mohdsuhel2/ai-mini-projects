# Broker-First Reconcile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Groww the source of truth for both positions and orders on every live cycle, so the bot adopts, repairs and manages whatever the user did by hand between cycles.

**Architecture:** `_reconcile_broker` is split from a monolithic read-and-record into four units — `_broker_state` (read once, MIS separated from CNC), `_sync_known` (one DB position → one of five outcomes), `_adopt` (unknown broker position → new book entry), `_takeover_foreign_orders` (the user's own exit orders are cancelled and re-placed at the bot's analysed level). A new `_ensure_protective_stop` guarantees no open position ever sits without a stop.

**Tech Stack:** Python 3, SQLite (`store.py`), pytest, Streamlit (`dashboard.py`), Groww SDK via `groww_client.py` / the FastAPI gateway.

**Spec:** `docs/superpowers/specs/2026-07-29-broker-first-reconcile-design.md`

## Global Constraints

- **Live only.** `_reconcile_broker` returns immediately unless `self.client.mode == "live"`. Paper behaviour must not change.
- **CNC is sacred.** CNC/delivery positions are never adopted and CNC orders are never cancelled. Flattening the long-term portfolio at square-off would be catastrophic.
- **Never invent a fill price.** A manually-sold slice's P&L is not reconstructed; exits booked by reconcile use LTP, falling back to `entry_price` when indicators fail (a zero-move book, not a fictional one).
- **Reconcile must never abort a cycle.** Every per-symbol and per-order operation is individually caught and logged. Broker cancel failures increment `self._cycle_errors`, which surfaces as the existing macOS notification from `run_cycle_job.py`.
- **Ratchet rule is inviolable.** A stop only ever moves toward profit. Nothing in this plan may widen an existing stop.
- **Run the full suite before every commit:** `.venv/bin/python -m pytest tests/ -q`

---

### Task 1: Store — position size and force-bracket persistence

**Files:**
- Modify: `store.py` (positions DDL ~line 220-250; positions migration block ~line 365-372; `Position` dataclass ~line 105-150; `_row_to_position` ~line 515-535; new methods near `update_position_quantity` ~line 667)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `Store.update_position_size(position_id: int, quantity: int, entry_price: float) -> None`
  - `Store.set_force_bracket(position_id: int) -> None`
  - `Position.force_bracket: bool` (defaults `False`)

`ScopedStore` delegates unknown attributes through `__getattr__` (`store.py:1169-1176`), so neither new method needs registering anywhere.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store.py`:

```python
def test_update_position_size_syncs_qty_and_blended_entry():
    store = Store(":memory:")
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.update_position_size(pid, 25, 102.4)
    p = store.get_position(pid)
    assert p.quantity == 25
    assert p.entry_price == pytest.approx(102.4)
    assert p.stop_loss == 95.0          # levels untouched — the ratchet rule owns them


def test_update_position_size_rejects_closed_position():
    store = Store(":memory:")
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, mode="live")
    store.close_position(pid, exit_price=101.0, exit_reason="TARGET", realized_pnl=10.0)
    with pytest.raises(StoreError):
        store.update_position_size(pid, 25, 102.4)


def test_force_bracket_defaults_false_and_is_settable():
    store = Store(":memory:")
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, mode="live")
    assert store.get_position(pid).force_bracket is False
    store.set_force_bracket(pid)
    assert store.get_position(pid).force_bracket is True
```

Check the existing imports at the top of `tests/test_store.py` — add `StoreError` to the `from store import ...` line if it is not already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -q -k "position_size or force_bracket"`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'update_position_size'`

- [ ] **Step 3: Add the `force_bracket` column**

In `_SCHEMA`, in the `positions` table, add the column immediately after `broker_target_price REAL,`:

```sql
    force_bracket INTEGER NOT NULL DEFAULT 0,
```

In the positions-column migration block (the run of `if "..." not in cols:` statements that ends with `broker_target_price` at `store.py:371-372`), append:

```python
        if "force_bracket" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN force_bracket INTEGER "
                               "NOT NULL DEFAULT 0")
```

- [ ] **Step 4: Add the dataclass field and row mapping**

In `Position` (`store.py:105`), after the broker-bracket fields, add:

```python
    # Pinned to eager bracket management regardless of the global exit_mode. Set when reconcile
    # cancels a user's own resting exit order: the bot must REPLACE that protection with its own
    # broker bracket, never merely remove it.
    force_bracket: bool = False
```

In `_row_to_position` (`store.py:516`), add to the constructor call:

```python
            force_bracket=bool(r["force_bracket"]),
```

- [ ] **Step 5: Add the two store methods**

Insert after `update_position_quantity` (`store.py:667-672`):

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -q`
Expected: PASS, all of them (the migration is additive, so pre-existing store tests are unaffected).

- [ ] **Step 7: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "store: add update_position_size and force_bracket for broker reconcile"
```

---

### Task 2: Store — `adopt_fallback_stop_pct` config knob

**Files:**
- Modify: `store.py` (config DDL ~line 186-206; `Config` dataclass ~line 40-78; `_CONFIG_FIELDS` ~line 82-87; `get_config` ~line 449-471; config migration block ~line 382-400)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config.adopt_fallback_stop_pct: float` (default `1.0`), settable via `store.update_config(adopt_fallback_stop_pct=...)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_store.py`:

```python
def test_adopt_fallback_stop_pct_defaults_and_updates():
    store = Store(":memory:")
    assert store.get_config().adopt_fallback_stop_pct == pytest.approx(1.0)
    cfg = store.update_config(adopt_fallback_stop_pct=0.75)
    assert cfg.adopt_fallback_stop_pct == pytest.approx(0.75)
    assert store.get_config().adopt_fallback_stop_pct == pytest.approx(0.75)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -q -k adopt_fallback`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'adopt_fallback_stop_pct'`

- [ ] **Step 3: Add the field, column, migration and mapping**

In `_SCHEMA`, in the `config` table, after `exit_mode TEXT NOT NULL DEFAULT 'db_only'` add a comma and:

```sql
    adopt_fallback_stop_pct REAL NOT NULL DEFAULT 1.0
```

In `Config` (`store.py:40`), after `arm_exit_band_pct`:

```python
    # Last-resort stop for any OPEN position the engine has not given one — an adopted manual
    # position, or a read that returned WAIT. Percent from entry. 0 disables the floor. Replaced
    # by the engine's structural stop as soon as it arrives, and never widened (ratchet rule).
    adopt_fallback_stop_pct: float = 1.0
```

In `_CONFIG_FIELDS` (`store.py:82`), add `"adopt_fallback_stop_pct"` to the tuple.

In the config-column migration block (the run of `if "..." not in ccols:` statements starting at `store.py:382`), append:

```python
        if "adopt_fallback_stop_pct" not in ccols:
            self._conn.execute("ALTER TABLE config ADD COLUMN adopt_fallback_stop_pct REAL "
                               "NOT NULL DEFAULT 1.0")
```

In `get_config` (`store.py:449`), add to the `Config(...)` call:

```python
                      adopt_fallback_stop_pct=r["adopt_fallback_stop_pct"],
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py tests/test_dashboard_data.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "store: add adopt_fallback_stop_pct config knob"
```

---

### Task 3: Groww client — expose `product` on the order book

**Files:**
- Modify: `groww_client.py:305-322`
- Test: `tests/test_groww_client.py:437-458`

**Interfaces:**
- Consumes: nothing.
- Produces: every dict from `GrowwClient.get_open_orders()` gains `"product"` — the raw broker value, or `None` when the broker omits it. Reconcile treats anything other than the exact string `"MIS"` as untouchable, so a missing value fails safe (no cancellation).

The gateway wraps the same `GrowwClient` (`groww_gateway/app.py:42-46, 146-147`), so this one change covers both the direct-SDK and gateway transports.

- [ ] **Step 1: Update the existing test to expect the new field**

`tests/test_groww_client.py::test_get_open_orders_live_maps_fields` asserts an exact dict list, so it must be updated in the same change. Replace its body's SDK stub and assertion with:

```python
def test_get_open_orders_live_maps_fields():
    class _Sdk(_FakeSdk):
        def get_order_list(self, segment=None, page=None, page_size=None, timeout=None):
            return {"order_list": [
                {"trading_symbol": "AAA", "groww_order_id": "G1", "order_status": "APPROVED",
                 "transaction_type": "BUY", "product": "MIS"},
                {"trading_symbol": "BBB", "groww_order_id": "G2", "order_status": "EXECUTED",
                 "transaction_type": "SELL", "product": "CNC"},
                # product omitted entirely -> None, so reconcile never treats it as MIS
                {"trading_symbol": "CCC", "groww_order_id": "G3", "order_status": "APPROVED",
                 "transaction_type": "SELL"},
            ]}

    import os
    os.environ["GROWW_API_KEY"] = "key123"
    os.environ["GROWW_TOTP_SECRET"] = pyotp.random_base32()
    client = GrowwClient(mode="live", sdk_factory=lambda k, t: _Sdk())
    client.authenticate()
    orders = client.get_open_orders()
    assert orders == [
        {"symbol": "AAA", "order_id": "G1", "status": "APPROVED",
         "transaction_type": "BUY", "product": "MIS"},
        {"symbol": "BBB", "order_id": "G2", "status": "EXECUTED",
         "transaction_type": "SELL", "product": "CNC"},
        {"symbol": "CCC", "order_id": "G3", "status": "APPROVED",
         "transaction_type": "SELL", "product": None},
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -q -k open_orders_live`
Expected: FAIL — the returned dicts have no `product` key

- [ ] **Step 3: Add the field**

In `groww_client.py:316-322`, extend the comprehension:

```python
        return [
            {"symbol": o.get("trading_symbol") or o.get("symbol"),
             "order_id": o.get("groww_order_id") or o.get("order_id"),
             "status": o.get("order_status") or o.get("status"),
             "transaction_type": o.get("transaction_type"),
             # MIS vs CNC. Reconcile only ever cancels MIS orders — a resting CNC sell belongs
             # to the user's delivery portfolio. Absent -> None, which fails safe (not MIS).
             "product": o.get("product") or None}
            for o in (orders or [])
        ]
```

Update the docstring's first line to mention that the product is included.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add groww_client.py tests/test_groww_client.py
git commit -m "groww: expose product on the normalized order book"
```

---

### Task 4: Orchestrator — `_broker_state`, separating MIS from CNC

**Files:**
- Modify: `orchestrator.py:400-492` (`_reconcile_broker`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `GrowwClient.get_open_orders()` including `product` (Task 3).
- Produces:
  - `Orchestrator._broker_state() -> tuple[dict, list | None]` — `(mis, orders)`. `mis` maps `symbol -> {"net": int, "avg": float | None}` built from **MIS rows only**. `orders` is the non-terminal broker order book, or `None` when the order-book read failed (which must disable takeover rather than read as "the user has no orders").

This task only restructures the read and fixes the CNC-masking bug; the decision table stays as-is and lands in Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py`, next to the existing reconcile tests:

```python
def test_reconcile_cnc_holding_does_not_mask_closed_mis_position():
    # A delivery holding in the SAME symbol must not keep a fully-exited MIS position alive:
    # net qty for the sync decision is MIS-only.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 50, "product": "CNC", "avg_price": 90.0}])
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "CLOSED" and p.exit_reason == "BROKER_SYNC"


def test_broker_state_splits_mis_and_reports_orders():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "AAA", "quantity": 5, "product": "MIS", "avg_price": 101.0},
        {"symbol": "AAA", "quantity": 40, "product": "CNC", "avg_price": 90.0},
        {"symbol": "BBB", "quantity": -3, "product": "MIS", "avg_price": 55.0}])
    client.open_orders = [
        {"symbol": "AAA", "order_id": "G1", "status": "APPROVED",
         "transaction_type": "SELL", "product": "MIS"},
        {"symbol": "ZZZ", "order_id": "G2", "status": "EXECUTED",
         "transaction_type": "BUY", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    mis, orders = orch._broker_state()
    assert mis == {"AAA": {"net": 5, "avg": 101.0}, "BBB": {"net": -3, "avg": 55.0}}
    assert [o["order_id"] for o in orders] == ["G1"]      # EXECUTED is terminal, filtered out


def test_broker_state_returns_none_orders_when_order_book_fails():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live")
    def boom():
        raise RuntimeError("gateway 502")
    client.get_open_orders = boom
    orch = _live_screen_orch(store, client)
    mis, orders = orch._broker_state()
    assert mis == {}
    assert orders is None          # unavailable != empty; takeover must stay disabled


def test_paper_mode_reconciles_nothing():
    # Paper has no broker to reconcile against — the DB is the only ledger. A broker payload
    # must be ignored entirely, never adopted.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="paper", broker_positions=[
        {"symbol": "MANUAL", "quantity": 5, "product": "MIS", "avg_price": 101.5}])
    orch = _live_screen_orch(store, client)
    run_id = store.start_run("paper")
    assert orch._reconcile_broker(run_id) == 0
    assert store.get_open_positions() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q -k "broker_state or cnc_holding"`
Expected: FAIL — `AttributeError: 'Orchestrator' object has no attribute '_broker_state'`; the CNC test fails because the position is still OPEN.

- [ ] **Step 3: Add `_broker_state`**

Insert immediately before `_reconcile_broker` in `orchestrator.py`:

```python
    def _broker_state(self):
        """Read the broker ONCE per cycle and normalize it. Returns (mis, orders):
        - mis: symbol -> {"net": signed_qty, "avg": avg_price|None}, built from MIS rows ONLY.
          CNC/delivery is excluded on purpose — summing it in let a delivery holding mask a
          fully-closed MIS position, keeping a dead trade alive in the book.
        - orders: the non-terminal broker order book, or None if that read FAILED. None and []
          must stay distinguishable: an empty list means the user has no resting orders, while
          None means we don't know — and we must never cancel or exclude on a guess.
        A positions failure propagates; the caller skips the whole reconcile."""
        mis: dict[str, dict] = {}
        for p in self.client.get_positions():
            if p.get("product", "MIS") != "MIS":
                continue
            row = mis.setdefault(p["symbol"], {"net": 0, "avg": None})
            row["net"] += int(p["quantity"])
            if p.get("avg_price"):
                row["avg"] = float(p["avg_price"])
        try:
            terminal = set(_REJECTED_STATES) | set(_FILLED_STATES)
            orders = [o for o in self.client.get_open_orders()
                      if o.get("symbol") and str(o.get("status", "")).upper() not in terminal]
        except Exception:
            log.exception("broker reconcile: get_open_orders failed — no takeover, no exclusions")
            return mis, None
        return mis, orders
```

- [ ] **Step 4: Rewire `_reconcile_broker` to use it**

Replace everything in `_reconcile_broker` from `self._external_order_symbols = set()` down to (and including) the `for p in broker_positions:` loop that builds `qty_by_symbol` / `mis_net` / `mis_avg`, with:

```python
        self._external_order_symbols = set()
        if self.client.mode != "live":
            return 0
        try:
            mis, orders = self._broker_state()
        except Exception:
            log.exception("broker reconcile: get_positions failed — skipping reconcile")
            return 0
        self._external_order_symbols = {o["symbol"] for o in orders} if orders else set()
        synced = 0
```

Then in the existing DB-position loop, replace `net = qty_by_symbol.get(position.symbol, 0)` with:

```python
            net = int((mis.get(position.symbol) or {}).get("net", 0))
```

And in the adoption loop, replace `for symbol, net in mis_net.items():` and its body's `entry = mis_avg.get(symbol)` with:

```python
        for symbol, row in mis.items():
            net = int(row["net"])
            if net == 0 or symbol in known:
                continue
            try:
                side = "LONG" if net > 0 else "SHORT"
                entry = row["avg"]
```

Update the method docstring's last paragraph to say the order-book snapshot now comes from `_broker_state`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: PASS — including the pre-existing `test_reconcile_adopts_manual_short_and_skips_cnc` and `test_reconcile_excludes_manual_open_order_symbols_from_entries`.

- [ ] **Step 6: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "reconcile: extract _broker_state, stop CNC masking MIS exits"
```

---

### Task 5: Orchestrator — cancel stale orders on every manual exit

**Files:**
- Modify: `orchestrator.py` (`_close_position` ~line 552-573; `_reconcile_broker`'s `net == 0` branch)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `_cancel_bracket` (`orchestrator.py:544`).
- Produces:
  - `Orchestrator._cancel_stale_orders(position) -> None` — cancels both bracket legs and the OCO. Never raises.
  - `Orchestrator._close_broker_synced(run_id, position, reason: str) -> None` — books a position the user closed by hand, at LTP, reason `BROKER_SYNC`.

- [ ] **Step 1: Write the failing test**

```python
def test_reconcile_manual_exit_cancels_bracket_and_oco():
    # The bot's resting stop must NOT survive the user's manual exit — it would fire against
    # shares we no longer hold and open a naked reverse position.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              oco_order_id="OCO-1", mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 110.0)
    client = _FakeClient(mode="live", broker_positions=[])     # flat at the broker
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert set(client.cancelled) == {"SL-1", "TG-1"}
    assert client.cancelled_ocos == ["OCO-1"]
    p = store.get_position(pid)
    assert p.status == "CLOSED" and p.exit_reason == "BROKER_SYNC"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q -k manual_exit_cancels`
Expected: FAIL — `client.cancelled` is empty; the reconcile close path never cancels anything.

- [ ] **Step 3: Add `_cancel_stale_orders` and reuse it in `_close_position`**

Insert after `_cancel_bracket` (`orchestrator.py:544-550`):

```python
    def _cancel_stale_orders(self, position) -> None:
        """Cancel EVERY broker order this position owns — both bracket legs and the OCO —
        because its size or its existence just changed at the broker. A leg left resting after
        a manual exit fires against shares we no longer hold and opens a naked reverse
        position. Never raises; a failed cancel is counted and logged loudly."""
        self._cancel_bracket(position)
        if position.oco_order_id:
            try:
                self.client.cancel_oco_order(position.oco_order_id)
            except Exception:
                self._cycle_errors += 1
                log.exception("OCO cancel failed for %s (%s) — verify at broker!",
                              position.symbol, position.oco_order_id)
```

Then replace the head of `_close_position` (`orchestrator.py:552-562`) — the `self._cancel_bracket(position)` call, the comment, and the `if position.oco_order_id:` try/except block — with:

```python
    def _close_position(self, position, exit_price: float, reason: str) -> None:
        # Disarm every protective order FIRST: exiting at market while a bracket leg or the OCO
        # stays armed at the broker means a leg can fire after we're flat and leave a naked
        # reverse position.
        self._cancel_stale_orders(position)
```

Leave the rest of `_close_position` (the market order, `record_order`, `close_position`) unchanged.

- [ ] **Step 4: Add `_close_broker_synced` and use it in the `net == 0` branch**

Insert after `_cancel_stale_orders`:

```python
    def _close_broker_synced(self, run_id: int, position, reason: str) -> None:
        """Book a position the user closed (or reversed) by hand. Stale broker orders are
        cancelled FIRST, then the position is booked at LTP — the manual fill price is unknown
        and never invented; if indicators fail we book at entry so no fictional move lands in
        the P&L."""
        self._cancel_stale_orders(position)
        try:
            exit_price = _ltp(self.get_indicators(position.symbol))
        except Exception:
            exit_price = position.entry_price
        pnl = self._realized_pnl(position.side, position.entry_price, exit_price,
                                 position.quantity)
        self.store.close_position(position.id, exit_price=exit_price,
                                  exit_reason="BROKER_SYNC", realized_pnl=pnl)
        self.store.record_decision(run_id=run_id, symbol=position.symbol, action="EXIT",
                                   reason=reason, position_id=position.id)
        log.warning("reconciled %s: closed in DB (%s), exit~%.2f",
                    position.symbol, reason, exit_price)
```

In `_reconcile_broker`, replace the whole `if net == 0:` branch body with:

```python
                if net == 0:
                    self._close_broker_synced(
                        run_id, position,
                        "broker sync: no net qty at broker (manual exit / OCO fired?)")
                    synced += 1
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "reconcile: cancel stale bracket and OCO orders on a manual exit"
```

---

### Task 6: Orchestrator — the full drift decision table

**Files:**
- Modify: `orchestrator.py` (`_reconcile_broker` — replace the DB-position loop body and the adoption loop)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `_broker_state` (Task 4), `_cancel_stale_orders` / `_close_broker_synced` (Task 5), `Store.update_position_size` (Task 1).
- Produces:
  - `Orchestrator._sync_known(run_id: int, position, mis: dict) -> int` — repairs one DB-open position; returns `1` if it changed anything, else `0`.
  - `Orchestrator._adopt(run_id: int, symbol: str, net: int, avg: float | None) -> int` — returns the new position id.

The five outcomes: `net == 0` → close; opposite sign → close + adopt; equal size → no-op; smaller → shrink; larger → absorb.

- [ ] **Step 1: Write the failing tests**

```python
def test_reconcile_absorbs_manual_add_at_blended_entry():
    # Bot holds 10, user manually buys 15 more -> the bot manages all 25 at the broker's
    # blended average, and the existing stop is NOT loosened.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 25, "product": "MIS", "avg_price": 102.0}])
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.quantity == 25
    assert p.entry_price == pytest.approx(102.0)     # true blended cost basis
    assert p.stop_loss == 95.0                       # never loosened
    assert "SL-1" in client.cancelled                # stale-size leg torn down


def test_reconcile_partial_exit_cancels_stale_size_legs():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 110.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 4, "product": "MIS", "avg_price": 100.0}])
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert store.get_position(pid).quantity == 4
    assert set(client.cancelled) >= {"SL-1", "TG-1"}   # legs for the OLD size are gone


def test_reconcile_side_flip_closes_old_and_adopts_new():
    # Bot LONG 10; user sells 20 -> broker is SHORT 10. Old trade booked, new side adopted.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": -10, "product": "MIS", "avg_price": 104.0}])
    orch = _live_screen_orch(store, client)
    summary = orch.run_cycle()
    old = store.get_position(pid)
    assert old.status == "CLOSED" and old.exit_reason == "BROKER_SYNC"
    fresh = store.get_open_positions()
    assert len(fresh) == 1
    assert (fresh[0].symbol, fresh[0].side, fresh[0].quantity) == ("BOT", "SHORT", 10)
    assert fresh[0].entry_price == pytest.approx(104.0)
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any(r.symbol == "BOT" and r.action == "ADOPTED" for r in recs)


def test_reconcile_matching_size_is_a_noop():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "OPEN" and p.quantity == 10
    assert "SL-1" not in client.cancelled        # nothing drifted -> nothing torn down
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q -k "absorbs_manual_add or stale_size_legs or side_flip or matching_size"`
Expected: FAIL — the add is ignored (qty stays 10), the flip leaves the LONG open, and the partial exit cancels nothing.

- [ ] **Step 3: Add `_adopt`**

Insert after `_close_broker_synced`:

```python
    def _adopt(self, run_id: int, symbol: str, net: int, avg: float | None) -> int:
        """Take an unknown broker MIS position into the book and return its id. Levels are left
        None ON PURPOSE: _manage_positions runs later in THIS same cycle, analyses the symbol
        with the full strategy skill and writes the real structural stop/target, with
        _ensure_protective_stop as the floor if that read returns none."""
        side = "LONG" if net > 0 else "SHORT"
        entry = avg or _ltp(self.get_indicators(symbol))
        pid = self.store.open_position(
            symbol=symbol, exchange="NSE", side=side, quantity=abs(net),
            entry_price=entry, target_price=None, stop_loss=None, mode="live")
        self.store.record_decision(
            run_id=run_id, symbol=symbol, action="ADOPTED",
            reason=f"manual {side} x{abs(net)} @ ~{entry} found at broker — "
                   f"adopted; bot manages it from this cycle", position_id=pid)
        log.warning("adopted manual %s position: %s x%d @ ~%.2f",
                    side, symbol, abs(net), entry)
        return pid
```

- [ ] **Step 4: Add `_sync_known`**

Insert after `_adopt`:

```python
    def _sync_known(self, run_id: int, position, mis: dict) -> int:
        """Repair ONE DB-open position against broker MIS reality. Exactly one outcome:
          net 0            -> the user flattened it: cancel our orders, book it
          opposite sign    -> the user reversed it: book the old trade, adopt the new side fresh
          same size        -> nothing to do
          smaller          -> manual partial exit: shrink (that slice's P&L is not booked)
          larger           -> manual add: absorb at the broker's blended average
        Any size change tears the bracket down — its legs rest at the OLD quantity and would
        over- or under-sell. _manage_one rebuilds them later this cycle at the corrected size.
        Returns 1 if anything changed, else 0."""
        net = int((mis.get(position.symbol) or {}).get("net", 0))
        avg = (mis.get(position.symbol) or {}).get("avg")
        if net == 0:
            self._close_broker_synced(
                run_id, position,
                "broker sync: no net qty at broker (manual exit / OCO fired?)")
            return 1
        if (net > 0) != (position.side == "LONG"):
            flipped = "LONG" if net > 0 else "SHORT"
            self._close_broker_synced(
                run_id, position,
                f"broker sync: side flipped to {flipped} x{abs(net)} (manual reversal)")
            self._adopt(run_id, position.symbol, net, avg)
            return 1
        if abs(net) == position.quantity:
            return 0
        self._cancel_stale_orders(position)
        if abs(net) < position.quantity:
            self.store.update_position_quantity(position.id, abs(net))
            reason = (f"broker sync: qty {position.quantity} -> {abs(net)} "
                      f"(manual partial exit)")
        else:
            entry = avg or position.entry_price
            self.store.update_position_size(position.id, abs(net), entry)
            reason = (f"broker sync: qty {position.quantity} -> {abs(net)} @ blended {entry} "
                      f"(manual add)")
        self.store.record_decision(run_id=run_id, symbol=position.symbol, action="ADJUSTED",
                                   reason=reason, position_id=position.id)
        log.warning("reconciled %s: %s", position.symbol, reason)
        return 1
```

- [ ] **Step 5: Collapse `_reconcile_broker` onto the new units**

Replace the whole body of `_reconcile_broker` after the `self._external_order_symbols = ...` line with:

```python
        synced = 0
        for position in self.store.get_open_positions():
            try:
                synced += self._sync_known(run_id, position, mis)
            except Exception:
                log.exception("broker reconcile failed for %s", position.symbol)
        known = ({p.symbol for p in self.store.get_open_positions()}
                 | {p.symbol for p in self.store.get_pending_positions()})
        for symbol, row in mis.items():
            net = int(row["net"])
            if net == 0 or symbol in known:
                continue
            try:
                self._adopt(run_id, symbol, net, row["avg"])
                synced += 1
            except Exception:
                log.exception("broker reconcile: adopting %s failed", symbol)
        return synced
```

Note the `known` set is rebuilt **after** `_sync_known` has run, so a symbol adopted by the side-flip branch is not adopted a second time by the loop below.

Update the numbered list in the `_reconcile_broker` docstring to describe all five outcomes rather than the old three.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "reconcile: handle manual adds and side flips, tear down stale-size brackets"
```

---

### Task 7: Orchestrator — take over the user's own exit orders

**Files:**
- Modify: `orchestrator.py` (`_reconcile_broker` tail; `_manage_one` ~line 617; `_ensure_bracket` ~line 878; module constants near line 146)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `orders` from `_broker_state` (Task 4), `Store.set_force_bracket` and `Position.force_bracket` (Task 1), `product` on order dicts (Task 3).
- Produces: `Orchestrator._takeover_foreign_orders(run_id: int, orders: list) -> int` — number of orders taken over.

- [ ] **Step 1: Write the failing tests**

```python
def _held_long(store, symbol="BOT", qty=10):
    return store.open_position(symbol=symbol, exchange="NSE", side="LONG", quantity=qty,
                               entry_price=100.0, target_price=110.0, stop_loss=95.0,
                               mode="live")


def test_takeover_cancels_manual_exit_order_and_forces_bracket():
    # The user's own SL on a symbol the bot manages is cancelled; the bot then rests its OWN
    # bracket at the analysed level even though exit_mode is db_only.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    pid = _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [{"symbol": "BOT", "order_id": "USER-SL", "status": "APPROVED",
                           "transaction_type": "SELL", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    summary = orch.run_cycle()
    assert "USER-SL" in client.cancelled
    p = store.get_position(pid)
    assert p.force_bracket is True
    assert p.broker_stop_order_id is not None      # replaced, not merely removed
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any(r.action == "ADJUSTED" and "USER-SL" in (r.reason or "") for r in recs)


def test_takeover_never_cancels_cnc_orders():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    pid = _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [
        {"symbol": "BOT", "order_id": "CNC-SELL", "status": "APPROVED",
         "transaction_type": "SELL", "product": "CNC"},
        {"symbol": "BOT", "order_id": "UNKNOWN-SELL", "status": "APPROVED",
         "transaction_type": "SELL", "product": None}]
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert client.cancelled == []                   # delivery orders are untouchable
    assert store.get_position(pid).force_bracket is False


def test_takeover_leaves_entry_side_orders_resting():
    # A manual BUY on a LONG is a pending ADD, not an exit — absorbed next cycle if it fills.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [{"symbol": "BOT", "order_id": "USER-BUY", "status": "APPROVED",
                           "transaction_type": "BUY", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert client.cancelled == []


def test_takeover_ignores_the_bots_own_bracket_legs():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="on_fill")
    pid = _held_long(store)
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [{"symbol": "BOT", "order_id": "SL-1", "status": "APPROVED",
                           "transaction_type": "SELL", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert "SL-1" not in client.cancelled           # our own leg is not "foreign"


def test_takeover_disabled_when_order_book_read_fails():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    pid = _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    def boom():
        raise RuntimeError("gateway 502")
    client.get_open_orders = boom
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert client.cancelled == []                   # unknown order book -> cancel nothing
    assert store.get_position(pid).force_bracket is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q -k takeover`
Expected: FAIL — `USER-SL` is never cancelled and `force_bracket` stays `False`.

- [ ] **Step 3: Add the exit-side constant**

Next to `_REJECTED_STATES` / `_FILLED_STATES` (`orchestrator.py:146-149`), add:

```python
# The broker transaction that CLOSES a position of each side — used to tell a user's own resting
# exit order apart from a pending manual add.
_EXIT_TXN = {"LONG": "SELL", "SHORT": "BUY"}
```

- [ ] **Step 4: Add `_takeover_foreign_orders`**

Insert after `_sync_known`:

```python
    def _takeover_foreign_orders(self, run_id: int, orders: list) -> int:
        """Cancel the user's OWN resting exit orders on symbols the bot manages, so the bot's
        analysed stop/target is the only protection resting against those shares (their explicit
        choice: update the SL per the analysis rather than defer to the manual one).

        The position is pinned to eager bracket management so its protection is REPLACED, never
        merely removed — cancelling a stop while exit_mode is 'db_only' would otherwise leave it
        barer than it was before we touched it.

        Never touches: CNC orders (the delivery portfolio), orders whose product is unknown (fail
        safe), entry-side orders (a pending manual add — absorbed next cycle if it fills), or the
        bot's own bracket / entry / OCO ids."""
        taken = 0
        for position in self.store.get_open_positions():
            own = {position.broker_stop_order_id, position.broker_target_order_id,
                   position.entry_order_id, position.oco_order_id}
            exit_txn = _EXIT_TXN[position.side]
            for o in orders:
                if o.get("symbol") != position.symbol or o.get("order_id") in own:
                    continue
                if str(o.get("product") or "").upper() != "MIS":
                    continue
                if str(o.get("transaction_type") or "").upper() != exit_txn:
                    continue
                try:
                    self.client.cancel_order(o["order_id"])
                except Exception:
                    self._cycle_errors += 1
                    log.exception("foreign exit-order cancel FAILED for %s (%s) — a live "
                                  "resting order may remain; verify at broker!",
                                  position.symbol, o["order_id"])
                    continue
                self.store.set_force_bracket(position.id)
                self.store.record_decision(
                    run_id=run_id, symbol=position.symbol, action="ADJUSTED",
                    reason=f"took over manual {exit_txn} order {o['order_id']} — bot re-places "
                           f"the exit at its own analysed level", position_id=position.id)
                log.warning("took over manual %s order %s on %s",
                            exit_txn, o["order_id"], position.symbol)
                taken += 1
        return taken
```

- [ ] **Step 5: Call it at the end of `_reconcile_broker`**

Immediately before `return synced` in `_reconcile_broker`, add:

```python
        if orders is not None:      # None = the order book read FAILED; never cancel on a guess
            synced += self._takeover_foreign_orders(run_id, orders)
```

- [ ] **Step 6: Honour `force_bracket` in the eager check and the arming gate**

In `_manage_one` (`orchestrator.py:617`), replace:

```python
        eager = cfg.exit_mode in ("armed", "on_fill") and self.client.mode == "live"
```

with:

```python
        # force_bracket pins a position to eager management regardless of the global mode: it is
        # set when reconcile cancelled the user's own exit order, and the replacement bracket is
        # the only thing standing in for the protection we removed.
        eager = ((cfg.exit_mode in ("armed", "on_fill") or position.force_bracket)
                 and self.client.mode == "live")
```

In `_ensure_bracket` (`orchestrator.py:878`), replace:

```python
        if cfg.exit_mode == "armed" and not self._bracket_live(position):
```

with:

```python
        if (cfg.exit_mode == "armed" and not position.force_bracket
                and not self._bracket_live(position)):
```

so a pinned position places its bracket immediately (on_fill behaviour) instead of waiting for price to near a level.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "reconcile: take over the user's own exit orders and replace them with the bot's bracket"
```

---

### Task 8: Orchestrator — no open position may sit without a stop

**Files:**
- Modify: `orchestrator.py` (`_manage_one` tail, ~line 690-692)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Config.adopt_fallback_stop_pct` (Task 2), `_tick` (`orchestrator.py:160`), `Store.update_position_levels`.
- Produces: `Orchestrator._ensure_protective_stop(run_id: int, position, indicators) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_adopted_position_gets_fallback_stop_when_engine_gives_none():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=1.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "MANUAL", "quantity": 5, "product": "MIS", "avg_price": 200.0}])
    # WAIT with no levels — exactly the read that used to leave an adopted position naked
    engine = _FakeEngine(_decision(action="WAIT", stop=None, target1=None))
    orch = Orchestrator(store, client, engine,
                        get_indicators=lambda s: _indic(s, last=200),
                        get_candidates=lambda **kw: [],
                        screen_engine=_FakeScreenEngine(results=[]))
    orch.run_cycle()
    p = store.get_open_positions()[0]
    assert p.stop_loss == pytest.approx(198.0)     # 1% below a 200.0 entry


def test_fallback_stop_is_never_widened_by_a_later_read():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=1.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=200.0, target_price=None, stop_loss=199.0,
                              mode="live")
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 200.0}])
    engine = _FakeEngine(_decision(action="WAIT", stop=None, target1=None))
    orch = Orchestrator(store, client, engine,
                        get_indicators=lambda s: _indic("BOT", last=200),
                        get_candidates=lambda **kw: [],
                        screen_engine=_FakeScreenEngine(results=[]))
    orch.run_cycle()
    assert store.get_position(pid).stop_loss == 199.0   # a real stop is left alone


def test_fallback_stop_disabled_at_zero_pct():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=0.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "MANUAL", "quantity": 5, "product": "MIS", "avg_price": 200.0}])
    engine = _FakeEngine(_decision(action="WAIT", stop=None, target1=None))
    orch = Orchestrator(store, client, engine,
                        get_indicators=lambda s: _indic(s, last=200),
                        get_candidates=lambda **kw: [],
                        screen_engine=_FakeScreenEngine(results=[]))
    orch.run_cycle()
    assert store.get_open_positions()[0].stop_loss is None


def test_fallback_stop_is_above_entry_for_a_short():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=1.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "MANUAL", "quantity": -5, "product": "MIS", "avg_price": 200.0}])
    engine = _FakeEngine(_decision(action="WAIT", stop=None, target1=None))
    orch = Orchestrator(store, client, engine,
                        get_indicators=lambda s: _indic(s, last=200),
                        get_candidates=lambda **kw: [],
                        screen_engine=_FakeScreenEngine(results=[]))
    orch.run_cycle()
    p = store.get_open_positions()[0]
    assert p.side == "SHORT" and p.stop_loss == pytest.approx(202.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q -k fallback`
Expected: FAIL — `p.stop_loss is None` where a fallback was expected.

- [ ] **Step 3: Add `_ensure_protective_stop`**

Insert after `_maybe_trail` (`orchestrator.py:1001`):

```python
    def _ensure_protective_stop(self, run_id: int, position, indicators) -> None:
        """Last-resort floor: no OPEN position may sit without a stop. An adopted position starts
        with none by design, and an engine read that returns WAIT supplies none — so a fallback
        is placed adopt_fallback_stop_pct away from entry. _maybe_trail only ratchets toward
        profit, so the engine's real structural stop replaces this the moment it arrives and can
        never widen it. 0 pct disables the floor."""
        if position.stop_loss is not None:
            return
        pct = self.store.get_config().adopt_fallback_stop_pct
        if pct <= 0 or position.entry_price <= 0:
            return
        raw = (position.entry_price * (1 - pct / 100) if position.side == "LONG"
               else position.entry_price * (1 + pct / 100))
        stop = _tick(raw)
        self.store.update_position_levels(position.id, stop_loss=stop,
                                          target_price=position.target_price)
        self.store.record_decision(
            run_id=run_id, symbol=position.symbol, action="ADJUSTED",
            reason=f"protective fallback stop {stop} ({pct}% from entry) — no engine stop yet",
            stop_loss=stop, target_price=position.target_price, position_id=position.id)
        log.warning("fallback stop %.2f set on %s (engine returned no stop)",
                    stop, position.symbol)
```

- [ ] **Step 4: Wire it into the tail of `_manage_one`**

Replace the last two lines of `_manage_one`:

```python
        # Position stays open — trail its stop/target to the engine's latest read.
        self._maybe_trail(run_id, position, decision)
        return 0
```

with:

```python
        # Position stays open — trail its stop/target to the engine's latest read, guarantee it
        # has SOME stop, then re-sync the broker bracket so a level that just moved is resting at
        # the broker now rather than a cycle from now (_ensure_leg is a no-op when it already is).
        self._maybe_trail(run_id, position, decision)
        position = self.store.get_position(position.id)
        self._ensure_protective_stop(run_id, position, indicators)
        if eager:
            self._ensure_bracket(run_id, self.store.get_position(position.id), indicators, cfg)
        return 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
Expected: PASS

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "manage: guarantee every open position carries a stop"
```

---

### Task 9: Dashboard — expose the fallback-stop knob

**Files:**
- Modify: `dashboard.py:552-580` (the exit-placement sub-tab)
- Test: manual — `dashboard.py` has no unit tests for the settings widgets; `tests/test_dashboard_data.py` covers the data layer only.

**Interfaces:**
- Consumes: `Config.adopt_fallback_stop_pct` (Task 2).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the input widget**

In `dashboard.py`, immediately after the `ex_band = st.number_input(...)` block (`dashboard.py:569-573`) and before the `if em != "db_only":` warning, insert:

```python
            fallback_pct = st.number_input(
                "Fallback stop for unprotected positions (% from entry)", min_value=0.0,
                max_value=10.0, value=float(ex.adopt_fallback_stop_pct), step=0.1,
                format="%.1f",
                help="Applied to any OPEN position that still has no stop after the engine's "
                     "read — an adopted manual position, or a read that returned WAIT. The "
                     "engine's structural stop replaces it as soon as it arrives and never "
                     "widens it. 0 disables the floor.")
```

- [ ] **Step 2: Persist it on save**

Replace the save call (`dashboard.py:578`):

```python
                _db(lambda s: s.update_config(exit_mode=em, arm_exit_band_pct=ex_band))
```

with:

```python
                _db(lambda s: s.update_config(exit_mode=em, arm_exit_band_pct=ex_band,
                                              adopt_fallback_stop_pct=fallback_pct))
```

- [ ] **Step 3: Verify the dashboard imports and renders**

Run: `.venv/bin/python -c "import ast, sys; ast.parse(open('dashboard.py').read()); print('ok')"`
Expected: `ok`

Then launch it and open Settings ▸ the exit-placement sub-tab to confirm the field renders, saves, and survives a reload:

Run: `.venv/bin/python -m streamlit run dashboard.py`

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard.py
git commit -m "dashboard: expose the fallback-stop knob in exit placement settings"
```

---

## Verification

After Task 9, confirm the spec's promises end to end:

- [ ] `.venv/bin/python -m pytest tests/ -q` — full suite green
- [ ] `git log --oneline -9` — nine focused commits, one per task
- [ ] Open an existing production DB copy with the new `store.py` and confirm both migrations apply cleanly and `get_config()` / `get_open_positions()` still work:

```bash
cp ~/.autointraday/autointraday.db /tmp/migration-check.db
.venv/bin/python -c "
from store import Store
s = Store('/tmp/migration-check.db')
print('config ok:', s.get_config().adopt_fallback_stop_pct)
print('positions ok:', len(s.get_open_positions()))
"
```
