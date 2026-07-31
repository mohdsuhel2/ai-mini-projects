# Live Intraday — a deterministic single-stock live trader

Date: 2026-07-31
Status: draft for review

## What this is

A new **Live Intraday** page in the existing dashboard, driving a rule-based trader that picks one
NSE stock from the Groww intraday screener, follows it on live data for the session, and places
real Groww orders when its rules fire.

**No LLM anywhere in this path.** Every decision is a pure function of the candles. The existing
skill-driven engines (`intraday-v1/v2/v3`) are untouched and keep running on their own schedule and
their own ledger.

Long-only for now. One position at a time. Square off by 15:15 IST.

## Constraints discovered before designing

These are load-bearing; the architecture follows from them.

1. **Groww's trade API only accepts whitelisted static IPs** (`groww_gateway/README.md`). The Mac
   is not whitelisted — every Groww call already detours through the VPS gateway at
   `76.13.241.82`. A `GrowwFeed` WebSocket **cannot be opened from the Mac**.
2. **The feed carries no candles.** `GrowwFeed` streams LTP, index value, market depth, order
   updates and position updates. There is no OHLC stream — candles must be aggregated locally from
   ticks, or seeded from the historical REST method.
3. **The feed subscribes by `exchange_token`, not trading symbol**, so an instrument-master lookup
   is required before subscribing.
4. **Historical candles**: `get_historical_candles()` gives 1-minute data, 7 days per request,
   3 months back. Enough to seed indicators at open.
5. **The gateway does not proxy historical candles today.** It exposes ltp, quote, holdings,
   positions, open orders, margin, orders (place/status/cancel), oco. Candles must be added.
6. `groww_intraday_screener.py` scrapes `groww.in/stocks/intraday` with **no auth**, so selection
   runs anywhere and needs no gateway change.

## Architecture

Three units, each independently testable, so the strategy can be proven before any live wiring.

```
  screener (no auth)          PriceFeed (pluggable)
         |                            |
         v                            v
  SymbolSelector  ------------>  LiveEngine.decide(candles, position, config) -> Signal
                                      |
                                      v
                               LiveTrader (executes, enforces safety)
                                      |
                                      v
                          GrowwClient -> gateway -> Groww
```

### 1. `live_engine.py` — the rules. Pure, no I/O.

```python
def decide(candles: list[Candle], position: Position | None, cfg: LiveConfig) -> Signal
```

Same candles in, same signal out — every rule below is unit-testable against fixed fixtures, which
is exactly what the LLM path can never offer.

- **Indicators** (computed locally from our own candles): VWAP (session-anchored), EMA 9/20,
  ATR(14), opening range (09:15–09:30), RVOL.
- **Entry (long only)** — all must hold: price reclaims VWAP from below **or** breaks the opening
  range high; the breaking candle's volume exceeds the RVOL floor; price is above EMA9; the
  computed R:R clears `min_rr`. Stop = `entry − atr_mult × ATR`, floored at the OR low.
  Target = `entry + rr_target × (entry − stop)`.
- **Exit** — whichever comes first: stop hit, target hit, close below VWAP for
  `vwap_exit_candles` consecutive candles, time stop after `max_hold_minutes` without reaching
  1R, or hard square-off at 15:15.
- **No averaging down, no pyramiding, no stop widening.** A stop only moves toward profit, and —
  learning from the 2026-07-30 post-mortem — never to within `min_stop_pct` of the live price.

### 2. `live_feed.py` — the transport, behind one interface

```python
class PriceFeed(Protocol):
    def ltp(self, symbol: str) -> float: ...
    def candles(self, symbol: str) -> list[Candle]: ...
```

Two implementations, so the engine never changes when the transport does:

- **`GatewayPollFeed` (phase 1)** — seeds from historical 1-min candles at startup, then polls
  `/v1/ltp` through the existing gateway every `poll_seconds` (default 2) and aggregates ticks into
  1-minute candles locally.
- **`GrowwWebSocketFeed` (phase 2)** — runs **on the VPS**, subscribes via `feed.subscribe_ltp()`
  and `feed.subscribe_equity_order_updates()`, aggregates the same way.

**Phase 1 ships first, deliberately.** For a strategy deciding on 1-minute candles, 2-second polling
is not materially worse than tick streaming — the WebSocket's real edge is tick-precise stop
triggering, not candle decisions. Building the engine and the transport at once would mean
debugging an unproven strategy and a new deployment simultaneously. Phase 2 is a drop-in swap.

### 3. `live_trader.py` — the loop and the safety envelope

Owns the session: select → follow → signal → order → reconcile. Enforces every invariant in the
safety section below. Persists state to the existing store under `strategy_id="live-intraday"`,
reusing the separate-ledger pattern the compare books already use, so P&L and positions never mix
with the skill engines.

**Process model.** This is a long-running loop, not a cron job — a departure from every existing
job in this repo, which launchd fires and which exit in minutes. Streamlit cannot host it: the
dashboard re-runs its script on every interaction and holds no background thread.

So `live_trader.py` runs as its own **launchd agent** (`com.autointraday.livetrader`), started once
at 09:20 and exiting at 15:20. The page never starts or stops the process. Instead:

- The page writes control flags to the DB (`armed`, `mode`, `capital`, `min_rr`, …).
- The loop reads those flags **at the top of every iteration**, so ARM/DISARM takes effect within
  one `poll_seconds` tick.
- The loop writes its state (chosen symbol, candles, indicators, current signal, position) back to
  the DB; the page renders whatever it finds there.

