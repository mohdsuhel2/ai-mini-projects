# intraday-analyst-2 (skill V2) — recommended fixes

**Status:** analysis notes — NOT yet implemented. To be validated with more data, then applied to
`~/.claude/skills/intraday-analyst-2/SKILL.md`.
**Evidence source:** V2 was the LIVE strategy on 2026-07-29 and 2026-07-30, so every example below
is a real skill-2 decision.
**Last updated:** 2026-07-30

## Summary

The skill **already contains** the anti-chase / climax / over-extension logic — L1 "Trend
Exhaustion", L7 "Volume Quality" (blow-off/climax → do NOT chase), L8 "Breakout Validation",
Gate A (direction map), Gate F (blue-sky ceiling), Gate H (no-fill) — and the Python engine
already computes the flags to enforce it: `blowoff_top`, `volume_climax_ratio`,
`exhaustion_flags`, `vwap_distance_pct`, `pct_off_day_high`, `directional_bias`.

**The failures below are the model NOT reliably applying its own rules, plus reporting/calibration
gaps — not missing logic.** So the fixes are about *enforcement* and *calibration*, not new rules.

---

## Fix 1 — make the anti-chase check a HARD, non-overridable pre-condition for BUY_NOW / SHORT_NOW  ⟵ highest priority

**Evidence — SYRMA, 2026-07-30, −₹6,772.** Bought `BUY_NOW` @ 1448, quality 84, on the 11:40
**climax bar**: price ran to the high-of-day 1468 on ~3× normal volume, +5.4% above the day open,
then reversed the very next bar. This is exactly the "blow-off / distribution → never chase" case
the skill defines (L7, L8 Blow-off row, L1 Trend Exhaustion) — and L1 "Trend Day Up" itself says
*"prefer PULLBACK/VWAP-retest longs; don't chase extension."* The model chased anyway, letting a
bullish Market-Regime read override the volume-climax evidence.

Same pattern: ARTEMISMED, 2026-07-29 (quality 87, backtest −₹3,252) — extended `BUY_NOW`.

**Change:** before emitting `BUY NOW` (or `SHORT NOW`), the model MUST explicitly read and state
`blowoff_top`, `volume_climax_ratio`, `vwap_distance_pct`, `pct_off_day_high`. If the entry is
extended/climactic (e.g. `blowoff_top: true`, or `volume_climax_ratio ≥ ~2.5` at/near HoD, or
`vwap_distance_pct` beyond a threshold), the ONLY permitted longs are **BUY ON PULLBACK / BUY ON
VWAP RETEST / WAIT** — `BUY NOW` is **forbidden**. A bullish Market-Regime (L1) or "strong tape"
read must NOT override this gate (on SYRMA it did). Word it as a hard gate with a required
check-and-state step, not a soft "do not chase" preference.

## Fix 2 — the trade-quality score must PENALISE extension / climax

**Evidence.** SYRMA (climax chase) scored **quality 84**; ARTEMISMED (extended) **87** — both
losers. TIL (the biggest winner of the week, +₹5,695) scored **73**. The 100-pt quality model is
not docking for over-extension, so it *rewards* the exact entries that lose and *under-rates* the
clean ones.

**Change:** add an explicit extension/climax penalty to the score — a large negative when a
`BUY NOW` sits at high `vwap_distance_pct` / `volume_climax_ratio ≥ 2.5` / `blowoff_top`. A
climax-chase entry should score LOW (ideally below the entry floor), not 84. Calibrate so quality
tracks *entry location* (pullback-into-strength high, chase-the-spike low), not just trend
direction.

## Fix 3 — self-reported R:R must match the emitted levels

**Evidence.** The `risk_reward` field frequently contradicts the stock's own entry/stop/target
geometry: VGUARD claimed **1.71** (real geometry 0.32), DEEPINDS **1.74** (0.37), MOIL **2.04**
(0.71). (Some were honest — SYRMA reported 1.95 and the geometry is 1.95.) The orchestrator
recomputes geometric R:R and rejects the mismatched ones, so the model wastes candidates on numbers
its own levels don't support.

**Change:** compute `risk_reward = (target1 − entry) / (entry − stop_loss)` exactly from the emitted
levels and report THAT — never a number that contradicts the geometry. Keeping R:R a *soft* gate is
fine (per L9); the reported value just has to be honest.

> **System note (not a skill-only fix):** L9 states "R:R is NOT a hard gate" (A/B-disproven
> 2026-07-24), but the orchestrator hard-gates *geometric* R:R at 1.5 (`MIN_RISK_REWARD`). That is a
> skill-vs-plumbing conflict. The `rr_gate_enabled` and `rr_gate_pre_margin` config knobs now exist
> to reconcile it — decide the intended policy and align the skill wording with the plumbing.

## Fix 4 — cut an invalidated LOSER with a convicted SELL; don't ride HOLDs to the stop

**Evidence.** SYRMA — after the bad entry the skill HELD as quality fell 84 → 73 → 66 → 57 → 45 and
**never issued a SELL**; it rode to the −₹6,772 stop. MOIL — it did flip to `SELL_NOW`, but at
quality **38**, below the orchestrator's exit floor (55), so it was ignored. SHK, 2026-07-29 —
repeated `SELL_NOW` that never cleared the 2-consecutive-convicted rule.

**Change:** Gate K covers *letting winners run*. Add the mirror rule for *cutting a broken thesis*:
when the entry thesis is invalidated post-fill (climax reversal after a chase, loses VWAP,
lower-highs distribution), emit a **convicted `SELL NOW`** — quality AND confidence high enough to
clear the exit floor (≥ 55) — not a weak HOLD or a low-conviction sell. A chase that immediately
reverses should produce a decisive exit, not a slow bleed to the stop.

---

## Priority order

1. **Fix 1** (hard anti-chase gate) and **Fix 2** (score penalises extension) — same root cause
   (chasing extension); together they would have prevented the two biggest losers (SYRMA, ARTEMISMED).
2. **Fix 4** (decisive exit on invalidation) — limits the damage when an entry is still wrong.
3. **Fix 3** (honest R:R reporting) — mostly caught downstream by the orchestrator, lower urgency.

## Open validation before implementing

- Confirm the engine's `exhaustion_flags` / `blowoff_top` / `volume_climax_ratio` were actually
  populated and non-trivial on the SYRMA 11:40 snapshot (i.e. the data WAS there and the model
  ignored it, vs the flag was absent/late). If the flag was absent, this becomes an engine/data fix
  instead of a prompt fix.
- Gather more losing-entry examples across more days before finalising the score-penalty weights.
