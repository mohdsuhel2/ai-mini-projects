# autoIntraday Phase 6: UI Dashboard — Design

## Context

Phase 6 of `autoIntraday` (see `2026-07-09-groww-client-design.md` for the full 6-phase
system overview) — the final phase. It gives the user a local web dashboard to see status
(open positions, decisions, job runs, P&L) and to control the system (pause/resume, capital
rules, paper/live mode). It reads the same SQLite DB the scheduler writes.

### Decisions (from brainstorming)

- **Streamlit** (Python-only, runs alongside the cron on the same machine) — chosen in the
  original system brainstorm.
- **The UI writes config only** (pause/resume, capital rules, paper/live mode via
  `update_config`); everything else is read-only.
- **Open positions show plan, not live P&L.** Live unrealized P&L needs a current-price
  fetch, which would pull the broker client + auth into the UI. To keep the dashboard
  **pure-store** (no broker/LLM calls, safe to open anytime), open positions show
  entry/target/stop, and **realized** P&L appears only on closed positions. Live marking is a
  later enhancement.
- **Testable seam:** all data-shaping is in pure functions over a `Store`; Streamlit is a
  thin render layer.

## Architecture

Three layers:

### `store.py` — added read queries (SQL stays in the store)
Phase 2 exposes per-run/per-position reads but not the cross-run history the dashboard needs.
Add (keeping the "only `store.py` touches SQL" global rule):
- `get_recent_runs(limit=20) -> list[JobRun]` — most recent runs, newest first.
- `get_recent_decisions(limit=50) -> list[Decision]` — most recent decisions across all runs,
  newest first.
- `get_recent_positions(limit=50) -> list[Position]` — most recent positions (open + closed),
  newest first.
- `realized_pnl_total() -> float` — sum of `realized_pnl` over CLOSED positions.
- `realized_pnl_since(iso_date: str) -> float` — sum of `realized_pnl` over positions
  CLOSED on/after `iso_date` (for "today's P&L").

### `dashboard_data.py` — pure view functions
Each takes a `Store` and returns plain Python (dicts/lists/floats) — no Streamlit import,
fully unit-testable with an in-memory `Store`:
- `header_view(store) -> dict`: `{mode, is_paused, total_pool, deployed_capital,
  utilization_pct, open_count, max_open_positions, capital_per_position}`.
- `positions_view(store, limit=50) -> list[dict]`: recent positions, each
  `{symbol, side, quantity, entry_price, target_price, stop_loss, status, exit_price,
  exit_reason, realized_pnl, opened_at, closed_at}`.
- `pnl_summary(store, today_iso) -> dict`: `{realized_total, realized_today, open_count}`.
- `decisions_view(store, limit=50) -> list[dict]`: recent decisions
  `{symbol, action, score, reason, entry_price, target_price, stop_loss, created_at}`.
- `runs_view(store, limit=20) -> list[dict]`: recent runs
  `{id, started_at, finished_at, status, mode, num_candidates, num_actions, summary, error}`.

### `dashboard.py` — the Streamlit app
A thin render layer over the view functions plus the config controls. Opens a fresh
`Store(db_path)` per rerun (`AUTOINTRADAY_DB`, same default as the scheduler). Sections:
- **Header:** mode badge (paper/live), paused/active indicator, pool utilization
  (deployed / total, %), open-position count vs max.
- **Controls (writes via `store.update_config`):**
  - Pause/Resume toggle (`is_paused`).
  - Capital rules: numeric inputs for `total_pool`, `max_open_positions`,
    `capital_per_position`, applied on a "Save" button.
  - Paper ⇄ Live toggle — gated behind an explicit confirmation checkbox ("I understand this
    will place REAL orders") before `mode` can be set to `live`; switching back to `paper`
    needs no confirmation.
- **Open positions** table (from `positions_view`, filtered to OPEN): plan + order ids.
- **P&L:** total realized and today's realized (from `pnl_summary`).
- **Decision history** table (`decisions_view`).
- **Job-run log** table (`runs_view`), FAILED runs visually flagged.
- Manual "Refresh" and an optional auto-refresh.

## Concurrency & safety

- The dashboard opens its own `Store` connection per rerun; SQLite handles a reader alongside
  the cron's occasional writes. `Store.__init__` is idempotent (`CREATE TABLE IF NOT EXISTS`,
  seed-only-if-empty), so reopening the same DB each rerun is safe.
- The only writes are config updates through the existing `update_config` (whitelist-guarded
  in Phase 2). No broker or LLM calls happen from the UI.
- The live-mode confirmation makes flipping to live a deliberate two-step action, not a
  single stray click.

## Error handling

- If the DB file doesn't exist yet (no cycle has run), the dashboard shows an empty state
  ("no runs yet — start the scheduler or run one cycle") rather than erroring: opening a
  `Store` on a fresh path creates the schema + seeded config, so views return empty
  lists/defaults cleanly.
- View functions never raise on empty tables — they return empty lists / zero totals /
  the seeded config.

## Testing

- Unit tests for the new `store.py` read methods (ordering newest-first, limits respected,
  realized-P&L sums over CLOSED only, `realized_pnl_since` date boundary) against an
  in-memory `Store`.
- Unit tests for `dashboard_data.py` view functions against a seeded in-memory `Store`:
  header utilization math, positions/decisions/runs shapes and ordering, `pnl_summary`
  totals (today vs all-time), empty-DB behavior.
- `dashboard.py` is verified by launching `streamlit run dashboard.py` manually (not
  unit-tested — it's a thin render layer) and documented in the README.

## Out of scope for Phase 6

Live unrealized-P&L marking (needs a broker price fetch), authentication/multi-user (it's a
local single-user dashboard), and remote hosting. Phase 6 is: read the store, show status,
and toggle config.
