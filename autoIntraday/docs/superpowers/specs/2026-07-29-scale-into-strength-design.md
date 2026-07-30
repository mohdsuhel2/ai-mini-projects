# Scale-into-strength (pyramiding into winners) — design

**Date:** 2026-07-29
**Status:** approved (design), pending implementation + 2026-07-30 live testing
**Branch:** `scale-into-strength` (merges to `main` after tomorrow's testing)

## Problem

Today the bot only *averages down*: `_maybe_scale_in` adds capital to an **underwater**
position on a dip and explicitly **never adds in profit**. There is no way to put *more*
capital behind a position that is clearly working. Combined with the profit-book full-exit at
19% return-on-margin (≈ +3.8% price), strong movers are both under-sized and capped early.

We want the mirror image: when the engine keeps re-affirming a **strong** call on an open
position for several cycles ("persisting"), add capital to chase a bigger move — strictly, and
only on the clearest movers.

## Behaviour (decisions locked with the user)

- **Trigger:** the engine's read on the open position is a **strong same-side entry**
  (`BUY_NOW` for a long / `SHORT_NOW` for a short) with `trade_quality ≥ pyramid_min_quality`
  **and** `confidence ≥ pyramid_min_confidence`, sustained for **`pyramid_confirm_cycles`
  consecutive cycles**. It does **not** require the position to already be in profit.
- **Persistence tracking:** a per-position counter `pyramid_signal_count` increments on each
  qualifying cycle and **resets to 0 on any miss** (a weak/one-off re-affirm does not count).
- **Add size:** `pyramid_add_pct`% of the configured per-position capital (default 50% of
  `capital_per_position`), bought **at market**.
- **Repeat:** up to `pyramid_max_adds` adds (default 2), so a position can reach **2× base**.
  After an add the counter resets — it must re-persist for the next add.
- **Stop:** **unchanged** — keep the engine's latest structural stop (never widened). This
  deliberately accepts more give-back risk in exchange for room to run (user's choice).
- **Exit:** once a position has been pyramided (`pyramid_count > 0`), its full-book level rises
  from the normal `profit_book_full_pct` to **`pyramid_full_pct`** (default 40% on margin ≈
  +8% price). The partial-book (14%) is unchanged.
- **Default OFF** (`pyramid_enabled=false`) — opt-in via the dashboard.

## Guards / invariants

- **Pool guard (hard):** the add must fit the free pool (`total_pool − committed/leverage`);
  it never over-commits the pool. If it doesn't fully fit, add what fits; if nothing fits, skip.
- **Position ceiling:** total deployed margin ≤ `capital_per_position × (1 + add_pct×max_adds/100)`
  (= 2× base at defaults). The normal per-position cap is *intentionally* exceeded up to this
  ceiling — this is the one place a position may exceed `capital_per_position`.
- **Mutually exclusive with `_maybe_scale_in`:** pyramiding requires a *strong re-affirm*;
  scale-in requires *underwater + dipping*. They cannot both fire in a cycle. Pyramiding is
  checked first (strength takes precedence); if it adds, the cycle does not also scale-in or
  trail off the same read.
- **Not near square-off:** no adds in the square-off window (a late add can't earn its move).
- **Works in paper** (simulated fill) for testing, same as `_maybe_scale_in`.

## Data model

`positions` table (additive migrations, default 0):
- `pyramid_count INTEGER NOT NULL DEFAULT 0` — number of adds performed.
- `pyramid_signal_count INTEGER NOT NULL DEFAULT 0` — consecutive persisting strong re-affirms.

Store helpers: increment/reset `pyramid_signal_count`; `add_to_position` already blends the avg
entry and grows quantity — reuse it, then bump `pyramid_count`.

## Config (config table + dataclass + dashboard)

Additive migrations, defaults in parentheses:
- `pyramid_enabled` (bool, **false**)
- `pyramid_add_pct` (50.0)
- `pyramid_max_adds` (2)
- `pyramid_full_pct` (40.0) — raised full-book on margin for pyramided positions
- `pyramid_confirm_cycles` (2)
- `pyramid_min_quality` (80.0)
- `pyramid_min_confidence` (75.0)

Dashboard: a toggle + inputs in a "Scale into strength" settings block.

## Code shape

- `Orchestrator._maybe_pyramid(run_id, position, decision, indicators) -> bool` — the new unit.
  Returns True if it added (so the caller skips scale-in/trail this cycle). All guards above live
  here. Mirrors `_maybe_scale_in`'s structure (sizing, market order, `record_order`,
  `add_to_position`, `record_decision` with an `ADD` action, loud log).
- `_manage_one`: call `_maybe_pyramid` **before** `_maybe_scale_in`; if it returns True, return 0
  (added this cycle). The persisting counter is incremented/reset inside `_maybe_pyramid` based on
  whether the current `decision` qualifies, independent of whether it actually adds (so a
  qualifying-but-pool-blocked cycle still counts toward persistence).
- `_maybe_take_profit`: when `position.pyramid_count > 0`, use `cfg.pyramid_full_pct` for the
  full-book level instead of `cfg.profit_book_full_pct`.

## Testing (TDD)

- Fires only after `pyramid_confirm_cycles` consecutive strong same-side re-affirms; a one-off /
  weak read resets the counter and does **not** add.
- Respects `pyramid_max_adds` and the 2× ceiling; a further add is refused.
- Pool guard: an add that would over-commit the pool is trimmed or skipped.
- Keeps the structural stop unchanged; blends the average entry; increments `pyramid_count`.
- `_maybe_take_profit` uses `pyramid_full_pct` once pyramided, normal `profit_book_full_pct`
  otherwise.
- Never fires on an underwater position (that path is scale-in's), and never both add + trail in
  the same cycle.
- `pyramid_enabled=false` → never fires.
- Store: new config knobs default correctly, round-trip, and migrate onto an existing DB; position
  counters default 0 and migrate.

## Out of scope

- Trailing-stop or run-to-target exits (we chose "raise the book %").
- Breakeven/lock-in stop moves on an add (we chose "keep structural stop").
- Any change to the underwater `_maybe_scale_in`.
