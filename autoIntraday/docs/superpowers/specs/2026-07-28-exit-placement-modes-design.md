# Exit placement modes — soft / armed / on-fill broker brackets

**Date:** 2026-07-28
**Status:** implemented 2026-07-28 (default `db_only`). config.exit_mode + broker_stop/target
columns, orchestrator bracket methods (`_ensure_bracket`/`_reconcile_bracket_fills`/`_cancel_bracket`
etc.), Capital sub-tabs + exit-mode radio, tests reworked. 382 green. Partial-book kept SOFT (resizes
the bracket) per the approved design.
**Supersedes:** the standalone armed-exit toggle from `2026-07-28-armed-exit-design.md` (that feature
becomes the "armed" mode here; its config/columns are reworked into the bracket model below).

## Goal

Replace the single armed-exit toggle with **one "exit placement" setting offering three modes**, and
in the two eager modes place a **full broker bracket (stop + target) as real Groww orders**, cancelled
OCO-style (one fills → cancel the other) using the now-reliable cancel. This gives real broker-side
protection on BOTH sides (upside spikes AND downside gaps between the 5-min polls), at a configurable
cost in API calls.

## The three modes (config `exit_mode`, default `db_only` = today's behavior)

| Mode | Broker orders placed | Protects gaps? | API load |
|------|----------------------|----------------|----------|
| `db_only` | none — the 5-min cycle fires a MARKET exit when price hits the soft stop/target | no | lowest |
| `armed` | the stop+target bracket, placed only when price is within `arm_exit_band_pct` of EITHER level | partial | medium |
| `on_fill` | the stop+target bracket, placed immediately when the entry fills; kept in sync as levels trail | full | highest |

`db_only` is exactly the current soft behaviour — nothing changes for existing users until they opt in.

## The broker bracket (eager modes)

Per OPEN position, up to two resting orders at Groww for the **current quantity**:

- **Target** — `SELL LIMIT` (long) / `BUY LIMIT` (short) at the full take-profit / structural target
  (`min(full-profit price, target)` for a long; `max` for a short — same `_full_exit_price` as before).
- **Stop** — `SELL SL_M` (long) / `BUY SL_M` (short) with `trigger_price` = the stop level (market
  fill on trigger, so a gap still exits).

Software OCO: they are NOT a native bracket (Groww's OCO is unreliable — `USE_BROKER_OCO=False`).
We hold both order ids and enforce one-cancels-other ourselves.

**Partial-book stays SOFT** (poll-based, books half at `profit_book_partial_pct`). When it fires it
resizes the bracket to the remaining qty and trails the stop to breakeven. (Keeping the bracket a clean
2-leg OCO; a partial-target-as-broker-order can be a later addition.)

## Lifecycle (per cycle, in `_manage_one`)

1. **Reconcile fills first.** Check both bracket order statuses:
   - target FILLED → close position at the target price; cancel the stop (benign if already gone). Exit.
   - stop FILLED → close position at the stop price; cancel the target. Exit.
   - rejected/cancelled → clear that id (re-place below if still wanted).
2. **Soft exits still apply for SQUARE-OFF** (always market-flatten at 15:18, cancelling the bracket
   first). In eager modes the poll's STOP/TARGET market-exits are **suppressed** while the bracket is
   live — the broker orders own those exits (no double-sell).
3. **Partial-book (soft)** — unchanged trigger; on book, resize the bracket to the new qty + trail stop.
4. **Ensure/refresh bracket** (`_ensure_bracket`):
   - `on_fill`: place the bracket as soon as the position is open (and any leg is missing).
   - `armed`: place it once price is within `arm_exit_band_pct` of the stop or the target.
   - Either mode: if a level trailed and the resting order's price drifted > `ARM_REARM_DRIFT_PCT`,
     cancel + replace that leg at the new level (never rest a stale price).
   - `db_only`: never place; if a bracket somehow exists (mode changed), cancel it.
5. **Trail** — when the engine moves stop/target, modify the corresponding broker leg (cancel+replace).
6. **Any non-bracket exit** (`_close_position`: square-off, reverse-signal, take-profit-full) cancels
   BOTH legs first, so a resting order can never fire after we're flat (no naked reverse). Safe because
   cancel is a benign no-op on an already-filled leg.

## Safety properties

- No double-sell: poll STOP/TARGET exits suppressed while the bracket is live.
- No naked position: every other exit cancels both legs first; cancel is benign post-fill.
- No stale level: legs re-placed on drift / trail.
- Gap-safe (on_fill): stop is a real `SL_M` trigger at the broker, so a between-poll gap still exits.
- Reversible & opt-in: default `db_only` is today's behaviour; switching modes only changes placement.

## Data / config changes

- `config.exit_mode TEXT NOT NULL DEFAULT 'db_only'` (migration; validated to the 3 values).
  Keep `arm_exit_band_pct`. Retire `arm_exit_enabled` (map any existing True → `exit_mode='armed'`
  once at migration; column left in place, unused).
- Position columns: `broker_stop_order_id/broker_stop_price`, `broker_target_order_id/broker_target_price`
  (migration). The earlier `armed_partial_*/armed_full_*` columns are left in place, unused.
- `store.set_bracket_leg(position_id, which('stop'|'target'), order_id, price)`.

## Orchestrator changes (replaces the armed_* methods)

`_bracket_levels(position)`, `_ensure_bracket`, `_reconcile_bracket_fills`, `_place_bracket_leg`
(LIMIT target / SL_M stop), `_modify_bracket_leg` (cancel+replace), `_cancel_bracket`; `_close_position`
+ `_square_off_all` cancel the bracket; `_exit_level`/poll suppression while a bracket is live.
`groww_client.place_order` already supports `SL_M` + `trigger_price`.

## UI — Settings ▸ Capital reorganised into nested sub-tabs

`st.tabs(["Capital", "Exits", "Execution"])` inside the Capital tab:
- **Capital** — pool, max positions, capital/position.
- **Exits** — profit-taking (toggle + partial/full %), then **Exit placement** (radio: DB-only / Armed /
  On-fill; band shown only for Armed).
- **Execution** — entry nudge / stop widen / target shave.

## Testing

- Mode gating: db_only never places; on_fill places on open; armed places only within band.
- Bracket place: target LIMIT + stop SL_M at correct levels/qty/side (long & short).
- Reconcile: target fill → close@target + cancel stop; stop fill → close@stop + cancel target.
- OCO-on-other-exit: square-off / signal cancels both legs first (benign if one already filled).
- Poll suppression: no double market-exit while a bracket is live; square-off still flattens.
- Partial-book resizes the bracket; trail modifies the drifted leg.
- Config: exit_mode default `db_only`, persists; arm_exit_enabled=True migrates to 'armed'.
