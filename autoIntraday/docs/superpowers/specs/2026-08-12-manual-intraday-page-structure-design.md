# Manual Intraday — page structure

Date: 2026-08-12 · Status: approved
Restructures the page built by `2026-08-12-manual-intraday-orders-design.md`. No change to what
orders are sent or how.

## Problem

The page grew feature by feature into one long scroll, and its shape now works against it.

- **The primary action is buried.** Placing a trade takes three clicks: result expander →
  "Formatted" tab → "Place order" expander. Trading is the point of the page.
- **The most urgent state is the least visible.** An order that filled but whose OCO failed to
  arm is a live, unprotected position. It is announced only inside a collapsed expander at the
  bottom of the page, below the entire results list.
- **Configuration interrupts the workflow.** The settings expander sits between the run buttons
  and the results.
- **The mode is visually weak.** PAPER vs LIVE — the difference between a simulation and real
  money — is plain text inside a sentence.
- **No sense of stage.** Positions, run, results and orders all compete for the same attention
  with no hierarchy saying what to do first.

## Design

The page is four stages — positions, analyse, act, manage — so it becomes a status strip plus
four tabs.

### Status strip

A bordered bar above the tabs, rendered on its own `@st.fragment(run_every=4)` so run progress
updates without re-rendering everything else. It carries:

- the mode as a coloured pill (grey `PAPER`, red `LIVE — REAL ORDERS`);
- positions count and the age of the snapshot;
- the active run's skill and progress, or a note that none is running;
- an alert row.

The alert row is the reason the strip exists. `FILLED`-but-not-armed and `ERROR` orders raise a
red banner that is visible from **every** tab, because an unprotected live position must not
depend on which tab happens to be open. The autoIntraday-is-live warning renders here too.

### Tabs

Built with the existing `_url_tabs` helper on the query parameter `mi`, so the section survives
a browser refresh — a run takes minutes, and reloading mid-run must not throw you back to the
first tab.

1. **Positions & Run** — fetch button, snapshot age, the checkbox table, the skill picker, the
   two run buttons and the time estimate.
2. **Results** — one bordered card per result: symbol, a tone-coloured verdict badge, conviction
   and the levels on one line, then the **Trade** ticket, then **Analysis** and **Raw** as tabs
   beneath. The ticket is an expander that opens by default when the verdict is an actionable
   entry (`side_from_verdict` returns a side) and stays closed otherwise, so a `WAIT` does not
   push other results off screen.
3. **Orders** — orders needing attention (`FILLED`, `ERROR`) pinned to the top, then the rest
   newest-first, each with its modify and square-off controls.
4. **Settings** — mode and capital per trade, out of the workflow, with an explicit warning on
   the live toggle.

### Colour

Verdict badges are toned so five results can be scanned without reading them: green for long
entries, red for short and exit verdicts, neutral grey for `WAIT` / `HOLD` / anything unknown.
Tone is derived from the same vocabulary `side_from_verdict` uses, with exits treated as red
because they are an instruction to get out, not to stand still.

### Code organisation

The Manual Intraday code is grouped under one section banner in `dashboard.py`. Extracting it
into its own module is a genuine improvement — the file is ~2,600 lines — but it depends on
`_db`, the IST formatters and the shared CSS, so a clean extraction is a larger refactor and is
deliberately left as separate work.

## Testing

Four pure helpers carry the logic so it is testable outside Streamlit:

- `_verdict_tone(verdict) -> str` — `good` / `bad` / `flat`.
- `_strip_facts(positions, fetched_at, run, progress, orders) -> dict` — the counts and flags the
  strip renders, including `attention` (filled-unarmed plus errored orders).
- `_orders_for_display(orders) -> list` — attention-first, then newest-first, with no row lost.
- `_ticket_open_default(verdict) -> bool` — open for an actionable entry, closed otherwise.

Plus an AppTest render of each of the four tabs asserting no exception, and the existing ticket
and card tests continue to pass unchanged.

## Out of scope

Any change to order placement, the OCO flow, sizing, or the analysis engine. Extracting the page
into its own module.
