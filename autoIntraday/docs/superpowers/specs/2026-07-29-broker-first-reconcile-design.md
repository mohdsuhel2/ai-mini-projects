# Broker-First Reconcile — Adopt and Repair

**Date:** 2026-07-29
**Status:** Design approved, pending implementation

## Problem

The user trades manually between cycles. `_reconcile_broker` (`orchestrator.py:400`) already
observes that drift and records it in the DB, but it never **repairs the broker side**, and it
handles only two of the five ways a position can drift. The result is stale resting orders left
armed at Groww and manual positions the bot cannot see.

Concretely, today:

1. **A manual exit leaves the bot's orders live.** The reconcile close calls
   `store.close_position(...)` directly (`orchestrator.py:447`) instead of `_close_position`,
   which is the method that cancels the bracket and OCO first (`orchestrator.py:552-553`). Sell
   your long by hand and the bot's stop-loss SELL order stays armed — it can fire and open a
   naked short.
2. **A manual partial exit leaves stale-quantity legs.** Quantity is shrunk
   (`orchestrator.py:455`) but the bracket is not rebuilt. `_ensure_leg` re-places only on
   *price* drift (`orchestrator.py:850-858`), never on quantity change, so legs keep resting for
   the old, larger size and can over-sell. The partial-profit path already cancels the bracket
   for exactly this reason (`orchestrator.py:641`); reconcile does not.
3. **Adding to a bot position is invisible.** Reconcile handles only `net == 0` and
   `abs(net) < quantity`. Buy more of something the bot holds and the extra shares get no stop,
   no target and no square-off.
4. **A side flip is ignored or corrupted.** Bot LONG 10, you sell 20 → broker is SHORT 10, but
   `abs(-10) < 10` is false, so nothing happens and the DB still says LONG 10. At SHORT 5 it
   "shrinks to 5" while keeping side LONG — wrong direction, wrong stop.
5. **An adopted position can end up with no stop.** It is created with `stop_loss=None`
   (`orchestrator.py:474-476`). If the engine's read that cycle returns no stop, nothing is set
   and `_ensure_leg` skips `None` — the position rides unprotected to square-off.
6. **CNC holdings mask MIS exits.** The `net` used for the sync checks sums all products
   (`orchestrator.py:432`), so a delivery holding in the same symbol hides a fully-closed MIS
   position.
7. **The bot's bracket can duplicate the user's own exit order.** If the user placed their own
   stop-loss and the bot then places its bracket, two sell orders rest against the same shares.
   One fires; the other opens a naked reverse position.

## Goal

Every live cycle, Groww is the source of truth for **both positions and orders**. The bot reads
that truth first, repairs the book and the broker to match it, and manages everything it finds —
including whatever the user did by hand.

## Non-goals

- Paper mode. `_reconcile_broker` continues to return immediately unless mode is live; paper
  orders live only in the DB and there is nothing to reconcile against.
- CNC / delivery adoption. Never adopted, unchanged. Flattening the long-term portfolio at
  square-off would be catastrophic.
- Reconstructing P&L for a manually-sold slice. Its fill price is unknown and is not invented.

## Design

### Component 1 — `_reconcile_broker` becomes read-then-repair

Split the current monolith into four units with clear boundaries:

| Unit | Responsibility | Depends on |
|---|---|---|
| `_broker_state()` | Read positions + orders once; return MIS net qty / avg price per symbol, keyed **separately from CNC**, plus the normalized live-order list | `client.get_positions`, `client.get_open_orders` |
| `_sync_known(run_id, position, mis)` | One DB-open position → exactly one outcome from the table below | `_cancel_stale_orders`, store |
| `_adopt(run_id, symbol, net, avg)` | One unknown MIS position → one new book entry | store |
| `_cancel_stale_orders(run_id, position)` | Cancel bracket legs + OCO for a position whose size or existence changed | `client.cancel_order`, `client.cancel_oco_order` |

`_broker_state()` fixes problem 6: MIS net is accumulated in its own map and CNC quantities never
contribute to the `net` used for sync decisions.

### Component 2 — the drift decision table

For each DB-open position, with `q` = DB quantity, `s` = DB side, `n` = broker **MIS** net:

