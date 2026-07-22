# Swing analysis — Stop / Restart / Resume

**Date:** 2026-07-22
**Status:** Approved

## Problem

The Swing holdings analysis (`swing_job.py`, launched by the dashboard's "Analyze N
holdings" button) runs as a fire-and-forget detached subprocess. The dashboard captures no
PID and has no channel to the running job, so a run can only be watched — never stopped. If
the user starts a long run by mistake, or wants to pause and pick up later, there is nothing
to do but let it finish.

## Goal

Give the user three controls over a running/stopped swing analysis:

1. **Stop** a running analysis immediately.
2. **Restart from start** — after stopping, begin a fresh run over all holdings.
3. **Resume** — after stopping, continue the same run, keeping the stocks already analyzed
   (`✓ done`) and processing only the ones still waiting, starting at the stock that was
   interrupted.

## Decisions

- **Stop is immediate.** The stock mid-analysis is abandoned and reset to `PENDING`; Resume
  re-analyzes it from scratch. (The in-flight LLM call is not awaited.)
- **Restart = new run over all holdings; Resume = same run, remaining only.** Restart creates
  a fresh `swing_runs` row so the interrupted run stays in history for comparison. Resume
  reuses the stopped run and skips `DONE`/`ERROR` rows.
- **PID lives in the DB.** The `swing_runs` row already gets read by the dashboard every 4s
  and survives Streamlit's reruns with no extra bookkeeping — simpler than a pidfile or
  process-group tracking.

## Design

### Schema (`store.py`)

- Additive migration in `Store._migrate`: `ALTER TABLE swing_runs ADD COLUMN pid INTEGER`
  (same guarded pattern as the existing `swing_verdicts.status` migration).
- New run status string `STOPPED` (status is free-text `TEXT`; no schema change).

### Store methods (`store.py`)

- `set_swing_pid(run_id, pid)` — job records its own OS pid at startup.
- `stop_swing_run(run_id) -> int | None` — mark the run `STOPPED`, set its `finished_at`,
  reset any `ANALYZING` verdict back to `PENDING`; return the stored pid (or `None`).
- `resume_swing_run(run_id) -> list[dict]` — set status back to `RUNNING`, clear
  `finished_at`; return the still-`PENDING` verdict rows as holdings dicts
  (`symbol`/`quantity`/`avg_price`) for the job to process.

### Job (`swing_job.py`)

- `run_swing(store, client, engine, holdings=None, resume_run_id=None) -> int`:
  - **Resume branch** (`resume_run_id` set): `run_id = resume_run_id`; holdings come from
    `store.resume_swing_run(run_id)`; skip the Groww auth/fetch and the seed step (rows
    already exist). Loop over the returned PENDING holdings.
  - **Fresh branch** (default): unchanged from today.
  - Both branches record `store.set_swing_pid(run_id, os.getpid())` as soon as `run_id` is
    known, and install a quiet `SIGTERM` handler (best-effort clean exit; the DB state is
    owned by the dashboard's Stop).
  - Resume with no PENDING holdings → finish `SUCCESS` immediately.
- `main()` accepts an optional `--resume <run_id>` argv to select the mode.

### Dashboard (`dashboard.py`)

- `_launch_swing_job(resume_run_id=None)` — append `--resume <id>` when resuming.
- `_stop_swing_job(run_id)` — `store.stop_swing_run(run_id)`, then best-effort
  `os.kill(pid, SIGTERM)` guarded in try/except (the process may already be gone; the DB is
  cleaned regardless).
- Controls in `_swing_page` / `_swing_live`:
  - Status `RUNNING` → **⏹ Stop** button beside the progress bar.
  - Status `STOPPED` → **↻ Restart from start** (new run, all holdings) and
    **▶ Resume (N remaining)** (same run, skip done).
  - `_swing_live` renders a `⏸ Stopped — X done, Y remaining` state.

## Edge cases

- **Stale `RUNNING`, process already dead** (crash / external kill): Stop still marks the run
  `STOPPED` and resets `ANALYZING`; the `os.kill` is best-effort and swallowed.
- **Missing pid** (older run row): Stop does DB-only cleanup.
- **Resume with nothing pending**: job marks the run `SUCCESS` and exits.

## Testing

- `store.py`: `stop_swing_run` resets `ANALYZING`→`PENDING` and returns pid; `resume_swing_run`
  flips status to `RUNNING` and returns only PENDING holdings; `set_swing_pid` round-trips.
- `swing_job.py`: resume mode processes only PENDING holdings, skips auth/fetch, keeps DONE
  rows intact, and finishes SUCCESS; resume with nothing pending is a clean SUCCESS.

## Addendum (2026-07-22): re-analyze a single stock

Alongside the batch "Analyze N holdings", the user can refresh one stock at a time via a
**per-row button in the list**.

**Decisions:** a `↻` button on each row of the analysis table. To host buttons the markdown
tables (`_md_table`) become native `st.columns` grids — safe, since the mimalloc/Arrow segfault
only affects `st.dataframe`/`st.table`, not `st.columns` + `st.markdown`. Clicking a row's `↻`
**updates that stock's verdict in place in the current run** (the row flips ⏳ → ✓; the rest of
the run is untouched) — the coherent behavior for an in-list button. The pre-analysis raw
holdings table gets an "Analyze" button per row that runs the stock as its own fresh
single-stock run (there is no batch run yet to update into).

- `swing_job._analyze_stock(...)` — shared per-stock ANALYZING → DONE/ERROR writer, used by the
  batch loop and the single path.
- `swing_job.run_swing_one(store, client, engine, symbol, run_id=None)` — with `run_id`, update
  that row in place (qty/avg from the existing row); without it, a fresh single-stock run
  (qty/avg from the holdings snapshot). `main()` gains `--symbol` (+ optional `--run <id>`);
  `--symbol` / `--resume` / batch are mutually exclusive.
- `dashboard._launch_swing_one(symbol, run_id=None)` spawns the detached subprocess with
  `--symbol` (+ `--run`).

### Bordered, collapsible analysis table (revision)

To keep the original bordered-table look *and* host per-row actions (an HTML `<table>` can't hold
Streamlit buttons, and `st.columns` gives no real borders), `_swing_verdicts_table` renders each
row as a bordered `<details>` element:

- The `<summary>` shows Symbol / Status / Qty / Avg / Swing / Short-swing and stays visible;
  clicking the row expands the swing + short-swing rationale (`.ai-swt-reason`). A rotating
  caret marks open/closed.
- The ↻ re-analyze control is an internal query-param link (`?swre=SYMBOL`, dimmed/inert while
  the run is RUNNING or that row is ANALYZING). `_swing_page` reads `st.query_params["swre"]` at
  the top, calls `_launch_swing_one(sym, latest_run_id)` (in-place), deletes the param, and
  reruns — so the action fires once, not on every 4s fragment auto-refresh.
- Styling (`.ai-swt*`, `.ai-act*`) matches the existing `.ai-tbl` border/tint system.
- The pre-analysis raw-holdings list stays on the original bordered `_md_table` (no per-row
  action needed before a batch exists).

### Newly-held stocks appear in the Analysis list (bug fix)

**Symptom:** a stock bought after the last batch run didn't show on the Swing page after
"Refresh holdings". **Root cause:** the Groww fetch and the holdings snapshot were correct (the
new symbols were present with full `quantity` — fresh buys carry `t1_quantity` but `quantity`
already includes them); the Analysis table only rendered the *last run's* verdicts, frozen at
that run's holdings snapshot, and the raw-holdings table is hidden once a run exists. So
post-run holdings had nowhere to appear.

**Fix:**
- `_swing_live` unions the current holdings into the verdicts list: any held symbol not in the
  run's verdicts is shown as a synthetic `status="NEW"` row ("· not analyzed yet") with the ↻
  action enabled. A caption flags how many are newly held.
- `swing_job.run_swing_one(..., run_id=...)` now **seeds** a verdict row when the symbol isn't
  in the run (previously `update_swing_verdict` no-op'd on a missing row, so analyzing a
  newly-held stock in place silently vanished). Qty/avg come from the holdings snapshot.

### Per-stock "Analyzed" timestamp

Each verdict records when it was analyzed, shown as an **Analyzed** column (compact IST, e.g.
"22 Jul, 14:32"). Because stocks in one run can be analyzed at different times (a ↻ re-analyze
is newer than the batch), the stamp is per-row, not per-run.

- Schema: `swing_verdicts.analyzed_at TEXT` (additive migration). Rows analyzed before the
  column existed show "—".
- `store.update_swing_verdict` stamps `analyzed_at` on terminal states (DONE/ERROR) with the
  completion time; ANALYZING keeps any prior stamp (COALESCE), PENDING/NEW have none.
- `dashboard._fmt_ist_short` renders the compact date+time; the `NEW` synthetic rows carry
  `analyzed_at=None` → "—".

### Search / filter (revision)

A `st.text_input(key="swing_search")` on the page filters the list by symbol substring
(case-insensitive). The box lives in `_swing_page` (main flow); `_swing_live` reads the value
from `st.session_state["swing_search"]` so the fragment's 4s auto-refresh keeps filtering to the
current query. Applies to both the analysis `<details>` table and the pre-analysis holdings
`_md_table`; shows a "Showing X of Y" caption when filtered and a "No stock matches" note when a
query matches nothing. Filter only — no new analysis (per the chosen option).

## Out of scope

- Graceful "finish current stock then stop" — chose immediate interrupt.
- Stop/resume for the intraday cycle job (separate subsystem).
