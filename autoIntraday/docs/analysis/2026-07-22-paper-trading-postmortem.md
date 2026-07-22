# autoIntraday — Paper-Trading Post-Mortem (21–22 Jul 2026)

Deep analysis of the paper run: what we traded, what it cost, *why* every trade lost, and the
prioritized fixes to stop the bleeding and let winners pay. Grounded in the SQLite store plus a
code audit of `orchestrator.py`, `decision_engine.py`, `engine_prompt.py`, `skill_screen_engine.py`,
`groww_client.py`.

## 1. The scoreboard

| Mode | Period | Closed | Wins | Win-rate | Net P&L | Avg loss | Worst |
|------|--------|--------|------|----------|---------|----------|-------|
| Paper | 21–22 Jul | 8 | **0** | **0%** | **−₹3,918** | −₹490 | −₹836 |
| Live | 15–20 Jul | 5 | 2 | 40% | +₹10,994 | — | — |

**The live "profit" is an illusion of health.** One trade — BEPL LONG, entry 126.2 → 131.67
(+4.33%, RR 3.62) — made **+₹10,940**, i.e. ~100% of the live P&L. Strip it out and live was
roughly breakeven. The system didn't win consistently; it happened to *hold one big winner*.
Paper never produced one — and the mechanics below are why.

## 2. Core disease: the risk:reward is destroyed *after* it's approved

The engine plans good trades. By the time they're live positions, the geometry is inverted.

| | Planned RR (LLM) | Stored RR (actual) |
|---|---|---|
| Median | **1.82** | **1.12** |
| Minimum | 1.50 | <1.0 (8 of 19 trades) |

Concrete cases (planned → stored):
- **MOL** 1.68 → **0.48** (target cut 55.75→55.31, entry raised)
- **CYIENTDLM** 1.58 → **0.31** (risked 22 to make 7)
- **M&MFIN** 2.0 → stop set **0.15 below entry** (RR 109 = a noise stop; guaranteed stop-out)
- **VIMTALABS** 1.53 → 0.69, **AYE** 2.08 → 0.94, **TVSMOTOR** 1.87 → 0.77

**Two code faults cause this:**
1. The RR gate trusts the LLM's *self-reported* `risk_reward` field and **never recomputes**
   `(target−entry)/(entry−stop)` — `orchestrator.py:121`. The model can say "RR 2.0" while the
   four numbers imply 0.4, and it passes.
2. `_with_level_margins` (`orchestrator.py:149-171`, applied at `:615` **after** the gate)
   **shaves 25% off the target** (`TARGET_MOVE_SHAVE_PCT = 25.0`) and **widens the stop**, then
   never re-checks RR. This is the exact target-compression measured above.

A book of trades that risk more than they can make loses money even at a *good* win rate. At 0%
it's a guaranteed drain.

## 3. Fatal asymmetry: hard to enter, trivial to get shaken out

- **Entry** must clear a 4-part gate: `trade_quality≥52`, `risk_reward≥1.5`, `confidence≥50`,
  valid levels (`_passes_entry_gate`, `orchestrator.py:114-124`).
- **SIGNAL exit clears nothing.** Every ~20-min cycle the engine re-runs on each open position
  (`orchestrator.py:459`); a single reverse read (`SELL_NOW` for a long) at `confidence=50`
  force-closes at market, **overriding the structural stop** (`:460-466`). 4 of 8 losses were
  SIGNAL exits — and 3 of those 4 exited *above* their stop, i.e. the trade was cut before the
  risk plan said to. The engine flip-flopped: CYIENTDLM entered at conviction **84**, exited on a
  **61**; MOL 74→44; WESTLIFE 74→42.
- **SETUP_GONE is trigger-happy.** A resting pullback order must re-pass the *entire* entry gate
  as a fresh BUY every cycle or it's cancelled (`orchestrator.py:793-802`). A pullback that hasn't
  printed yet returns `WAIT` → cancelled. LLM nondeterminism (55 one cycle, 51 the next) →
  cancelled. Result: good setups churned out, low fill rate (7 of the paper orders cancelled
  `SETUP_GONE` before filling).