| Broker says | Outcome | Decision row |
|---|---|---|
| `n == 0` | cancel stale orders → close | `EXIT` / `BROKER_SYNC` |
| same side as `s`, `abs(n) == q` | no-op | — |
| same side, `abs(n) < q` | cancel stale orders → shrink qty to `abs(n)` | `ADJUSTED` |
| same side, `abs(n) > q` | cancel stale orders → **absorb** (below) | `ADJUSTED` |
| opposite side to `s` | cancel stale orders → close old, then **adopt** `n` as a fresh position | `EXIT` + `ADOPTED` |
| symbol unknown to the DB, `n != 0` | **adopt** | `ADOPTED` |

**Absorb** (manual add) sets `quantity = abs(n)` and `entry_price` to the broker's reported
`avg_price` — that is the true blended cost basis, so booked P&L stays honest. The existing stop
is **kept and never loosened**; the ratchet rule in `_maybe_trail` continues to govern it. This
needs a new store method, since `update_position_quantity` cannot change the entry price:

```python
def update_position_size(self, position_id: int, quantity: int, entry_price: float) -> None:
    """Sync an OPEN position's size AND blended cost basis to broker reality (manual add
    detected by reconcile). Raises StoreError if the position is not open."""
```

**Side flip** is decomposed, not special-cased: close the old position via the same path as
`n == 0` (exit price = LTP, reason `BROKER_SYNC`), then run `_adopt` on the new side. The
adopted position is a fresh row and gets its levels from the engine like any other adoption.

Cancelled legs are not re-placed by reconcile. `_manage_positions` runs later in the **same
cycle** and `_ensure_bracket` rebuilds them at the corrected quantity and levels.

### Component 3 — the bot takes over the user's exit orders

`_broker_state()` returns the full normalized order list, not just the symbol set. To make that
safe, `GrowwClient.get_open_orders()` gains a `product` field in its normalized dict
(`groww_client.py:316-322`). One change covers both transports: the gateway wraps the same
`GrowwClient` (`groww_gateway/app.py:42-46, 146-147`), so the field propagates through the
direct-SDK path and the gateway path alike.

For a managed position, an order is a **foreign exit order** when all of:

- same symbol,
- `product == "MIS"` — a CNC resting sell belongs to the user's delivery portfolio and must
  never be touched,
- non-terminal status, using the terminal set already computed in reconcile
  (`_REJECTED_STATES | _FILLED_STATES`, `orchestrator.py:422`),
- `transaction_type` is the closing side (`SELL` for a LONG, `BUY` for a SHORT),
- `order_id` is not the position's own `broker_stop_order_id` / `broker_target_order_id` /
  `entry_order_id` / `oco_order_id`.

Foreign exit orders are **cancelled and replaced** by the bot's own bracket at the level its
analysis produced. This is the user's explicit choice: the bot updates the SL/exit order per its
own understanding rather than deferring to the manual one. Each cancellation is recorded as an
`ADJUSTED` decision naming the order id, so the takeover is visible in the dashboard and never
silent.

**Replace, never merely remove.** Cancelling the user's stop while the global `exit_mode` is
`db_only` (the default) would leave the position *less* protected than before — soft levels only,
nothing resting at the broker between cycles. So a position whose foreign exit order was taken
over is **promoted to eager bracket management for the rest of the day**, as if `exit_mode` were
`on_fill`, regardless of the global setting. A new `force_bracket` flag on the position row
carries this; `_manage_one` treats `eager` as `exit_mode in ("armed", "on_fill") or
position.force_bracket`. The promotion is recorded as an `ADJUSTED` decision.

Ordering matters here: reconcile takes over foreign orders **before** `_manage_positions` runs,
so by the time `_close_position` market-exits anything, no foreign exit order can still be
resting against those shares. `_close_position` needs no change.

A foreign order on the **entry** side is left alone — it is a pending manual add, and if it fills
the next cycle absorbs it through the `abs(n) > q` branch.

A cancel that fails increments `_cycle_errors` and logs loudly, matching `_cancel_leg`
(`orchestrator.py:536-541`). It never aborts the cycle.

### Component 4 — no position may sit stopless

Adoption already leads to analysis in the same cycle: the adopted position is in
`get_open_positions()` by the time `_manage_positions` runs, so the full intraday skill runs on
it with position context and `_maybe_trail` writes stop and target. That remains the primary
path and produces the real, structural levels.

