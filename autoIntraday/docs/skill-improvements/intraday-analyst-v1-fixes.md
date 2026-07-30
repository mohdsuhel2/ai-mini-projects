# intraday-analyst (skill V1) — recommended fixes

**Status:** analysis notes — NOT yet implemented. To be validated with V1's OWN live data, then
applied to `~/.claude/skills/intraday-analyst/SKILL.md`.
**Evidence source:** ⚠️ **V1 was NOT the live strategy on 2026-07-29 / 2026-07-30 — both days ran
V2.** There is therefore **no direct V1 trade evidence yet.** The items below are *inferred* from
V1's prompt (which shares V2's structure and the same known failure modes) and MUST be confirmed
against live V1 data before implementing.
**Last updated:** 2026-07-30

## What V1 already has (from its prompt)

V1 carries the same guards as V2, just organised as a 20-step engine instead of the 12-step
hierarchy:
- **Step 5 (Volume):** `volume_climax_ratio` + `blowoff_top` — "one bar ≥~2.5× avg at a new high =
  blow-off/distribution → do NOT chase (Gate H)."
- **Step 6 (Smart-money est.):** `climax_reversal` / `faded_from_high` = distribution (bearish).
- **Step 14 (Execution):** "Default to PULLBACK when trend is strong but price is extended from
  VWAP/EMA20."
- **Gate A (Direction):** `directional_bias` map; "Never buy the VWAP hold on a stock making lower
  highs / faded from a volume climax."
- **Step 13 (R:R):** R:R is NOT a hard gate.
- **Step 16 (Invalidation):** the exact kill condition.

So V1 has the same rules — and, by construction, is likely exposed to the same *reliability* gaps
seen on V2. But that is a hypothesis until V1 is run.

## Likely fixes (mirror of V2 — each UNVERIFIED for V1)

1. **Enforce anti-chase on BUY NOW (extension/climax hard pre-check).** V1 has step 5/6 + Gate A/H,
   but "do NOT chase" is a preference, not a forbidding gate. Make BUY NOW / SHORT NOW require an
   explicit `blowoff_top` / `volume_climax_ratio` / `vwap_distance_pct` / `pct_off_day_high` check
   that a bullish read cannot override — downgrade extended entries to BUY ON PULLBACK / WAIT.
   *(V2 evidence: SYRMA chase, −₹6,772. Confirm V1 does the same before changing.)*
2. **Quality score must penalise extension/climax.** V1's 100-pt model (step ~11) uses the same
   trend/volume/VWAP weights that on V2 scored two extended losers 84–87. Add an extension penalty
   so a chase scores low. *(Verify V1's scoring on real V1 chases.)*
3. **Self-reported R:R must equal `(target1−entry)/(entry−stop_loss)` from the emitted levels.**
   *(V2 emitted contradictory R:R on VGUARD/DEEPINDS/MOIL; check whether V1 does too.)*
4. **Cut an invalidated loser with a convicted SELL.** V1 has invalidation (step 16) but the same
   weak-exit risk — a broken entry should produce a decisive SELL NOW (quality+confidence ≥ the
   orchestrator's 55 exit floor), not a slow HOLD to the stop. *(V2 evidence: SYRMA/MOIL/SHK.)*

## Next step (required before implementing any of the above)

Run V1 live for a stretch (or backtest V1 on the same days V2 traded) to produce V1's own decision
log, then confirm which of items 1–4 actually manifest for V1 versus which are V2-specific. Do not
port V2's fixes blind — V1's step ordering and scoring differ enough that the same wording may land
differently.

---

*See `intraday-analyst-v2-fixes.md` for the evidence-backed detail; this file intentionally stays
lighter until V1 has its own data.*
