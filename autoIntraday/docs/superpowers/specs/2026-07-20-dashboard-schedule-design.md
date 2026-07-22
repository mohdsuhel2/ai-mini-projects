# Dashboard schedule controls — design

**Date:** 2026-07-20
**Status:** approved (user, 2026-07-20)

## Problem

The trading schedule (first cycle, last cycle, interval) lives in the installed launchd plist
and can only be changed by editing/reloading it from a terminal. The user wants to view and
change it from the Streamlit dashboard, see when the next cycle fires, and wants the "Job
runs" table shown above the "Decisions" table.

## Design

### New module: `schedule_manager.py`

Owns all launchd-schedule knowledge; the UI stays a thin render layer.

- `INSTALLED_PLIST = ~/Library/LaunchAgents/com.autointraday.cycle.plist` (module constant,
  overridable in tests). `SQUAREOFF = (15, 18)` — always present, not editable from the UI.
- `read_schedule(path) -> {"start": (h,m), "last": (h,m), "interval_min": int}` — parses
  `StartCalendarInterval`, ignores the square-off entry, infers start/last from min/max and
  interval from the gap between the first two regular entries (interval 0/one-entry edge:
  interval reported as 0).
- `build_entries(start, last, interval_min) -> list[dict]` — regular fires every
  `interval_min` from `start` while `<= last`, for Weekday 1–5, plus the square-off entry.
- `next_fire(path, now) -> datetime | None` — earliest future entry (IST), scanning up to 8
  days ahead (handles weekends). Includes the 15:18 square-off. NSE holidays are NOT
  considered (the runner's guard skips those fires; the UI captions this).
- `validate(start, last, interval_min) -> str | None` — error string or None. Rules:
  5 <= interval <= 120; start >= 09:15; last <= 15:00; start <= last.
- `cycle_running() -> bool` — `pgrep -f run_cycle_job.py` (any live cycle process).
- `apply_schedule(start, last, interval_min, path) -> str` — validate; refuse if
  `cycle_running()` (reloading launchd kills a mid-flight cycle — run-23 lesson, 2026-07-17);
  rewrite ONLY `StartCalendarInterval` via plistlib (EnvironmentVariables/creds untouched,
  chmod 600 preserved); `launchctl bootout` (ignore failure) + `launchctl bootstrap`
  (raise `ScheduleError` on failure). Returns a human summary ("10 cycles/day ...").
  The repo's `deploy/` plist copy is documentation and is NOT touched by the dashboard.

### `dashboard.py` changes

1. Sidebar section **"Schedule"** (below Mode): three inputs pre-filled from
   `read_schedule()` — `st.time_input` first/last cycle, `st.number_input` interval (min) —
   and an **Apply schedule** button. On click: `apply_schedule(...)`; success → `st.success`
   + rerun; `ScheduleError` (validation, cycle running, launchctl) → `st.error(str(e))`.
2. Header metrics row: sixth column **"Next cycle"** — `next_fire()` rendered as
   `HH:MM IST · in Nm` (or "—" when the plist is missing); caption notes the holiday guard.
3. Day-history order becomes: Closed positions → **Job runs** → Decisions.

### Error handling

Missing/unparseable plist: `read_schedule`/`next_fire` raise `ScheduleError` /return None;
the sidebar section shows the error instead of inputs; the metric shows "—". All launchctl
output is captured and included in `ScheduleError` messages.

## Testing

`tests/test_schedule_manager.py`, temp plist files: read/build round-trip; square-off always
present and excluded from read; next_fire mid-day, after last regular (→15:18), after
square-off (→next weekday 09:45), Friday evening (→Monday); validate bounds; apply_schedule
happy path with stubbed launchctl runner + preserved EnvironmentVariables; refusal while
cycle_running; launchctl failure raises. Dashboard render stays untested (repo pattern).

## Out of scope

Editing the square-off time; holiday-aware next_fire; syncing the repo plist copy; changing
the runner/orchestrator.
