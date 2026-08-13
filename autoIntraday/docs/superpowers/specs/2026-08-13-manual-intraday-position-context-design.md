# Manual Intraday — give the skill the full picture of a held position

Date: 2026-08-13 · Status: approved

## Problem

Analysing a stock you already hold sends the skill exactly one line of context:

```
You currently HOLD this: 40 @ avg ₹1830.5 (MIS).
```

Quantity, average price, product — and nothing else. Three things that change the answer are
missing:

- **Your P&L.** The skill cannot see whether you are up 8% or down 12%. It could derive this,
  since its data tool fetches the live price and it has your average, but it must notice and do
  the arithmetic rather than being told. Whether a position is underwater is decisive for a
  hold-versus-exit call.
- **Exits you already have resting.** If an order placed from this page is `ARMED`, a live OCO
  sits at Groww with a specific stop and target on that symbol. The skill is told nothing, so it
  proposes fresh levels as though the position were unprotected — and nothing warns you that
  acting means replacing resting orders.
- **The book, in top-5 mode.** No position context is passed at all, so the skill can suggest an
  entry on something you are already long.

`broker_positions` also has no `ltp` column, unlike `holdings`, so P&L cannot be computed on our
side today.

## Design

### Live price on the position snapshot

`broker_positions` gains `ltp REAL`. The table already exists in the live database, so this needs
an `ALTER TABLE` migration — `CREATE TABLE IF NOT EXISTS` silently no-ops on an existing table.

`_refresh_positions_from_groww` calls `client.get_ltp` for the fetched symbols and stores the
result alongside. A quote failure degrades to a plain position refresh, exactly as the Swing
page's holdings refresh does: the price is an enrichment, not a requirement.

### P&L, computed once and shared

`position_pnl(position) -> dict | None` in `manual_engine`, returning `pnl`, `pct`, `side` and
`value`, or `None` when the price, average or quantity is missing — never a fabricated zero.

Groww reports a short as a **negative quantity**, so `(ltp - avg) * quantity` is correct for both
sides without branching. The percentage is sign-corrected by the side, so a short that has fallen
reads as a gain.

### What the skill is told

For a single symbol, the context becomes:

```
You currently HOLD this: 40 shares @ avg ₹1830.50 (MIS).
Live price ₹1868.00 — unrealised +₹1,500 (+2.05%) on ₹74,720 of exposure.
You ALREADY have exits resting at Groww from an earlier order: stop ₹1808.00,
target ₹1905.00 on 13 shares. New levels REPLACE these, they do not add to them.
```

Each line appears only when its data exists. A symbol you do not hold keeps today's wording.

For top-5, a book summary is prepended so the skill can see what it would be adding to:

```
Positions you already hold: KEI 40 @ 1830.50 (+2.05%), BSE 10 @ 99.00 (-3.10%).
Say so plainly if a pick is something already held.
```

### Seen on screen too

The Positions table gains **LTP** and **P&L** columns (rupees and percent), so the same
information you are sending to the skill is visible while you pick.

## Testing

- `position_pnl`: long in profit and in loss; a short, where a falling price is a gain; missing
  ltp / avg / quantity each yield `None`; a zero average is rejected rather than dividing.
- Prompt builders: the held line carries qty, average, price and P&L; the resting line appears
  only with an armed order and states that new levels replace it; a flat symbol keeps the
  fresh-entry wording; the top-5 book summary lists holdings and is absent when the book is empty.
- Store: `ltp` round-trips, and the migration adds it to a database created before this change.
- Job: resting exits are matched per symbol and the newest armed order wins; positions are passed
  to top-5.
- Dashboard: the fetch stores LTP and degrades to a plain refresh when quotes fail; the table
  shows LTP and P&L.

## Out of scope

Position age (Groww's positions response carries no open time, and inferring it from order
history is not worth the complexity). Any change to order placement or the OCO flow.
