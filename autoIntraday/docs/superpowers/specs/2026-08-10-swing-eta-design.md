# Swing target ETA ("when should this pay off?")

Date: 2026-08-10 · Status: approved

## Problem

The swing page shows each holding's target, stop and "At target" PnL, but nothing says *when*
the target is expected to hit. The two legs carry only implicit horizons (shortswing = 3–5
trading days, swing = days-to-a-month). The user wants an explicit per-holding ETA for the
expected result.

## Design

The analyst estimates the ETA; the dashboard converts it to a calendar date at render time.

- **Engine** (`swing_engine.py`): the per-leg reply schema (`_LEG`) gains a required
  `eta_days` field (`integer|null`) — the analyst's expected number of **trading days** from
  analysis until the target is reached. The prompt addendum instructs: estimate it from the
  momentum/ATR data the tool already shows, keep it within the leg's natural horizon
  (shortswing ≈ 1–5 td, swing ≈ 3–22 td), and return null when the action is EXIT or there is
  no target. `_leg()` parses defensively: missing or null → `None`, so pre-change replies
  still parse.
- **Storage** (`store.py`): additive columns `swing_verdicts.swing_eta_days INTEGER` and
  `ss_eta_days INTEGER` (same migration pattern as `price_at_analysis`).
  `update_swing_verdict` writes them from the legs; `swing_job` passes legs through
  unchanged.
- **Date conversion** (`trading_calendar.py`): new pure helper
  `add_trading_days(start_date, n, holidays)` that skips weekends and NSE holidays. The ETA
  date is computed at render time from the verdict's `analyzed_at` + `eta_days`, so nothing
  stored ever goes stale.
- **Display** (`dashboard.py`): new "ETA" column joins the column picker, showing the swing
  leg (consistent with the "At target" column): `~7 td · 19 Aug`. When the computed date is
  already past it renders dimmed as `was due 19 Aug` — an honest signal the thesis is late,
  not an error. The expanded row shows both legs' ETAs alongside their rationales. Rows
  analyzed before this change show `—`; the existing per-row ↻ upgrades them.

## Testing

- Engine: `eta_days` parsed when present, tolerated when absent or null.
- Store: `eta_days` round-trips through `update_swing_verdict` for both legs.
- Calendar: `add_trading_days` handles weekends, holidays, and n=0.
- Dashboard: ETA cell renders future, past, and unknown ETAs; column joins the picker.

## Out of scope

ATR-implied sanity check, re-estimating ETA on refresh without re-analysis, changes to book
totals.
