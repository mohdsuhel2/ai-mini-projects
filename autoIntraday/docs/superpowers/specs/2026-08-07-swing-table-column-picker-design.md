# Swing table column picker

Date: 2026-08-07 · Status: approved

## Problem

The swing Analysis table (`_swing_verdicts_table` in `dashboard.py`) always renders all
8 columns. On narrow windows the columns the user cares about (the Swing / Short-swing
verdict text) get ellipsis-truncated while columns they may not need right now
(Qty, Avg, Analyzed) keep their fixed width. The user wants to choose which columns are
visible; freed space should go to the remaining columns.

## Design

- **Control**: a "Columns" `st.multiselect` added to the existing search + verdict-filter
  row in `_swing_page`, keyed `swing_columns` in `session_state` and read inside the
  auto-refreshing `_swing_live` fragment — the same pattern as `swing_search` and
  `swing_verdict_filter`.
- **Semantics**: empty selection = all columns (matches the verdict filter's
  "empty = no filter" convention and avoids 7 pre-selected chips). Selecting labels shows
  only those columns.
- **Choices**: Status, Qty, Avg, Swing, Short-swing, At target, Analyzed. The Symbol
  column, expand caret and ↻ re-analyze control are always rendered.
- **Rendering**: the table body moves into a pure function `_swing_table_html(verdicts,
  running, visible)` returning the HTML string; `_swing_verdicts_table` becomes a thin
  `st.markdown` wrapper. Hidden columns are skipped in both header and rows; the flexbox
  layout redistributes their width to the flex-grow columns automatically — no CSS change.
- **Persistence**: browser-session only, same as the existing swing filters.

## Testing

`_swing_table_html` is pure → unit tests assert: default shows every column header;
hidden columns appear in neither header nor rows; Symbol and ↻ are always present.

## Out of scope

Drag-to-resize (needs a JS grid, would lose the expandable rationale rows), persisting
the choice in the DB, other dashboard tables.