Net: noise shakes trades out early, real setups rarely survive to fill, and there's no
compensating winner.

## 4. Stops: no armed broker stop, 20-minute poll, market fill

- `USE_BROKER_OCO = False` — **even in live** (`orchestrator.py:71`, `:593-602`). No stop is armed
  at the broker. Stops are enforced only when a cycle poll sees `LTP ≤ stop`, and the fill is
  booked at **LTP, not the stop price** (`orchestrator.py:417-419`). DYCL exited 451.85 against a
  455 stop — a correctly-modeled gap-through, but it means realized losses routinely exceed the
  planned 1% risk. **This is the single biggest risk for real money.**
- **No minimum stop distance.** A near-zero stop (M&MFIN, 0.04% away) makes
  `risk_qty = risk/stop_distance` explode → the position is sized to the full capital cap
  (`_size_quantity`, `orchestrator.py:127-142`) → the largest position is the one guaranteed to be
  stopped by the first tick.

## 5. No winner management (the thing that made live work is structurally blocked)

- **No partial profit-taking** — every exit closes the full quantity (`_close_position:393-404`).
- **Scale-in adds to *losers*** (`_maybe_scale_in`, `orchestrator.py:475-540`).
- **Target can ratchet the wrong way** — `_maybe_trail` moves the stop toward profit but takes the
  engine's latest `target1` unconditionally, up *or down* (`:557`), pulling a winner's target in.

The one behavior that produced the live profit — holding a winner to a multiple-R gain — is
exactly what these mechanics prevent in the general case.

## 6. Market regime & direction (open gap)

8 of 9 paper entries were LONG. Market context (NIFTY/India-VIX/trend) is **not persisted** in the
decision `raw_json`, so we can't confirm the tape from the DB — but there is **no Python trend
veto** anywhere; direction discipline lives only as prose in the prompt
(`engine_prompt.py:57-74`). If 21–22 Jul was a soft/choppy tape, ungated longs would lose as a
group with nothing to stop them. *Recommendation: log NIFTY/VIX/directional_bias per decision so
this is answerable next time.*

---

## Prioritized fixes (by expected P&L impact)

**P0 — Recompute & re-gate RR on the *actual* levels.** After `_with_level_margins`, compute
`rr = (target−entry)/(entry−stop)` (mirror for shorts) and reject if `< 1.5`; also reject if the
LLM's reported RR disagrees with geometry beyond a tolerance. Stop shaving the target 25% (or fold
the shave into the RR check so it can't push RR under the floor). — `orchestrator.py:114-124, 149-171, 615`

**P0 — Minimum stop-distance floor.** Reject or clamp any stop closer than
`max(k·ATR, ~0.4%·price)` before sizing. Kills both noise stop-outs and the oversizing blow-up. —
`orchestrator.py:114-142`

**P1 — Gate the SIGNAL exit.** Require the reverse signal to clear a conviction floor *and* two
consecutive confirming cycles, or only honor it when price has also broken a structural level.
Never let a bare confidence-50 read override the stop the trade was risked to. — `orchestrator.py:459-466`

