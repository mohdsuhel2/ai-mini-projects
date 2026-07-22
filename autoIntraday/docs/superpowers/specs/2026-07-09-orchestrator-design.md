# autoIntraday Phase 4: Orchestrator (the hourly loop) — Design

## Context

Phase 4 of `autoIntraday` (see `2026-07-09-groww-client-design.md` for the full 6-phase
system overview). This is the phase that ties everything together: it drives one trading
cycle end to end — screen candidates → decide → size → place/paper-simulate orders → manage
exits → persist state — using Phase 1 (broker client), Phase 2 (store), and Phase 3
(decision engine). Phase 5 (scheduler) will call this once an hour; Phase 4 owns what one
cycle does, not the cadence.

### Decisions (from brainstorming)

- **Cost-controlled candidate depth.** The free `groww_intraday_screener.py` movers screener
  ranks the pool cheaply; the expensive Opus-4.8 decision runs only on the top-N candidates
  that could actually be entered given free pool slots + capital. The engine also always runs
  on every OPEN position (to manage exits).
- **Paper exits via day high/low mark-to-market.** In paper mode no broker engine watches
  target/stop between hourly runs, so each cycle marks every open paper position against the
  day's high/low from the indicator data — catching an intra-hour touch, the best a paper sim
  can do on an hourly cadence.
- **Exits before entries.** Each cycle manages open positions (and frees slots/capital)
  before screening for new entries.
