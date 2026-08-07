# Swing PnL from the current price ("from here")

Date: 2026-08-07 · Status: approved

## Problem

The swing page's "At target" numbers are signed against the holding's average buy price —
"what does this verdict mean for money already committed". The user reads them as *expected*
PnL: "if I stay until the target, how much do I make from here?" On an underwater holding
(GREENPOWER held at 23.62, target 10.60) the vs-cost number shows a 55% loss even though
reaching the target from the current price is a gain — exactly the confusion reported.

## Design

Show **both** bases; from-here becomes the headline.

- **Price source**: the swing analyst skill already works from live market data, so its reply
  schema gains a required `cmp` field (`number|null`) — the current market price it analyzed
  at. No broker calls from the dashboard; the price is exactly the one the target/stop were
  set against.
- **Storage**: new additive column `swing_verdicts.price_at_analysis REAL` (same migration
  pattern as `status`/`analyzed_at`). `update_swing_verdict` takes `price_at_analysis`;
  `swing_job._analyze_stock` passes the engine's `cmp` through.
- **Economics** (`swing_engine.verdict_economics`): for each leg/kind, alongside the existing
  vs-cost outcome add `<leg>_<kind>_here` = same math with the analysis price as the
  reference. Missing price → `None`, never zero. `book_totals` stays vs-cost (its tiles are
  explicitly labeled against invested capital).
- **Display** (`dashboard.py`): the "At target" cell shows the from-here outcome, falling
  back to vs-cost for rows analyzed before this change (↻ upgrades a row). The expanded
  block shows the analysis price and both labeled lines per leg: *from here* and *vs cost*.

## Testing

Economics: underwater case where vs-cost is negative but from-here is positive; missing
price → `_here` is None. Engine: `cmp` parsed from single and batch replies, tolerated when
absent. Job/store: `cmp` round-trips into `price_at_analysis`. Dashboard: cell prefers
from-here, falls back to vs-cost.

## Out of scope

Live LTP refresh in the dashboard, changing the book-totals tiles' basis.

## Addendum (same day): refresh-time prices, Total PnL column, sorting

- **Refresh holdings** also fetches LTPs (`client.get_ltp`, already exposed by the VPS
  gateway's `/v1/ltp`) and stores them on the snapshot (`holdings.ltp REAL`, additive
  migration). LTP failure degrades to a plain holdings refresh, never an error.
- **Freshest price wins**: `_attach_live_price` annotates each verdict with the holding's
  LTP as `current_price` when the snapshot's `fetched_at` is newer than the verdict's
  `analyzed_at`; `verdict_economics` prefers `current_price` over `price_at_analysis` for
  the `_here` outcomes and exposes the reference used as `ref_price`. Refreshing therefore
  re-prices the table without touching the analysis.
- **Two PnL columns**: "At target" = expected PnL from the current price (— when no price
  is known; no silent vs-cost fallback). "Total PnL" = the same level vs the average buy
  price. Both join the column picker.
- **Sorting**: a "Sort" selectbox beside the existing filters (analysis order, Symbol A→Z,
  At target ↓/↑, Total PnL ↓/↑, Analyzed newest). Rows whose sort value is unknown go last
  in either direction — unknown is not zero.