**P1 — Loosen SETUP_GONE.** Cancel a resting order only on a genuine invalidation (opposite-side
signal, or price breaking the setup's invalidation level) — not on `WAIT` or a few-point quality
wobble. Add hysteresis / a grace count. — `orchestrator.py:793-802`

**P1 — Arm a real broker stop in live.** Turn on OCO/SL at the broker before any real capital, so
the stop isn't a 20-minute-poll market exit. — `orchestrator.py:71, 593-602`

**P2 — Winner management.** Book part at `target1` and trail the rest; make the target ratchet only
*away* from entry; stop scaling into losers. — `orchestrator.py:475-557`

**P2 — Trend veto + re-tighten the gate.** Add a Python direction veto from
`intraday_structure.directional_bias`; take longs only in a non-bearish tape. The gate was
loosened over time (quality 60→52, RR 1.8→1.5, confidence→50, `orchestrator.py:15-29`) to reduce
idle cycles — reconsider RR back to ≥1.8 now that we see the fill-time degradation. Persist market
context per decision. — `orchestrator.py:15-29`, `engine_prompt.py`

## Implemented 2026-07-22 (Path A: P0 + leverage + partial book)

Shipped in the same session, test-first (full suite 295 green):

- **Geometric R:R re-gate** — `_geometric_rr` recomputes reward:risk from the ACTUAL post-margin
  entry/stop/target; `_place_entry` rejects anything `< MIN_RISK_REWARD` (1.5). Trusting the
  LLM's self-reported number is over. (`orchestrator.py`)
- **Minimum stop-distance floor** — `MIN_STOP_DISTANCE_PCT = 0.4`; `_stop_distance_ok` rejects a
  structural stop inside noise (kills the noise stop-out + the oversizing blow-up), judged on the
  engine's pre-margin stop.
- **Target shave 25% → 10%** — the biggest R:R destroyer; early profit-taking is now the
  partial-book's job, so the target no longer needs pulling in so hard.
- **5x leverage-aware sizing** — `LEVERAGE = 5.0`; `capital_per_position`/`total_pool` are MARGIN,
  a position deploys up to 5x notional (still capped at 1% pool RUPEE risk). Margin accounting
  updated at every pool check (`_place_entry`, `_maybe_scale_in`, `_screen_and_enter`, pending
  refresh).
- **Partial profit-book** — `_maybe_book_partial`: once a position earns the quality-scaled
  profit-book return (10% on margin ≈ a 2% move at 5x, tilted ±20% by entry `trade_quality`),
  sell `PROFIT_BOOK_FRACTION` (half) and trail the runner's stop to breakeven. New position
  columns `entry_quality` / `booked_pnl` / `partial_booked`; `book_partial` + `close_position`
  fold the banked slice into lifetime realized P&L.

### Second pass (P1/P2)

- **SIGNAL-exit gate** — a reverse read now must clear conviction floors (`MIN_EXIT_QUALITY=55`,
  `MIN_EXIT_CONFIDENCE=55`) AND repeat for `EXIT_CONFIRM_CYCLES=2` consecutive cycles before it
  overrides the stop; a weak or one-off flip resets the counter (new position column
  `reverse_signal_count`). Directly targets the 4 SIGNAL-exit losses.
- **SETUP_GONE loosened** — a resting order is cancelled only when the engine flips to the
  OPPOSITE side; a plain WAIT / minor quality wobble keeps it resting (fixes the low fill rate).
- **Target ratchets only away from entry** — `_maybe_trail` never pulls a winner's target in, so
  the reward can't be shrunk mid-trade into an early exit.

### Third pass (trend + observability)

Pulled a real indicator sample from the StockAnalayze tool to confirm the schema
(`higher_timeframe.overall_bias`, `market_context.nifty/india_vix`), then:

- **Trend veto** (`TREND_VETO_ENABLED`, `_trend_blocks`) — a LONG is rejected in a bearish
  aggregate tape and a SHORT in a bullish one, keyed on `higher_timeframe.overall_bias`. Fails
  OPEN if the field is absent. Directly targets the all-long-into-a-downtape loss pattern.
- **Market context per decision** (`_market_summary`) — every entry decision (placed or vetoed)
  now records a compact tape snapshot (`tape <bias> · NIFTY <chg>% (<trend>) · VIX <regime>`),
  closing the post-mortem's "couldn't tell the regime" gap.

Still OPEN (deliberately deferred): **arm a live broker stop** (`USE_BROKER_OCO` — Groww's OCO
API is unverified/unreliable per the code notes; needs a live 1-share test, not a paper flip);
**stop scaling into losers** (`SCALE_IN_ENABLED` — kept because the user explicitly asked for
scale-in on 2026-07-20; revisit given leverage is now on).

### Why this should flip the P&L
Fixing P0 alone converts the book from "risk 1.3 to make 0.6 on every trade" to "reward ≥ risk, or
no trade." Adding the SIGNAL-exit gate and winner management lets the occasional big winner (the
live-BEPL archetype) survive long enough to pay for the small losers — which is the entire economic
model of discretionary intraday. The current system does the opposite on both sides.
