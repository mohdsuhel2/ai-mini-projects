# Dashboard UI redesign — design

**Date:** 2026-07-20
**Status:** approved (user, 2026-07-20; layout choice: tabs)

## Goal

Modernize the Streamlit dashboard's look and navigation without touching the data layer:
CSS-injected styling, status pills, stat tiles, and a three-tab structure.

## Layout

- **Header:** title + live IST clock; LIVE/PAPER and ACTIVE/PAUSED rendered as colored
  pills (red/green/amber), not metric widgets.
- **Stat tiles row:** Pool used, Open positions, Resting orders, Next cycle, Realized P&L
  (today), Realized P&L (total) as styled cards.
- **Tabs:** `st.tabs(["Overview", "Performance", "History"])`
  - Overview: pending/resting orders table, open positions table.
  - Performance: trades/win-rate/avg-win/avg-loss/expectancy tiles + exit-reason table.
  - History: date picker → Job runs, Decisions, Closed positions (+ day P&L caption).
- **Sidebar:** unchanged controls (pause, capital rules, mode, schedule), grouped with
  dividers; no logic changes.

## Styling

Single `_CSS` constant injected once via `st.markdown(<style>, unsafe_allow_html=True)`:

- System font stack; `font-variant-numeric: tabular-nums` for numbers.
- Markdown tables (the PyArrow-safe `_md_table` output): padded cells, striped rows,
  header band, rounded border, horizontal scroll via a wrapping div class if needed.
- Metric/stat cards: border + radius + subtle elevation, small uppercase label.
- Tabs: larger labels, accent underline on the active tab.
- All colors as rgba overlays / Streamlit CSS vars so light AND dark themes both work; no
  hardcoded page backgrounds.

## Constraints (unchanged invariants)

- NO `st.dataframe`/`st.table` (PyArrow mimalloc segfault — hard rule).
- `ARROW_DEFAULT_MEMORY_POOL` env line stays before the streamlit import.
- Single-worker SQLite `_db()` pattern untouched.
- `dashboard_data` / `schedule_manager` interfaces untouched.
- No new dependencies, no custom components, CSS only.

## Verification

`py_compile` + import; manual `streamlit run dashboard.py` eyeball in light and dark theme.
Existing test suite must stay green (no logic modules touched).
