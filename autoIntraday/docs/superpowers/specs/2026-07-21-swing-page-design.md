# Swing holdings-analysis page — design

**Date:** 2026-07-21
**Status:** approved (user, 2026-07-21)

## Goal

A totally separate "Swing" page in the dashboard: one button fetches the user's Groww
holdings and runs a Claude swing analysis (both horizons) giving a HOLD / ADD / REDUCE / EXIT
verdict per holding; status → result; every run persisted for comparison against past runs.
Independent of the intraday trading system.

## Decisions (user)

- **Both horizons**, using the EXISTING skill files: `swing-analyst` (days-to-a-month) and
  `shortswing-analyst` (3-5 day), embedded whole as the system prompt — same pattern as the
  intraday `SkillScreenEngine` embeds `intraday-analyst/SKILL.md`.
- On-demand (button), not scheduled. Keep old runs to compare.

## Architecture

### `swing_engine.py` — `SwingEngine.analyze(holdings) -> list[dict]`

One agentic `claude -p` call. System prompt = full text of BOTH
`~/.claude/skills/swing-analyst/SKILL.md` and `~/.claude/skills/shortswing-analyst/SKILL.md`
+ an addendum: "for each holding below, run BOTH skills' methodology and return a verdict per
horizon." Tools: WebSearch + Bash restricted to the two swing data scripts
(`stock_analyze.py`, `stock_analyze_shortswing.py`). `--json-schema` enforces:
`{verdicts: [{symbol, swing: {action, conviction, target, stop, rationale},
shortswing: {action, conviction, target, stop, rationale}}]}` — action ∈
HOLD/ADD/REDUCE/EXIT, conviction 0-100. `TIMEOUT_S = 1800`. Reuses `_result_text` from
`claude_cli_engine`. `SwingEngineError(DecisionEngineError)`. Missing skill file → loud error.
The holdings (symbol, qty, avg_price) are passed in the user message.

### `swing_job.py` — entry point (launched as a detached subprocess by the button)

Loads settings, authenticates to Groww (`GrowwClient(mode="live").authenticate()` — holdings
need real auth regardless of trading mode), fetches `get_holdings()`, opens a `swing_runs`
row (RUNNING), runs `SwingEngine.analyze`, saves `swing_verdicts`, finishes the run
(SUCCESS / FAILED with error). Fully defensive — any failure marks the run FAILED, never
crashes silently. `--db` override for tests.

### Store — two new tables (separate from trading)

- `swing_runs(id, started_at, finished_at, status, num_holdings, error)`
- `swing_verdicts(id, run_id FK, symbol, quantity, avg_price, swing_action, swing_conviction,
  swing_target, swing_stop, swing_rationale, ss_action, ss_conviction, ss_target, ss_stop,
  ss_rationale)`
- Methods: `start_swing_run() -> id`, `finish_swing_run(id, status, num_holdings, error)`,
  `save_swing_verdicts(run_id, rows)`, `get_swing_runs(limit)`, `latest_swing_run()`,
  `get_swing_verdicts(run_id)`. Created in `_SCHEMA` (idempotent CREATE IF NOT EXISTS; new DBs
  and existing ones both get them — CREATE IF NOT EXISTS runs every init).

### Dashboard — multipage via `st.navigation`

`main()` becomes a router: `st.navigation([st.Page(intraday_page, "Intraday"),
st.Page(swing_page, "Swing")])`. The entire current dashboard becomes the Intraday page
(`_render` unchanged). New `swing_page()`:
- **"Analyze my holdings"** button → `subprocess.Popen([python, swing_job.py])` detached;
  writes nothing itself (the job owns the DB row). Disabled while a run is RUNNING.
- **Status** — polls `latest_swing_run()`: RUNNING (with a spinner/caption), SUCCESS, or
  FAILED (shows error). A Refresh button re-polls.
- **Result table** — latest run's verdicts: symbol · qty · avg · swing verdict (action +
  conviction + target/stop) · short-swing verdict · rationale.
- **History + compare** — pick a previous run; show per-symbol verdict CHANGES vs the latest
  (e.g. swing HOLD→EXIT), so the user sees what shifted.

## Data flow

button → Popen(swing_job) → swing_runs RUNNING → engine (claude, minutes) → verdicts saved →
SUCCESS → page (on refresh) shows table. Non-blocking UI throughout.

## Error handling

Groww auth/IP failure, engine timeout, bad JSON, missing skill file → run marked FAILED with
the message; the page shows it. One bad holding never aborts the batch (the model returns what
it can; missing symbols simply absent). Never touches trading tables/config.

## Testing

- `swing_engine`: stubbed runner — parse happy path, envelope unwrap, schema violation, empty,
  timeout, missing skill file, argv/prompt wiring (both skills embedded, Bash allowlist =
  exactly the two swing scripts, holdings in the user message).
- `store`: swing_runs/swing_verdicts round-trip, latest, verdicts-by-run, empty.
- `swing_job`: stubbed engine + client — happy path writes run+verdicts SUCCESS; engine error
  → FAILED; no Groww creds → FAILED.
- Page render untested (repo pattern).

## Out of scope

Scheduling/auto-runs; acting on verdicts (analysis only, no orders); editing holdings;
per-holding manual re-run.