The two processes therefore share nothing but the database, and either can be restarted
independently. DISARM works even if the dashboard is closed, because it is a persisted flag rather
than an in-memory signal.

## Symbol selection — sticky, with an abandonment test

Pick once at 09:35 (after the opening range forms) and **hold it for the session**, per your call.
Rank the screener output on change %, volume ratio, and a price band that keeps position size sane.

Drop the symbol and re-select only when it stops being worth watching — any of:

- stopped out twice on the same name
- no valid entry setup for `abandon_after_minutes` (default 60)
- RVOL falls below the floor for 15 consecutive minutes (the move is dead)
- it goes untradeable: circuit limit, or spread wider than `max_spread_pct`

Re-selection is blocked while a position is open, and after 14:30 (too late to start a new name).

## Configuration defaults

Stored in the DB alongside the existing trading config, editable from the page. Every default below
is a **starting point to be tuned from backtest, not a claim of profitability.**

| Key | Default | Meaning |
|---|---|---|
| `armed` | `false` | Master switch; false blocks all new entries |
| `mode` | `paper` | `paper` \| `live` — live requires an explicit toggle |
| `capital_per_trade` | `30000` | Rupees committed per position |
| `poll_seconds` | `2` | Feed poll interval (phase 1) |
| `candle_minutes` | `1` | Aggregation interval the engine decides on |
| `min_rr` | `1.5` | Reject any setup below this projected reward:risk |
| `atr_mult` | `1.5` | Stop distance = this × ATR(14) |
| `rr_target` | `2.0` | Target = entry + this × risk |
| `min_stop_pct` | `0.35` | A stop may never sit closer than this % to live price |
| `rvol_floor` | `1.5` | Breaking candle must exceed this volume ratio |
| `vwap_exit_candles` | `2` | Consecutive closes below VWAP that force an exit |
| `max_hold_minutes` | `45` | Time stop if the trade has not reached 1R |
| `daily_loss_cap` | `5000` | Rupees; breach disarms for the day |
| `abandon_after_minutes` | `60` | No valid setup for this long → re-select |
| `max_spread_pct` | `0.5` | Wider than this → untradeable, re-select |
| `select_at` | `09:35` | When the symbol is chosen |
| `no_new_entry_after` | `14:30` | Latest a fresh position may open |
| `squareoff_at` | `15:15` | Hard flatten, overrides everything |

`min_stop_pct` exists because of the 2026-07-30 post-mortem, where trailed stops reached 0.07–0.17%
of entry and were taken out by noise. It is a floor the engine cannot cross.

## Gateway changes

Two additions to `groww_gateway/app.py`, matching the existing token-guarded pattern:

- **`GET /v1/candles`** — proxies `get_historical_candles()`. Required by phase 1 to seed
  indicators at open; nothing else can supply them.
- **`GET /v1/instruments`** — symbol → `exchange_token` lookup. Required by phase 2 to subscribe.

Phase 2 additionally deploys `live_trader.py` to the VPS as its own service, with the dashboard
reading its state over new status endpoints. That deployment is out of scope for this spec.

## The page

A new `st.Page("Live Intraday", url_path="live-intraday")` in the existing `st.navigation`, styled
exactly like the current pages — same tiles, same table conventions, no new visual language.

**Controls:** ARM / DISARM (the kill switch — DISARM blocks new entries and, on confirmation,
flattens), paper/live toggle, capital per trade, `min_rr`, ATR multiple, and a manual
"drop and re-select" button.

**Display:** the chosen symbol and why it was chosen, live price and candles, current indicator
values, the engine's current signal with the reason it fired or did not, the open position with
live P&L, and today's order log for this ledger.

## Safety

Everything here is a hard invariant, not a guideline. This path places real money orders with no
human in the loop.

- **Paper mode first.** Identical logic, orders simulated. Live requires an explicit toggle.
- **One position at a time**, enforced by a store-level check, not by convention.
- **Daily loss cap** — breach disarms the trader for the day.
- **Kill switch** on the page, effective within one loop iteration.
- **Reconcile against broker positions on startup**, before any decision, so a restart mid-position
  cannot double-order. Reuses the broker-first reconcile already in the codebase.
- **Hard square-off 15:15**, independent of every other rule.
- **Single-flight lock** — reuse the `fcntl` pattern from `run_cycle_job.py` so two live traders can
  never run at once.

## Testing

- Engine: fixture candle series per rule — entry fires, entry blocked on each individual gate,
  each exit path, stop-never-widens, stop-never-within-`min_stop_pct`-of-price.
- Replay: run the engine over 2026-07-30's real 1-minute bars (already captured in
  `docs/skill-improvements/2026-07-30-bars-1m.json`) and record what it would have done. A
  deterministic engine can be backtested honestly, unlike the skill path.
- Feed: candle aggregation from a synthetic tick sequence, including gaps and duplicate ticks.
- Trader: safety invariants — cap breach disarms, kill switch stops entries, reconcile prevents
  double-order, square-off fires.
- Paper-mode soak for a full session before any live toggle.

## Explicitly out of scope

Shorting. Multiple concurrent positions. Options or F&O. Any LLM involvement. Changes to the
existing skill engines, their schedule, or their ledgers.

## Open risk

The strategy rules above are a conventional VWAP/opening-range template. They are **unproven on
this book** — nothing here says they are profitable. The value of this spec is that, unlike the LLM
path, they can be backtested and unit-tested honestly before a rupee is committed. Treat the first
live session as an experiment with the daily loss cap set low.
