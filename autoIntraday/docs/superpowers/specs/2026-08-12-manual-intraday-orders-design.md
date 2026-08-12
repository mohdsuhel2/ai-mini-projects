# Manual Intraday — place real Groww orders from the analysis

Date: 2026-08-12 · Status: approved
Supersedes the page naming in `2026-08-12-manual-analysis-page-design.md`.

## Problem

The Analyze page reads a skill's verdict — side, entry, stop, targets — and then stops. Acting
on it means retyping those numbers into the Groww app, which is slow and error-prone at exactly
the moment speed matters. The analysis and the execution should be one motion.

This must be **my own desk, not the bot**. It places real orders on my Groww account regardless
of what autoIntraday's paper/live config says, because it is not part of autoIntraday.

## Design

The page is renamed **Manual Intraday** (`url_path="manual-intraday"`).

### Independence from autoIntraday

`manual_broker.py` constructs its own `GrowwClient` and never reads the trading config. The page
carries its **own** mode (`manual_config.mode`) and its own capital-per-trade setting, persisted
separately from autoIntraday's. Mode defaults to `paper` so a first click cannot be expensive;
one toggle switches it to `live`, and the current mode is displayed prominently at all times.

Broker-protocol constants (filled/rejected status strings) are duplicated in `manual_broker.py`
rather than imported from `orchestrator.py`. They are facts about Groww, not autoIntraday
policy, and importing the orchestrator would drag the entire trading engine into this page and
defeat the separation.

### The order ticket

Each analysis result's Formatted tab gains a "Place order" section, prefilled from that skill's
own numbers: side inferred from the verdict, entry, stop and target exactly as suggested. Every
field is editable. Quantity prefills as `floor(capital_per_trade / entry)` and is editable.

Live-computed beside the fields: rupee exposure, worst-case loss at the stop, reward at the
target, and the resulting risk:reward.

`validate_bracket` rejects geometrically impossible brackets before anything is sent — a long
whose stop sits above entry, or whose target sits below it (and the mirror for a short) would
fire the instant it armed. This is a fat-finger guard, not a strategy opinion.

### What gets sent

`manual_order_job.py` runs detached and does three things in order:

1. Place the entry — MARKET or LIMIT — via `GrowwClient.place_order`.
2. Poll `get_order_status` every 10s until the order fills, is rejected, or the deadline passes
   (default 6h, covering a LIMIT that rests all session). A MARKET entry resolves on the first
   poll.
3. On fill, arm a **native Groww OCO** (`place_oco_order` → `create_smart_order`) carrying the
   target and stop.

The broker owns the one-cancels-other, so both exit legs rest safely and there is no orphan-leg
risk and no watcher to maintain. Stops route through `place_order`, which already translates
`SL_M` into the buffered `SL` that the 2026-08-03 NSE 16448 finding requires — this feature adds
no new order-construction code.

Status lifecycle: `PLACING → ENTRY_PENDING → FILLED → ARMED`, or `REJECTED` / `ERROR` / `CLOSED`.

### Managing what was placed

A "Manual positions" section lists every order this page sent, with its status, fill price and
resting OCO levels, and two actions:

- **Modify exits** — `modify_oco_order` moves the target/stop in place.
- **Square off** — market exit for the remaining quantity, then cancel the resting OCO.

### Storage

Two new tables, `manual_config` (single row: mode, capital_per_trade) and `manual_orders`
(symbol, side, quantity, entry type/price, stop, target, mode as placed, status, broker order
ids, fill price, timestamps, error, and the skill/result that suggested it).

Deliberately NOT named `positions` — that is autoIntraday's own trade ledger, and an earlier
draft of the Analyze page collided with exactly that name.

### The risk that cannot be engineered away

Code separation is real; broker separation is not. There is one Groww account. If autoIntraday
is switched to **live**, its `_reconcile_broker` will `_adopt` any MIS position this page opened
and `_takeover_foreign_orders` will cancel the manual exit orders to replace them with its own
analysed levels. While autoIntraday is in paper mode (`_reconcile_broker` returns immediately
unless `client.mode == "live"`) this cannot happen.

The page therefore reads autoIntraday's mode and, only when it is live, shows a prominent
warning that the bot will take over positions opened here. Surfacing the conflict beats letting
the two silently fight.

## Testing

- **Pure helpers**: `entry_txn`/`exit_txn` per side; `qty_for_capital` floors and handles a zero
  or missing price; `order_economics` computes exposure/risk/reward/RR and returns None for
  unknown legs; `validate_bracket` rejects inverted long and short brackets and accepts valid
  ones.
- **Broker**: entry placement passes the right transaction type and order type; paper mode never
  reaches a live SDK; OCO is armed with the exit-side transaction; square-off cancels the OCO
  before its market exit; a rejected entry raises with the broker's reason attached.
- **Store**: `manual_config` round-trips and defaults to paper; `manual_orders` round-trip
  through the full status lifecycle.
- **Job**: a MARKET entry fills on the first poll and arms the OCO; a rejected entry records the
  reason and never arms an OCO; a LIMIT still pending at the deadline stays `ENTRY_PENDING` with
  no OCO; an OCO failure after a filled entry records the error without losing the fill.
- **Dashboard**: the ticket prefills from a result and recomputes on edit; the live-mode warning
  appears only when autoIntraday is live; the page is registered as `manual-intraday`.

## Out of scope

Trailing stops, partial exits, CNC/delivery orders, and any change to autoIntraday's own
reconcile behaviour.