- **Entry gate:** enter only when the engine returns a BUY/SHORT action AND `trade_quality ≥
  60` AND `risk_reward ≥ 1.5` (the skill's "tradable" bands).
- **Everything is injected** so the whole loop is unit-testable with fakes — no network, no
  LLM, no broker in tests.

## Architecture

### `screener.py` — candidate provider
`get_candidates(direction="up", top=15, min_price=50.0, min_mcap_cr=1000.0,
runner=_default_runner) -> list[dict]`. A thin subprocess adapter (same pattern as
`indicators.py`) that runs the sibling `StockAnalayze` project's `groww_intraday_screener.py`
via its own venv by env-overridable paths (`SCREENER_PYTHON`/`SCREENER_SCRIPT`, defaulting to
the `StockAnalayze` location), captures stdout, and returns the `picks` array (each pick a
dict with at least `symbol`, `ltp`, `change_pct`). Raises `ScreenerError` (a subclass of
`DecisionEngineError`) on non-zero exit, empty output, or unparseable JSON. The subprocess
boundary is the seam tests mock.

### `orchestrator.py` — the cycle driver
`Orchestrator(store, client, engine, get_indicators, get_candidates, now_provider=...)`.
All collaborators are constructor arguments:
- `store`: a Phase 2 `Store`.
- `client`: a Phase 1 `GrowwClient` (its `mode` determines paper vs live).
- `engine`: a Phase 3 `DecisionEngine`.
- `get_indicators`: the Phase 3 `indicators.get_indicators` callable (`symbol -> dict`).
- `get_candidates`: the `screener.get_candidates` callable.
- `now_provider`: a `() -> datetime` seam (so square-off timing is testable). Defaults to
  `datetime.now(timezone.utc)` — but the authoritative session state comes from the indicator
  JSON (`session.bars_remaining` / `session.minutes_to_squareoff`), so `now_provider` is only
  a fallback.

One public method, `run_cycle() -> dict` (returns a run summary), plus private helpers
`_manage_positions`, `_screen_and_enter`, `_evaluate_and_maybe_exit`, `_should_square_off`,
`_passes_entry_gate`, `_size_quantity`, `_place_entry`, `_close_position`.

## The cycle algorithm (`run_cycle`)

1. `run_id = store.start_run(client.mode)`. `cfg = store.get_config()`.
2. **If `cfg.is_paused`:** `store.finish_run(run_id, "SUCCESS", num_candidates=0,
   num_actions=0, summary="paused")` and return. No trading.
3. `client.authenticate()`.
4. **Manage open positions** (`_manage_positions`) — for each open position from
   `store.get_open_positions()`:
   - Fetch `indicators = get_indicators(symbol)` (isolated: a failure here logs a skipped
     decision and leaves the position untouched — never crashes the cycle).
   - **Square-off check** (`_should_square_off(indicators)`): if the session is near close
     (`bars_remaining ≤ 1` or `minutes_to_squareoff ≤ ~15`), close at LTP with reason
     `SQUARE_OFF`. This takes priority over any other exit.
   - **Mark-to-market** against `target_price`/`stop_loss` using day high/low from the
     indicators: LONG → `day_high ≥ target` closes at `target` (`TARGET`); `day_low ≤ stop`
     closes at `stop` (`STOP`). SHORT mirrored (`day_low ≤ target`; `day_high ≥ stop`). If
     both target and stop appear breached in the same bar, the **stop takes precedence**
     (conservative — assume the adverse level hit first).
   - Else run `engine.decide(symbol, indicators, position=<held context>)`: a `SELL_NOW`
     (for a long) / `BUY_NOW`-to-cover (for a short) / explicit exit closes at LTP
     (`SIGNAL`); `HOLD`/anything else leaves it open. Record the decision either way.
   - Each close (`_close_position`): place the exit order via `client` (paper-simulated in
     paper mode), `store.record_order`, `store.close_position(id, exit_price, reason, pnl)`.
     P&L = `(exit − entry) × qty` for LONG, negated for SHORT.
5. **Screen for entries** (`_screen_and_enter`) only if capacity remains:
   - `free_slots = cfg.max_open_positions − store.count_open_positions()`;
     `free_capital = cfg.total_pool − store.deployed_capital()`.
   - If `free_slots ≤ 0` or `free_capital < cfg.capital_per_position` → skip entries.
   - `candidates = get_candidates(...)`; drop symbols already held; take the first
     `free_slots` (a small headroom multiple is allowed so WAIT/NO_TRADE names don't starve
     the slot — take up to `free_slots + 3`, but never open more than `free_slots`).
   - For each candidate, stopping once `free_slots` new positions are opened:
     - `indicators = get_indicators(symbol)`; `decision = engine.decide(symbol, indicators,
       position=None)`. Record the decision.
     - **Entry gate** (`_passes_entry_gate`): `action ∈ {BUY_NOW, BUY_ON_PULLBACK,
       BUY_ON_BREAKOUT, SHORT_NOW}` AND `trade_quality ≥ 60` AND `risk_reward ≥ 1.5` AND
       `entry` and `stop_loss` are present. WAIT/NO_TRADE/HOLD and low-quality/low-R:R are
       rejected (still recorded).
     - **Sizing** (`_size_quantity`): `qty = floor(cfg.capital_per_position / entry)`; skip
       if `qty < 1` or `qty × entry > free_capital`.
     - **Place** (`_place_entry`): entry order via `client.place_order`, then an OCO
       (target + stop) via `client.place_oco_order`; `store.open_position(...)` linking the
       broker order ids; `store.record_order` for both; link the decision's `position_id`.
       Decrement `free_slots`, reduce `free_capital`.
   - Any per-name failure (indicator/decision/order) is caught, recorded as a skipped
     decision with the error in `reason`, and the loop continues to the next candidate.
6. `store.finish_run(run_id, "SUCCESS", num_candidates=<screened>, num_actions=<entries +
   exits>, summary=<short text>)` and return the summary.
7. **Whole-cycle guard:** the body from step 3 is wrapped so any unhandled exception marks the
   run `FAILED` (`store.finish_run(run_id, "FAILED", error=str(e))`) and re-raises — a cycle
   fails loudly, it never half-completes silently.

## Guardrails

- **Hard caps checked before every entry:** never exceed `max_open_positions`, never let
  `deployed_capital` exceed `total_pool`, never size a position above `capital_per_position`.
- **Paper-first** is the default safety net; `mode` comes from the injected `client`.
- **`is_paused`** is an honored kill switch (step 2).
- **Fault isolation:** one name's failure never aborts the cycle; the whole cycle failing
  marks the run FAILED rather than corrupting state.
- **Writes never retry** (Phase 1 rule) — a failed order skips that name for the cycle.

## Testing

Unit tests drive `run_cycle` with fake collaborators (fake store or a real in-memory
`Store(":memory:")`, a fake client recording orders, a fake engine returning scripted
decisions, fake `get_indicators`/`get_candidates`):
- paused config → no orders placed, run finishes SUCCESS with summary "paused".
- an open long whose `day_high ≥ target` → closed at target, correct realized P&L, removed
  from open positions.
- an open long whose `day_low ≤ stop` → closed at stop; stop-takes-precedence when both
  breach.
- square-off: near close, an open position is force-closed at LTP with reason SQUARE_OFF.
- entry gate: a `BUY_NOW` with `trade_quality=80, risk_reward=2.0` opens a position; a
  `trade_quality=50` or `risk_reward=1.0` or `WAIT` does not (but is recorded).
- capital/slot caps: with 1 free slot, only 1 position opens even if 3 candidates pass; a
  candidate whose sizing exceeds free capital is skipped.
- exits-before-entries: an exit in step 4 frees a slot that step 5 can then use.
- fault isolation: a candidate whose `get_indicators` raises is skipped, recorded, and the
  run still finishes SUCCESS.
- every decision (entered, rejected, skipped) is persisted to the store.

One manual, not-CI smoke script runs a single real **paper** cycle end to end against a real
`Store` (temp DB), the real `GrowwClient` in paper mode, the real engine, and the real
screener/indicators — places no real orders — and prints the run summary.

## Out of scope for Phase 4

Scheduling / the hourly cron and trading-day/holiday guard (Phase 5), and the UI (Phase 6).
Phase 4 is one cycle: state in → decisions + paper/live orders + state out.
