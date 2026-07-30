# Reversal persistence: a sudden opposing read must not act on an open position

Date: 2026-07-30
Status: approved, ready for implementation

## Problem

On 2026-07-30 the live book lost ₹17,543 across seven closed trades, all exited `STOP`.
BALKRISIND was the one trade that worked — it closed **+3.20%** above entry — and the bot
still booked −₹1,510 on it. It was not stopped out by the market.

At 13:01:13 the `intraday-analyst-2` skill flipped bearish and returned a **short** plan for a
symbol the book was **long**:

```json
{"action":"SELL_NOW", "confidence":62, "trade_quality":34,
 "entry":2229.6, "stop_loss":2250.23, "target1":2213.7}
```

For a short those levels are correct — stop above entry, target below. The position was LONG at
2253.98 and the tape was at 2229.20.

`_maybe_trail()` (`orchestrator.py:1195`) read `stop_loss: 2250.23` and applied it, because for a
long it only asks "is the new stop higher than the old one?" — and a short's stop always is:

```
trailed stop 2213.7 -> 2250.23
```

That placed the stop 21 points **above** the market on a long. Four minutes later it fired.

### The hole is in an existing feature, not a missing one

`orchestrator.py:810-834` already gates signal exits on conviction and persistence: an opposing
read must clear `MIN_EXIT_QUALITY = 55` **and** `MIN_EXIT_CONFIDENCE = 55`, and repeat for
`EXIT_CONFIRM_CYCLES = 2` consecutive cycles. Its comment states the intent exactly:

> A weak or one-off flip just resets the counter and the trade rides its structural stop.

BALKRISIND's four SELL reads scored quality 34, 54, 34, 34 — all below the floor. **The gate
correctly refused to exit.** Across every position on 2026-07-30 the convicted-flip counter never
reached 2; the `SIGNAL` exit path did not fire once.

But when the flip fails the conviction test, control falls through to `_maybe_trail()`, which
consumes the levels of that same rejected plan. The system decides "too weak to exit on", then
exits anyway through the back door — destroying the structural stop the comment promises.

### Scope of the defect

Six cross-side writes across four positions in one session:

| position | side | skill said | stop written | effect |
|---|---|---|---|---|
| BALKRISIND | LONG | SELL_NOW | 2213.7, then **2250.23** | above market → forced exit |
| PNGSREVA | LONG | SELL_NOW | 436.0, then **452.11** | above market → forced exit |
| BLACKBUCK | LONG | SELL_NOW | 553.75 | pinned to market → out in 5 min |
| PREMIERENE | SHORT | BUY_NOW | 989.0 | compressed to 0.07% from entry |

## Design

Three changes, all in `orchestrator.py`. No change to stop-loss strategy, stop values, or the
conviction thresholds.

### 1. `_opposes(action, side)` — pure predicate, extracted from existing code

The codebase **already** defines exactly this set, inline at `orchestrator.py:1557-1558`, where the
resting-order refresh cancels a pending order whose side has flipped:

```python
opposite_actions = (("SELL_NOW", "SHORT_NOW") if position.side == "LONG"
                    else ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT"))
```

Extract that into a module-level `_opposes(action, side)` and have both call sites use it. Do not
invent a parallel vocabulary: these five strings are the actions the orchestrator actually handles
(`IMMEDIATE_ENTRY_ACTIONS`, `RESTING_ENTRY_ACTIONS`, `SELL_NOW`), and they match every action
observed in the 2026-07-30 decision data. The resting-order path keeps its current behaviour —
this is a pure extraction there, with the new gate as the second consumer.

`HOLD`, `WAIT`, `NO_TRADE` are **neutral, not opposing**. They carry valid same-side levels and
must continue to trail normally.

### 2. Gate `_maybe_trail` on it

An opposing read may feed the reversal counter — it already does, upstream — but may **never**
move stop or target. Levels stay wherever the last same-side or neutral read put them.

On refusal, log and record a decision row using the established idiom at line 828
(`action="HOLD"` plus an explicit reason), so it renders in the dashboard, counts nowhere in the
`num_actions` tally, and lands in future JSON exports:

```
"ignored SELL_NOW levels — opposing read, stop 2213.70 kept"
```

### 3. `_stop_is_sane(side, stop, ltp)` — absolute clamp

A LONG's stop must be strictly **below** the live price; a SHORT's strictly **above**. A violation
is refused — the previous stop is kept — and logged as a defect.

Applied at the two places an engine-supplied stop reaches a position, independent of the direction
gate: `_maybe_trail()` and `_ensure_protective_stop()`. Both already receive `indicators`, so the
live price is available without changing any signature.

This is defence in depth. It catches PNGSREVA's 452.11 even if such a level arrives via a path the
direction gate does not cover — pyramid, scale-in, adopt, or a future caller.

## Explicitly unchanged

- `EXIT_CONFIRM_CYCLES = 2` and the 55/55 floors stay hardcoded. They were never the binding
  constraint on 2026-07-30, and the conviction floors are what protected BALKRISIND.
- The genuine-reversal path is untouched: two consecutive **convicted** flips still exit on
  `SIGNAL`. This change only stops *unconvicted* flips from exiting via a corrupted stop.
- No stop-loss sizing, trailing strategy, or level arithmetic is altered.

A note on why persistence alone is insufficient: had the counter simply tallied *any* opposing
read, BALKRISIND reaches two-in-a-row at 13:01 (12:56 SELL, 13:01 SELL) and is exited anyway. The
quality floor is what did the protective work. Both halves are required.

## Verification

Tests written from the four real cases before implementation:

1. BALKRISIND — a q34 `SELL_NOW` on a long leaves the stop at 2213.70 (the session low after entry
   was 2224.40, so the position survives to the close).
2. PNGSREVA — a stop of 452.11 with the tape at ~438 is refused by the clamp.
3. BLACKBUCK and PREMIERENE — cross-side writes refused.
4. **Regression guard:** two consecutive convicted flips still exit on `SIGNAL`.
5. **Regression guard:** a neutral `HOLD` read still trails the stop normally.

Then replay the 2026-07-30 backtest. Expected: −₹17,543 → approximately **−₹12,539**.

### Honest bound on the benefit

The +₹5,004 net is driven almost entirely by BALKRISIND (+₹6,270). PNGSREVA and BLACKBUCK
genuinely traded through their real stops and finish marginally **worse** (−₹659, −₹607). One
session and seven trades is far too small to generalise from. The case for this change is that the
current behaviour is *incorrect* — a short's risk levels must never be written onto a long — not
that it is worth ₹5,004 a day.
