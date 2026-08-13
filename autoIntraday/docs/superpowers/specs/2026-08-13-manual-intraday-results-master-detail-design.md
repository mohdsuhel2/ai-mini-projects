# Manual Intraday — results as a table plus one detail panel

Date: 2026-08-13 · Status: approved

## Problem

The Results tab renders every result as a full card, and the trade ticket opens automatically
whenever the verdict is an actionable entry. That is right for one result and wrong for five.

Measured on a realistic five-pick top-5 run, the tab renders **50 interactive widgets at once**:
20 number inputs, 10 dropdowns, 10 tabs, 5 expanders and 5 buttons — four complete six-field
trade forms open simultaneously, each behind its own pair of Analysis/Raw tabs. Nothing can be
compared across stocks, because the numbers are buried in separate cards separated by whole
screens of form controls.

## Design

Master–detail: a compact table of every result, and one detail panel for the row you select.

### The table

One row per result, with the columns that make stocks comparable at a glance: Symbol, Verdict,
Conviction, Entry, Stop, T1, R:R, and Status. Rendered with `st.dataframe(on_select="rerun",
selection_mode="single-row")` so a click selects.

**Row order is the run's own order, not re-sorted.** For a top-5 run the skill returns its picks
best-first, and that ranking is information — re-sorting by conviction would silently discard the
skill's own judgement. Streamlit's built-in column sorting remains available for anyone who wants
a different view, and the caption says so.

Unknown values render blank rather than zero. Rows still being worked on show their status
(`⏳ analyzing`, `· waiting`) in place of a verdict, so progress is visible in the same table
instead of a separate list underneath.

### The detail panel

Below the table, the selected result only: its existing card (verdict badge, conviction, levels,
summary), then three tabs — **Trade**, **Analysis**, **Raw** — with Trade first because acting is
what the page is for. The ticket is a tab rather than an expander now that only one exists at a
time.

Selection is stored as a **symbol**, not a row index, in `st.session_state`. The tab
auto-refreshes every four seconds and rows appear as a run progresses, so an index would drift
and silently select a different stock. When nothing is selected, or the selected symbol is no
longer in the run, the panel falls back to the first result that has a verdict.

A result that is not `DONE` shows its status and error in the panel instead of a ticket — there
is nothing to trade.

This takes the tab from 50 widgets to roughly 11, independent of how many results the run holds.

## Testing

Two pure helpers:

- `_results_table_rows(results) -> list[dict]` — one display row per result, unknowns as `None`,
  in-progress rows labelled by status, order preserved.
- `_pick_result(results, symbol) -> dict | None` — the selected result by symbol, falling back to
  the first with a verdict, and `None` for an empty run.

Plus an AppTest render, against a temporary database, of a five-result run asserting the widget
count has collapsed, exactly one trade ticket exists, and selecting a different symbol moves the
detail panel.

## Out of scope

Any change to order placement, the OCO flow, the analysis engine, or the other three tabs.
