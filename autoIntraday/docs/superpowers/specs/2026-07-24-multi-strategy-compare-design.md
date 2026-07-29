# Multi-strategy framework + Compare-testing mode

**Date:** 2026-07-24 · **Status:** Approved (phased delivery)

## Goal
Make the trading engine strategy-agnostic so multiple intraday skills (V1, V2, future V3/AI/ML)
plug in behind one interface, and add a **Compare** mode that runs two (or more) strategies on
**identical market data** as isolated paper portfolios to objectively evaluate them before live
use. Everything else (engine, broker, scheduler, risk/position/capital mgmt, dashboard, paper/live)
stays intact. Compare-off + one strategy ⇒ **behaviour identical to today**.

## Key decisions (locked)
- **Compare data fidelity:** one shared neutral screen per cycle builds the universe; each symbol's
  indicators are fetched once; both strategies run their DECISION engine on the same names. Truly
  identical candles/indicators; compares decision logic on a shared universe.
- **Delivery:** phased with review checkpoints (P1 → P2 → P3).

## Architecture

### Strategy abstraction (`strategies.py`)
`Strategy{id, name, skill_path, model, web_search}` with `make_decision_engine()` /
`make_screen_engine()` returning the existing `SkillDecisionEngine`/`SkillScreenEngine`
parameterized by `skill_path`. A config-driven `StrategyRegistry` maps `id → Strategy`. Adding a
strategy = one config entry. A future AI/ML strategy just returns any object exposing
`.decide(symbol, indicators, position)` — the engine stays agnostic. V1 = `intraday-analyst`,
V2 = `intraday-analyst-2`.

### Per-strategy isolation (store)
Add `strategy_id TEXT NOT NULL DEFAULT 'intraday-v1'` to `job_runs`, `positions`, `decisions`,
`orders` (additive migration; legacy rows backfill to `intraday-v1`). The strategy-scoped methods
(inserts: `start_run`, `record_decision`, `open_position`, `record_order`; aggregates/lists:
`get_open_positions`, `get_pending_positions`, `committed_capital`, `deployed_capital`,
`count_open_positions`, `count_committed_positions`, `realized_pnl_since`) take
`strategy_id=DEFAULT_STRATEGY_ID`. Position-id-keyed mutations need no scoping (id is unique). The
`config` table (pool/capital/pause/mode) stays single & shared — both strategies use identical
capital *rules*, each computing its own committed/free capital from its own `strategy_id`
positions ⇒ "identical capital, isolated funds" for free.

### `ScopedStore`
A thin wrapper injecting a fixed `strategy_id` into the scoped methods (`__getattr__` delegates
everything else). The Orchestrator is handed a `ScopedStore`, so its existing `self.store.*` calls
become strategy-scoped with **near-zero orchestrator churn** and no trading-logic rewrite.

### Compare orchestration (`compare_orchestrator.py`, P2)
1. Screen once (movers both directions) → shared universe, cached for the cycle.
2. `SharedIndicators` memoizer — fetch each symbol's indicators once, hand identical JSON to all
   strategies.
3. Per strategy: a paper, `ScopedStore`-scoped Orchestrator (classic screening over the shared
   universe → per-name `decide`) manages its own positions and places paper entries into its own
   ledger.
4. Broker hard-off: paper client + explicit guard that no live-order path runs.

### Config & mode rules
`config.yaml` gains `strategies.available`, `live.strategy`, `paper.strategy`, `compare{enabled,
strategies}`. DB `config` gains `compare_enabled`, `live_strategy`, `paper_strategy`,
`compare_strategies` so the UI can toggle. Invariant: `compare.enabled ⇒ live disabled + mode
paper`. No `strategies` block ⇒ synthesize a single default strategy from today's skill config ⇒
identical current behaviour.

### Logging (P2)
Per-strategy `LoggerAdapter` prefixes every line `[intraday-v1]` / `[intraday-v2]`. Decisions are
already DB-recorded, now strategy-scoped.

### Dashboard (P3)
- Strategy selector (V1 / V2 / Comparison) on the Intraday page; V1/V2 = today's view filtered to
  that `strategy_id`; compare-off + one strategy ⇒ pixel-identical to today.
- New Comparison page: performance summary, decision table (time/symbol/V1/V2), trade comparison,
  portfolio comparison, analytics charts, auto-highlight leaderboard.
- Config screen: live/paper strategy radios + compare toggle (auto-disables live + paper-only
  warning).
- **Charts caveat:** the app avoids PyArrow (mimalloc segfault); `st.line_chart`/Altair use Arrow,
  so charts likely need self-built inline SVG (like the existing HTML tables) or a verified
  Arrow-system-pool workaround. Resolved in P2/P3.

## Phasing
- **P1** — strategy abstraction + registry + config parsing + single-strategy selection +
  `strategy_id` in store + `ScopedStore`. Backward-compatible. Tests → review.
- **P2** — compare orchestration + shared-data layer + isolated ledgers + broker-off + logging
  tags. Tests (isolation, identical-data, no-broker) → review.
- **P3** — comparison dashboard + strategy-scoped views + analytics + leaderboard + config toggles.

## Backward compatibility (hard requirement)
Compare-off + one strategy: no UI/behaviour/perf/architecture regressions. Legacy rows scoped to
`intraday-v1`; single-strategy live/paper tags trades with the configured strategy id.
