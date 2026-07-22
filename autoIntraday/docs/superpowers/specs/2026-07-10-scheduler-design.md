# autoIntraday Phase 5: Scheduler — Design

## Context

Phase 5 of `autoIntraday` (see `2026-07-09-groww-client-design.md` for the full 6-phase
system overview). Phase 4 delivered `Orchestrator.run_cycle()` (one trading cycle). Phase 5
fires it on the right schedule — hourly during market hours on trading days — and skips
closed-market times so no Opus calls or orders happen when the market is shut.

### Decisions (from brainstorming)

- **launchd** (macOS-native LaunchAgent), not cron — reliable across reboots/login, clean
  restart semantics, captures stdout/stderr to log files. The user's Mac is on IST, so all
  schedule times are wall-clock IST.
- **Bundled NSE holiday list + weekday + market-hours guard.** The runner exits early and
  cheaply on any non-trading time (weekend, NSE holiday, outside hours) — no LLM calls, no
  orders. Explicit and predictable, rather than probing a closed market.
- **Mode lives in the store config.** The runner reads `mode` (paper/live) from the Phase 2
  config row and builds `GrowwClient(mode)` accordingly — so the UI (Phase 6) can toggle
  paper/live without touching the scheduler.

## Architecture

### `trading_calendar.py` — the trading-day/time guard
- `load_holidays(path) -> set[str]`: reads a bundled `nse_holidays.txt` (one `YYYY-MM-DD`
  per line; blank lines and `#` comments ignored) into a set of ISO date strings.
- `is_trading_time(now, holidays, open_time=(9, 15), close_time=(15, 30)) -> bool`: true only
  when `now` (a timezone-aware IST `datetime`) is a weekday (Mon–Fri), its date is NOT in
  `holidays`, and its time is within `[open_time, close_time]`. Pure — `now` and `holidays`
  are injected, so it is fully unit-testable with no clock or file dependency.
- Times are compared in IST (`Asia/Kolkata`). The runner passes `datetime.now(IST)`.

### `run_cycle_job.py` — the entry point launchd invokes
`main()`:
1. Resolve the DB path from `AUTOINTRADAY_DB` (default `~/.autointraday/autointraday.db`);
   create the parent directory if missing. The DB is file-backed so state persists across
   hourly runs — each launchd invocation reopens the same file.
2. `now = datetime.now(IST)`. If `not is_trading_time(now, load_holidays(...))` → log
   "market closed, skipping" and exit 0. **No store run, no LLM, no orders.**
3. Open `Store(db_path)`; read `cfg = store.get_config()`; build
   `GrowwClient(mode=cfg.mode)`, `DecisionEngine(use_web_search=True)`, and an
   `Orchestrator(store, client, engine, get_indicators, get_candidates)`.
4. `summary = orch.run_cycle()`; log the summary (entries/exits/candidates/status).
5. Wrap 3–4 in try/except: any exception is logged with a traceback and re-raised as a
   non-zero exit (so launchd records the failure and the log captures it) — but a crash
   inside `run_cycle` has already marked the store run FAILED (Phase 4's guard); this outer
   handler covers a crash BEFORE `run_cycle` (auth build, config read).

Testable seam: the trading-day decision is factored as `should_run(now, holidays) -> bool`
(thin wrapper over `is_trading_time`) so the guard is unit-tested without constructing the
real collaborators. The collaborator wiring in `main()` is exercised by the manual smoke run
(and the Phase 4 cycle smoke), not by a unit test.

### launchd config — `com.autointraday.cycle.plist` + install doc
A LaunchAgent plist with `StartCalendarInterval` entries (an array, one dict per fire time)
for the hourly slots on weekdays, running the venv Python against `run_cycle_job.py`, with
`StandardOutPath`/`StandardErrorPath` pointing at log files under the DB directory. Weekday
filtering (`Weekday` 1–5) is in the plist; NSE holidays (which launchd cannot express) are
caught by the runner's guard. A short `scripts/install_launchd.md` (or a tiny generator
script) documents `launchctl bootstrap`/`bootout` and where to put the plist
(`~/Library/LaunchAgents/`).

### `nse_holidays.txt` — bundled, editable holiday list
Seeded with the known 2026 NSE trading holidays, one `YYYY-MM-DD` per line, with a header
comment. **Holiday dates are not asserted with false precision:** the file carries a note to
verify the seeded list against the official NSE holiday calendar and to update it each
January for the new year. Editing it needs no code change.

## Schedule

Fire times (IST, weekdays): **11:00, 12:00, 13:00, 14:00, 15:00**, plus **15:15** as a
square-off pass — at 15:15 the indicator `session.minutes_to_squareoff` is ≤ 15, so the
orchestrator's `_should_square_off` flattens every open position before the ~15:20 intraday
cutoff. The guard's trading window is 09:15–15:30 (so all six fires pass the time check on a
trading day); the plist only schedules the useful hours.

## Error handling

- Market-closed is a clean exit 0 (not an error) — the common case must be silent/cheap.
- A failure in cycle setup or `run_cycle` is logged with a traceback and surfaces as a
  non-zero exit so launchd and the log file both record it; the store run is marked FAILED by
  Phase 4's own guard when the failure is inside `run_cycle`.
- Every run appends to a log file, so a series of runs is auditable outside the DB too.

## Testing

- `trading_calendar.py`: unit tests with injected `now` and holiday sets — weekday vs weekend,
  holiday vs non-holiday, inside vs outside the window (including the 09:15 open and 15:30
  close edges), and `load_holidays` parsing (comments/blank lines ignored).
- `run_cycle_job.should_run`: unit test that it delegates to `is_trading_time` correctly
  (market-open → True, weekend/holiday/after-hours → False).
- A manual, not-CI smoke: run `run_cycle_job.py` once by hand (during or outside market
  hours) and confirm it either runs one paper cycle or logs "market closed, skipping" — plus
  the documented `launchctl` load/verify step for the plist.

## Out of scope for Phase 5

The UI (Phase 6). Phase 5 is purely: fire `run_cycle` on the right schedule, skip
closed-market times cheaply, and log outcomes.
