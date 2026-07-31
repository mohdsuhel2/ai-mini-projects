# Position rotation — replace the weakest holding with a clearly better candidate

Date: 2026-07-31
Status: approved, ready for implementation

## Problem

When the book is full the cycle stops looking at the market entirely
(`orchestrator.py:1993`):

```python
if free_slots <= 0 or free_capital < cfg.capital_per_position:
    # Book full: do NOT screen the market — Screening now would waste an
    # expensive market scan + LLM calls on trades we can't take.
    return 0, 0
```

With `max_open_positions = 5` and five names held, the bot is **blind to every new opportunity for
the rest of the session**. It manages what it holds and nothing else. On 2026-07-31 the book filled
by 12:19 and stayed full.

The early-exit is not wrong — screening every cycle to take no trade really is wasted spend. What
is missing is the case where a new candidate is *good enough to be worth displacing a holding.*

## Why this is easy to get dangerously wrong

Three facts from this account's own data shape every rule below.

1. **Engine scores are noisy.** BALKRISIND on 2026-07-30 scored 78 → 40 → 54 → 34 in thirty
   minutes. Any rule reacting to one cycle's score will churn.
2. **"Worst performing" is a trap.** BALKRISIND was the most underwater holding at 13:01 and closed
   **+3.20%** — the day's best trade. Ranking by unrealized P&L would have sold it to fund
   something worse. Being down is not evidence of being wrong.
3. **Rotation costs two round trips** (~₹80 plus spread) against a median per-name edge of ₹410 on
   2026-07-30. Marginal rotations lose money even when both picks are sound.

## Design

Rotation is deliberately hard to trigger. Four independent brakes must all release.

### 1. Rank by engine quality, persisted — never by P&L

Each `_manage_one` already receives the engine's `trade_quality` for the held name. Persist it on
the position as `last_quality`, and maintain `weakest_streak`: incremented on a cycle where this
position is the lowest-scoring holding, reset to 0 otherwise.

A position is rotation-eligible only once `weakest_streak >= rotation_confirm_cycles` (default 2) —
the same persistence discipline the exit gate uses, for the same reason: a single weak read is
noise, and we already learned that lesson the expensive way.

Unrealized P&L is **not** an input. A losing position is not a bad position.

### 2. The newcomer must be clearly better, not merely better

`new.trade_quality >= weakest.last_quality + rotation_margin` (default 15 points), **and** the
candidate must independently clear the normal entry gate. A candidate that would not be bought into
a free slot is never good enough to displace a holding.

### 3. Minimum hold before a position can be displaced

`rotation_min_hold_minutes` (default 20). Stops a name being entered and rotated out on adjacent
cycles, which the score noise would otherwise cause routinely.

### 4. Screen at most every Nth cycle when full

`rotation_screen_every` (default 3) — roughly every 15 minutes on the 5-minute grid. Rotation is
not time-critical the way a stop is. Counted by `job_runs` id modulo N so it needs no extra state.

### Execution order

Close the outgoing position **first** and only open the newcomer once that exit is confirmed. Never
hold both: the slot and the margin must actually be free. If the exit fails, abandon the rotation
for this cycle and leave the book untouched.

### Never rotate when

- A daily loss circuit breaker is active
- Inside the square-off window
- The outgoing position has a resting broker leg that cannot be cancelled
- `rotation_enabled` is false (**the default — this ships off**)

## Config

| Key | Default | Meaning |
|---|---|---|
| `rotation_enabled` | `0` | Master switch; ships **off** |
| `rotation_margin` | `15.0` | Quality points the newcomer must beat the weakest by |
| `rotation_min_hold_minutes` | `20` | Minimum age before a holding can be displaced |
| `rotation_confirm_cycles` | `2` | Consecutive cycles a holding must rank weakest |
| `rotation_screen_every` | `3` | Screen when full only every Nth cycle |

New position columns: `last_quality REAL`, `weakest_streak INTEGER NOT NULL DEFAULT 0`.

## Visibility

Every rotation records two decision rows — `EXIT` with
`reason="rotated out: quality 34 vs NEWNAME 71"` and the normal entry — so the pairing is legible
in the dashboard and in the JSON export. A *considered but rejected* rotation logs at info level
with the failing brake named, so "why didn't it rotate?" is always answerable.

## Testing

- Weakest ranking ignores P&L: a deeply underwater but top-scoring position is never eligible.
- The 2026-07-30 BALKRISIND series (78/40/54/34) does **not** rotate at `rotation_confirm_cycles=2`
  until the streak genuinely persists.
- Margin gate: a +14 candidate does not displace at margin 15; +15 does.
- Minimum hold blocks a 5-minute-old position.
- Screen cadence: with `rotation_screen_every=3`, cycles 1 and 2 do not screen when full.
- Failed exit leaves both the old position open and no new entry.
- `rotation_enabled=0` changes nothing — the full-book early exit behaves exactly as today.

## Open risk

This lets the bot close a position it would otherwise have held, on the strength of a score the
engine has already been shown to produce erratically. The brakes are calibrated from a single
session. It ships **disabled**; enable it in paper first and read the rotation decision rows before
trusting it with the live book.
