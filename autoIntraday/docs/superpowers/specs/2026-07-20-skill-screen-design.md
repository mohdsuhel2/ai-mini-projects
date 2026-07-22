# Skill-driven one-shot screening — design

**Date:** 2026-07-20
**Status:** approved (user, 2026-07-20)

## Problem

The bot's entry pipeline decides with `engine_prompt.py`, a ~1.5-page condensation of the
user's 347-line `intraday-analyst` skill. The condensation lacks the skill's full machinery
(reversal-watch rules, blow-off-top handling, backtested entry-mode probabilities, the
CUPID/DYCL/MEESHO lessons), and the user trusts the full skill's chat output more than the
bot's condensed per-name calls. The user wants the bot to produce candidates the way the
skill does: one "top 5 right now, with trade plans" pass, then trade or ignore each purely
on the returned scores.

## Decision (user choices during brainstorming)

1. **One top-5 call per cycle** — not per-name decisions, not a prompt swap.
2. **Claude does everything** — the headless call runs the screener and indicator tool
   itself via restricted Bash, exactly like an interactive skill session.
3. **Exits unchanged** — the per-position HOLD/exit/trailing calls and all OCO/square-off
   code stay as they are. Entries only.

## Architecture

### New module: `skill_screen_engine.py`

`SkillScreenEngine.screen(exclude_symbols: list[str]) -> list[Decision-with-symbol]`

One `claude -p` invocation per cycle:

- `--append-system-prompt`: the **full text of
  `~/.claude/skills/intraday-analyst/SKILL.md`** (read at call time, cached per process;
  loud error if missing) + a SCREENING MODE addendum:
  - Run the Groww movers screener (both `--direction up` and `down`) via the StockAnalayze
    scripts; run the indicator tool on the top names; apply the full skill methodology to
    each; rank; return the **top 5** (fewer if fewer have any edge; empty list is a valid
    answer).
  - Exclude the symbols passed in (already held/pending).
  - Emit no prose — JSON only, matching the schema.
- `--json-schema`: `SCREEN_SCHEMA` = `{candidates: [{symbol, action, confidence,
  trade_quality, entry, stop_loss, target1, risk_reward}]}` — per-candidate fields are
  exactly the existing compact `Decision` shape plus `symbol`.
- `--allowedTools`: `WebSearch` plus `Bash` **restricted to the two StockAnalayze
  invocations** (screener script, indicator script) — no other commands.
- `--model` claude-opus-4-8 (same as today), subscription via `claude_cli` (no
  ANTHROPIC_API_KEY).
- Timeout **1200 s** (agentic session; cycles may take 15–20+ min — an overrun past the
  25-min spacing just makes the overlap lock skip the next fire, as today).

Parsing reuses the `--output-format json` envelope handling (`_result_text`) from
`claude_cli_engine.py`; each candidate is validated into a `Decision` + symbol.

### Orchestrator: `_screen_and_enter`

- New mode: instead of `get_candidates()` + per-name `engine.decide()`, call
  `SkillScreenEngine.screen(exclude=held+pending symbols)` once, then for each returned
  candidate **in trade-quality order**: existing entry gate (52/50/1.5 floors, entry
  action, target1 required) → existing placement (`_place_entry`: market or resting,
  sizing, OCO) until free slots run out.
- Re-filter excluded symbols in code (belt and braces — do not trust the model to have
  excluded them).
- Book full → skip the call entirely (existing behaviour, existing log line).
- Everything after the gate is byte-for-byte today's code path.

### Config switch

`screen_mode: skill | classic` in `config.yaml` (default **skill**), env override
`SCREEN_MODE`, plumbed through `settings.py` like `DECISION_BACKEND`. `classic` keeps the
old screener + per-name path fully working as rollback. The per-name engine also stays in
use for exits regardless of mode.

## Failure handling

Same contract as today's screener failure: if the one-shot call errors, times out, returns
an empty/unparseable result, or the schema rejects — log, treat as **0 candidates**, cycle
stays SUCCESS, exits still managed. A skill-screen failure must never abort the cycle.

## Testing

- Unit: `SkillScreenEngine` with a stubbed runner — happy path, envelope unwrap, schema
  violations, empty list, timeout, missing SKILL.md.
- Orchestrator: skill-mode wiring — gate applied per candidate, quality ordering, slot
  exhaustion, exclusion re-filter, failure → 0 candidates.
- `scripts/smoke_test_skill_screen.py`: manual credentialed smoke — one real call, print
  the top-5, before the scheduler uses it.

## Out of scope

Exit/position management changes; broker/order changes; gate recalibration (floors stay
52/50/1.5 — recalibrate later from the observed score distribution if the full-skill
scoring shifts it, per the 2026-07-14 lesson); removing the classic screener code.
