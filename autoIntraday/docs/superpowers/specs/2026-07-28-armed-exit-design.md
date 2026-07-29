# Armed broker exit — place a real Groww exit order when price is near a profit level

**Date:** 2026-07-28
**Status:** implemented 2026-07-28 (default OFF). Config + Position columns, `_maybe_arm_exit` /
`_reconcile_armed_fills` / `_arm_or_rearm` in orchestrator, dashboard toggle+band, 9 new tests.

## Problem

Profit levels (partial book at `profit_book_partial_pct`, full exit at `profit_book_full_pct`,
plus the structural target) are **soft** — enforced by the 5-minute cycle polling a MARKET exit.
A fast spike to a profit level that reverts *between polls* is missed. The user wants a **real
resting exit order at Groww** placed only when price is **really close** to a profit level, so the
broker fills it instantly — without arming early and locking in a target that may still trail.

This is a **scoped** re-introduction of broker-side resting orders. Full OCO is off
(`USE_BROKER_OCO=False`) because cancel wasn't trusted; the cancel path is now fixed
(benign-terminal handling + diagnosable errors + no per-request re-auth), which makes this safe.

## Decisions (confirmed with user)

- **Arm at BOTH levels**: half the position at the partial-book level, then the remaining half at
  the full/target level.
- **Closeness band: 1.0%** — arm when price is within 1.0% of the level price. (Configurable.)
- **Live-only, default OFF.** Paper keeps the existing poll-based book (paper has no between-poll
  gap — it fills at poll-time LTP anyway). Ships disabled; enable from the UI once the live state
  is clean and cancel is verified in production.

## Config (new, in `config` table + `Config` dataclass, dashboard-editable)

- `arm_exit_enabled: bool = False`
- `arm_exit_band_pct: float = 1.0`

## Position fields (new columns + migration + `Position` dataclass)

- `armed_partial_order_id: str | None` — resting SELL LIMIT for the partial half.
- `armed_full_order_id: str | None` — resting SELL LIMIT for the remaining/full position.

## Level prices (LONG; mirror for SHORT)

- `partial_price = entry * (1 + (profit_book_partial_pct / LEVERAGE)/100)`
- `full_price    = entry * (1 + (profit_book_full_pct   / LEVERAGE)/100)`
- effective full profit target = the profit exit the poll would take = `min(full_price, target)`
  for LONG (`max` for SHORT), so the armed full order never sits above where the poll would exit.
- `near(level) = |ltp - level| / level <= arm_exit_band_pct/100`

## Mechanics — new step `_maybe_arm_exit(position, indicators)` in `_manage_one`

Runs live-only, when `arm_exit_enabled`, **after** `_exit_level`/`_maybe_take_profit` (so a level
already breached this cycle still exits at market — arming is only for the *between-poll* gap), and
only when NOT near square-off.

1. **Fill detection FIRST** (each cycle, per armed order id):
   - `get_order_status(id)` → if FILLED:
     - partial order filled → `book_partial(...)` (reduce qty, set `partial_booked`, trail runner
       stop→breakeven), clear `armed_partial_order_id`, record `BOOK_PARTIAL`.
     - full order filled → `close_position(...)` at the fill price, clear id, record `TAKE_PROFIT`,
       return `exited=True`.
   - if REJECTED/CANCELLED → clear the id (re-arm below if still valid).

2. **Double-sell suppression:** while `armed_partial_order_id` is set and pending, the poll-based
   `_book_partial_slice` is skipped for that position; while `armed_full_order_id` is pending, the
   poll-based full take-profit is skipped. The broker order is the single source of that exit.

3. **Arm / re-arm:**
   - Phase A — `not partial_booked`: if `near(partial_price)` and no partial order armed → place a
     resting SELL LIMIT for `floor(qty * PROFIT_BOOK_FRACTION)` at `_tick(partial_price)`; store id.
   - Phase B — `partial_booked` (runner remains): if `near(full_target)` and no full order armed →
     place a resting SELL LIMIT for the full remaining qty at `_tick(full_target)`; store id.
   - **Re-arm on drift:** if an armed order exists but its level has moved (target trailed) beyond
     0.1% from the order's price → cancel (benign-safe) and re-arm at the new level. This is the
     "don't lock a stale target" guarantee.

4. **Cancel on any other exit:** extend `_close_position` and `_square_off_all` to cancel
   `armed_partial_order_id` / `armed_full_order_id` (in addition to `oco_order_id`) BEFORE the
   market exit — so a stop/square-off/signal exit can never leave a resting SELL that later fills
   into a naked short. Cancel is now benign on already-filled orders, so a fill-vs-cancel race is
   safe: if it already filled, we detect the fill next; if we cancel first, the market exit stands.

## Safety properties

- Never both market-exit and leave a live resting exit: cancel-before-exit + benign cancel.
- Never double-sell: poll book suppressed while the corresponding order is armed.
- Never lock a stale target: re-arm on drift; 1% band keeps drift small.
- Cancel failure on a still-open order is surfaced (`_cycle_errors++` + loud log + notify), never
  silently ignored.
- Default OFF; live-only.

## Testing

- Level math: `near()` boundaries; partial/full price from return-on-margin ÷ leverage; LONG+SHORT.
- Arm Phase A places half-qty LIMIT at partial price; Phase B places remaining at full target.
- Poll suppression: partial not double-booked while armed; full not double-exited while armed.
- Fill detection: partial fill → book_partial; full fill → close_position.
- Re-arm on drift: target trails up → old order cancelled, new order at new price.
- Cancel-on-exit: stop/square-off cancels armed ids first; benign when already terminal.
- Disabled / paper: no arming; existing behavior unchanged.
