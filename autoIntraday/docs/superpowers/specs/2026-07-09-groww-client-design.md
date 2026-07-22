# autoIntraday: System Overview + Phase 1 (Groww API Client) Design

## Context

`autoIntraday` automates intraday trading on Groww: an hourly job (starting 11:00 AM IST,
during market hours) screens for top intraday candidates, checks existing open positions,
decides entries/exits using a rule-based scoring engine, places orders (with target +
stop-loss) via Groww's official trade API, and persists everything so a Streamlit dashboard
can show current status and job/decision history.

This is a multi-phase system. This document captures the overall phase breakdown for
context, then specifies **Phase 1: the Groww API client** in full. Later phases each get
their own spec.

### Key decisions (apply across all phases)

- **Paper trading first.** The full loop (screen → decide → "place" → track → square off)
  runs against real live prices but does not submit real orders, until validated over
  multiple days. Live trading is a later, explicit opt-in.
- **Decision-making is a self-contained Python rules/scoring engine** (ported from the
  existing `intraday-analyst` indicator logic in the sibling `StockAnalayze` project:
  VWAP, EMA 9/20/50/200, ADX, SuperTrend, RVOL, etc.) — no LLM call in the hot path. Fast,
  deterministic, backtestable.
- **Auth is TOTP-based** (not API key+secret), specifically because API key+secret requires
  daily manual re-approval, which would silently break an unattended hourly cron. TOTP has
  no expiry.
- **Runs locally** on the user's Mac via cron/launchd (not a cloud VM, for now).
- **UI is a Streamlit dashboard** reading from the shared data store — no separate frontend
  stack.

### Phase breakdown

| # | Phase | Delivers | Depends on |
|---|-------|----------|------------|
| 1 | Groww API client | Python wrapper: TOTP auth, quotes/LTP, holdings/positions, place/modify/cancel order, OCO smart orders (target+SL), order status | — |
| 2 | Data store & state model | SQLite schema: positions, orders, signals/decisions, job-run log, config | — |
| 3 | Decision engine | Rule-based scorer → BUY/SELL/HOLD + entry/target/stop, ported from existing intraday indicator logic | — |
| 4 | Orchestrator (hourly job) | Ties 1+2+3 together: fetch state → score candidates → apply pool/capital rules → paper-simulate or live-execute via OCO → persist → decide next action | 1, 2, 3 |
| 5 | Scheduler | launchd/cron: hourly 11:00–15:15 IST, trading-day/holiday guard, failure logging | 4 |
| 6 | UI dashboard | Streamlit: positions, target/SL, P&L, decision history, job-run log, pause/resume + paper/live toggle | 2 |

Recommended build order: 1 → 2 → 3 → 4 → 5 → 6. Each phase depends only on earlier phases;
the orchestrator (4) — the riskiest, highest-value piece — is built last among the "brain"
phases so the client, store, and decision logic are each independently solid first.

---

## Phase 1: Groww API Client

### Scope

A single Python module, `groww_client.py`, wrapping the official `growwapi` PyPI SDK.
Exposes a small, typed surface so every later phase talks to Groww only through this
module — if Groww changes their SDK, only this file changes.

**Out of scope for Phase 1** (belong to later phases): candidate screening/discovery logic,
position sizing/pool rules, the decision engine, the hourly orchestration loop, scheduling,
and the UI. Phase 1 is purely "can we reliably auth, read, and write to Groww."

### Architecture

`GrowwClient` is constructed with an explicit `mode`: `"paper"` or `"live"`.

- **`paper` mode**: read operations (quotes, holdings, positions, margin) call the real
  Groww API. Write operations (place/modify/cancel order, OCO) are intercepted — never sent
  to Groww — and instead simulated locally: a paper order is logged with a simulated fill
  price derived from the current LTP, and a paper order ID is returned in the same shape a
  real order response would have.
- **`live` mode**: every operation calls the real SDK, no interception.

Mode is a constructor argument, not a global/env toggle read deep inside the module — so
Phase 4's orchestrator can be paper-tested and live-tested through the identical code path,
and it's structurally impossible for a "paper" caller to accidentally fire a live order.

### Components

- **Auth** — `authenticate()`: reads `GROWW_API_KEY` and `GROWW_TOTP_SECRET` from
  environment variables (never hardcoded or committed), generates the current OTP via
  `pyotp`, exchanges it for an access token via `GrowwAPI.get_access_token(...)`, and caches
  the token in-memory for the process lifetime. Each cron run is a fresh process, so no
  cross-run token persistence is needed in Phase 1.
- **Market data** — `get_ltp(symbols: list[str])`, `get_quote(symbol: str)`: thin
  pass-through to the SDK, returning normalized dicts.
- **Portfolio** — `get_holdings()`, `get_positions()`, `get_margin()`: thin pass-through,
  normalized.
- **Orders** — `place_order(symbol, exchange, transaction_type, quantity, order_type,
  price=None, product="MIS")`, `place_oco_order(symbol, entry, target, stop_loss)`,
  `get_order_status(order_id)`, `get_smart_order_status(order_id)`,
  `cancel_order(order_id)`. In paper mode, all writes go to a local paper-order log
  (in-memory list returned to the caller in Phase 1; persisted properly once Phase 2's
  store exists) instead of calling Groww.
- **Errors** — a single `GrowwClientError` wraps SDK exceptions, HTTP errors, and
  rate-limit responses, so every caller handles exactly one exception type regardless of
  what failed underneath.

### Data flow

Caller → `GrowwClient` method → **paper**: simulate fill against current LTP + append to
paper-order log, **or live**: call SDK (respecting Groww's documented rate limits — 10
req/s orders, 10 req/s market data, 20 req/s non-trading — with basic retry/backoff on
reads) → normalized Python dict returned → caller never sees raw SDK response shapes.

### Error handling

- **Auth failure** → raise immediately, no silent retry. A cron run should fail loudly
  rather than proceed without valid credentials or place orders on stale state.
- **Transient network/rate-limit errors on reads** (quotes, holdings, order status) →
  retry a few times with backoff.
- **Any error on a write** (place/modify/cancel order, live or paper) → **no automatic
  retry.** Duplicate order submission is worse than a missed cycle. The error is logged and
  raised to the caller; Phase 4's orchestrator decides what to do on the next run.

### Testing

- Unit tests mock the SDK boundary and cover: auth success/failure, paper-mode fill
  simulation math against a given LTP, error wrapping for each error category above.
- One manual, not-CI smoke script that authenticates for real and calls only read-only
  endpoints (`get_holdings`, `get_ltp`) — safe to run against the live account with zero
  trading risk, used to confirm credentials and connectivity actually work end-to-end.

### Configuration

Required environment variables: `GROWW_API_KEY`, `GROWW_TOTP_SECRET`. No credentials are
ever written to source, logs, or the git history. A `.env.example` documents the required
variable names without values; the real `.env` is gitignored.
