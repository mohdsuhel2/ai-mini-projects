# Manual Intraday — tell the operator what is actually happening

Date: 2026-08-13 · Status: approved

## Problem

The page reports too little, and in one case reports something untrue.

- **Confirmations vanish.** Placing an order, modifying exits and squaring off each confirm with
  a `st.toast` that disappears after a few seconds. Placing also switches you to Results, so the
  only acknowledgement of a real-money action is easy to miss entirely.
- **Errors are raw exceptions.** `Could not load positions: {e}` and the two order-management
  handlers print whatever Python raised. A Groww auth failure reads as a stack-trace fragment.
- **Progress has no time dimension.** The strip shows `3/5` with no elapsed time and no estimate.
  A five-stock run takes roughly ten minutes. For a top-5 run the fraction is meaningless — it
  is one call — yet it renders as `0/?` behind a 0% bar.
- **A dead run locks the page and the UI keeps claiming it is working.** `Fetch positions`,
  `Analyze N selected` and `Top 5` are all `disabled=running`, and nothing ever clears a
  `RUNNING` run whose detached job has crashed. There is no stop and no liveness check, so the
  page becomes permanently unusable while the status strip shows a progress bar. This is a bug,
  not only a messaging gap.
- **In-flight orders are invisible.** Only `FILLED` and `ERROR` raise a signal. An order resting
  as `ENTRY_PENDING` — a LIMIT that may sit for hours — shows nothing anywhere except inside the
  Orders tab.
- **Statuses are not explained.** `ARMED`, `FILLED`, `ENTRY_PENDING` carry terse labels with no
  statement of what they mean or what to do about them.

## Design

### Persistent receipts

Every action that reaches the broker writes a receipt into `st.session_state["mi_notice"]`, a
dict of `{kind, text}` rendered as a durable banner directly beneath the status strip and
replaced by the next action. Placing an order produces, for example: *"Order #12 sent — PAPER
LONG 13 KEI, MARKET entry. Track it under Orders."* Toasts remain as an additional flourish but
are no longer the only acknowledgement.

### Readable errors

`_friendly_error(exc) -> tuple[str, str]` returns a plain-language headline plus the raw text.
Recognised cases: Groww authentication (points at the `.env` credentials), network or timeout,
insufficient margin, and the manual broker's own `ManualBrokerError` messages, which are already
written for a human and pass through unchanged. The raw text is always rendered underneath in a
collapsed expander, so nothing is ever hidden — only demoted.

### Progress with time

`_run_progress_text(run, progress, now)` produces the strip's line:

- symbols mode: `⏳ intraday-analyst-2 — 3/5 · 6m elapsed · ~4m left`, where the estimate comes
  from the mean time the completed rows actually took, falling back to two minutes per symbol
  before the first one finishes;
- top5 mode: `⏳ intraday-analyst-2 — single call, screening the market · 2–4 min typical ·
  running 1m40s`, with no fraction, because one call has no meaningful denominator.

### Stuck and dead runs

`Store.stop_analysis_run(run_id)` mirrors `stop_swing_run`: it marks the run `STOPPED`, resets
any `ANALYZING` row to `PENDING`, and returns the stored pid so the caller can signal the
process. `_pid_alive(pid)` uses `os.kill(pid, 0)`.

The strip then distinguishes three cases instead of one:

- process alive and progressing — normal progress line;
- process alive, no row completed for five minutes — the progress line plus *"no progress since
  HH:MM — the skill may still be thinking"*;
- process gone — an error line saying the run died, with a **Stop run** button that clears the
  `RUNNING` state and unlocks the page.

A **Stop run** button also appears whenever a run is RUNNING, so a run can always be abandoned.

### In-flight orders in the strip

Orders in `PLACING` or `ENTRY_PENDING` produce a neutral info line naming the symbols and the
time since placement, so a resting LIMIT is visible without opening the Orders tab.

### Status meanings

`_MANUAL_STATUS_HELP` maps each status to one sentence of meaning and, where action is needed,
what to do. Rendered as a caption inside each order's expander.

## Testing

Pure helpers, tested directly: `_friendly_error`, `_run_progress_text` (both modes, with and
without completions), `_pid_alive`, `_run_health(run, progress, now)` returning
`ok`/`stalled`/`dead`, `_inflight_orders`, and `_fmt_duration`. Store: `stop_analysis_run`
mirrors the swing behaviour and returns the pid. AppTest, against a temporary database:
a dead run shows the died message and a Stop button, a stalled run shows the stalled note, and
a pending order raises the in-flight line.

## Out of scope

Resuming a stopped analysis run (the swing page's Resume has no equivalent need here — a manual
run is cheap to re-launch). Any change to order placement, OCO arming or the analysis engine.