The safety net is a new `_ensure_protective_stop(run_id, position, indicators)` called at the end
of `_manage_one`. If `stop_loss` is still `None` after the engine's read, it sets a fallback at
`adopt_fallback_stop_pct` from entry (below entry for a LONG, above for a SHORT) and records it
as `ADJUSTED`. Because `_maybe_trail` only ratchets toward profit, the engine's structural stop
replaces the fallback as soon as it arrives and can never widen it.

This guard covers every stopless open position, not only adopted ones — one rule, no special
case.

### Component 5 — new persistence

`force_bracket` on the positions table (default 0), set when a foreign exit order is taken over
so the promotion survives across cycles, with the standard additive migration
(`store.py:365-372`):

```python
if "force_bracket" not in cols:
    self._conn.execute("ALTER TABLE positions ADD COLUMN force_bracket INTEGER NOT NULL "
                       "DEFAULT 0")
```

plus a `set_force_bracket(position_id)` setter, mirroring `set_reverse_signal_count`.

### Component 6 — new config knob

`adopt_fallback_stop_pct: float = 1.0` on `Config` (`store.py:40`), added to `_CONFIG_FIELDS`,
with the standard additive migration used by every other knob (`store.py:382-400`):

```python
if "adopt_fallback_stop_pct" not in ccols:
    self._conn.execute("ALTER TABLE config ADD COLUMN adopt_fallback_stop_pct REAL NOT NULL "
                       "DEFAULT 1.0")
```

Exposed in the dashboard Settings panel alongside the other risk knobs.

### Re-entry after a manual exit

Unchanged, per the user's decision. A manual exit frees the slot before `_screen_and_enter` runs,
so the symbol competes for entry again like any other name. No blacklist, no cooldown.

## Error handling

Reconcile stays fully defensive — it runs before everything else and must never block a cycle:

- `get_positions()` fails → log and skip reconcile entirely (existing behaviour, unchanged).
- `get_open_orders()` fails → log; proceed with position sync but perform **no** order takeover
  (an empty order list must not be read as "the user has no orders", so nothing is cancelled).
- Any per-symbol failure is caught and logged; the remaining symbols still reconcile.
- Broker cancel failures increment `_cycle_errors`, surfacing as the existing macOS notification
  from `run_cycle_job.py`.

## Testing

TDD, table-driven, against the fake Groww client already used in `tests/test_orchestrator.py`.
One test per row of the decision table, plus:

1. Manual full exit cancels both bracket legs **and** the OCO before closing.
2. Manual partial exit cancels the bracket; the next `_manage_one` re-places it at the new qty.
3. Manual add absorbs qty and blended entry, and does **not** loosen the existing stop.
4. Side flip closes the old position and adopts the new side with the correct side and quantity.
5. A CNC holding in the same symbol does not mask a fully-closed MIS position.
6. A foreign MIS exit order is cancelled and replaced by the bot's bracket.
7. A foreign **CNC** sell order is never cancelled.
8. A foreign order on the entry side is left resting.
9. The bot's own bracket legs are not mistaken for foreign orders.
9a. Taking over a foreign order sets `force_bracket`, and the bot then places a broker stop even
    when `exit_mode` is `db_only` — the position is never left with less protection than before.
10. A stopless open position receives the fallback stop.
11. The fallback stop is never widened by a later engine read; a tighter structural stop replaces
    it.
12. `get_open_orders()` failing disables takeover without cancelling anything.
13. Paper mode reconciles nothing.

## Files touched

| File | Change |
|---|---|
| `orchestrator.py` | Split `_reconcile_broker`; add `_broker_state`, `_sync_known`, `_adopt`, `_cancel_stale_orders`, `_takeover_foreign_orders`, `_ensure_protective_stop`; `eager` honours `force_bracket` |
| `store.py` | `update_position_size`, `set_force_bracket`; `force_bracket` column + `adopt_fallback_stop_pct` config field, both with additive migrations |
| `groww_client.py` | `product` in the normalized `get_open_orders()` dict |
| `dashboard.py` | Surface `adopt_fallback_stop_pct` in Settings |
| `tests/test_orchestrator.py` | The table above |
| `tests/test_store.py` | `update_position_size`, config migration |
| `tests/test_groww_client.py` | `product` in normalized orders |
