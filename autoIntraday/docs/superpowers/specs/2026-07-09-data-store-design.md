# autoIntraday Phase 2: Data Store & State Model — Design

## Context

Phase 2 of `autoIntraday` (see `2026-07-09-groww-client-design.md` for the full 6-phase
system overview). Phase 1 delivered `GrowwClient` — the broker interface. Phase 2 delivers
the persistent state that ties the system together: what the hourly job did, what it
decided, which positions are open with what target/stop, and every order placed. Later
phases consume it: the orchestrator (Phase 4) reads open positions and deployed capital to
enforce pool rules and writes back decisions/orders/positions; the UI (Phase 6) reads it
all for display.

### Decisions (from brainstorming)

- **Money as `REAL` (float)**, consistent with what `GrowwClient` already returns (e.g.
  `ltp=2456.7`). No convert-on-read layer. Round only for display. Acceptable float drift
  for a paper-first educational P&L.
- **Config as a single-row typed table** (not key-value): type-safe, one obvious place to
  look; adding a setting is a migration.
- **`orders` is the durable source of truth.** Phase 1's in-memory paper-order log was
  always a placeholder; from Phase 2 on, the orchestrator records every order here.
- **Access only through a `Store` class** — no other module touches SQL.

## Architecture

A single module `store.py` exposing a `Store` class wrapping one `sqlite3` connection.
`Store(db_path)` takes the DB path as a constructor argument — a real file for the app,
`":memory:"` for tests. On init it creates the schema idempotently
(`CREATE TABLE IF NOT EXISTS`), enables `PRAGMA foreign_keys = ON`, and stamps
`PRAGMA user_version` (a hook for future migrations; version 1 for this schema). All
timestamps are ISO-8601 UTC text (`datetime.now(timezone.utc).isoformat()`). Money is
`REAL`, quantities `INTEGER`. Store methods return dataclasses (typed), not raw tuples.
Every error the module raises is a `StoreError`.

## Schema (5 tables)

### `config` — single row (`CHECK (id = 1)`)
| column | type | notes |
|---|---|---|
| id | INTEGER PK | always 1 |
| mode | TEXT | 'paper' or 'live' |
| total_pool | REAL | max ₹ deployable across all positions |
| max_open_positions | INTEGER | pool size (count cap) |
| capital_per_position | REAL | max ₹ per single position |
| is_paused | INTEGER | 0/1 — orchestrator honors this |
| updated_at | TEXT | ISO-8601 UTC |

Seeded on first init with safe defaults: `mode='paper'`, `total_pool=0`,
`max_open_positions=0`, `capital_per_position=0`, `is_paused=0`. (Zeros mean "the
orchestrator will place nothing until the user configures real limits" — a safe default.)

### `job_runs` — the hourly-job log
| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| started_at | TEXT | ISO-8601 UTC |
| finished_at | TEXT NULL | set on finish |
| status | TEXT | 'RUNNING' / 'SUCCESS' / 'FAILED' |
| mode | TEXT | 'paper' / 'live' — mode this run executed in |
| num_candidates | INTEGER | candidates screened |
| num_actions | INTEGER | orders/positions acted on |
| error | TEXT NULL | failure detail |
| summary | TEXT NULL | free-form / JSON |

### `decisions` — every scored candidate + engine decision
| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| run_id | INTEGER FK → job_runs(id) | |
| symbol | TEXT | |
| action | TEXT | 'BUY' / 'SELL' / 'HOLD' / 'SKIP' / … |
| score | REAL | engine score |
| reason | TEXT | human-readable rationale |
| entry_price | REAL NULL | |
| target_price | REAL NULL | |
| stop_loss | REAL NULL | |
| position_id | INTEGER FK → positions(id) NULL | set if the decision opened a position |
| created_at | TEXT | ISO-8601 UTC |
| raw_json | TEXT NULL | indicator snapshot for audit |

### `positions` — an intraday position, open or closed
| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| symbol | TEXT | |
| exchange | TEXT | e.g. 'NSE' |
| side | TEXT | 'LONG' / 'SHORT' |
| quantity | INTEGER | |
| entry_price | REAL | |
| target_price | REAL NULL | |
| stop_loss | REAL NULL | |
| status | TEXT | 'OPEN' / 'CLOSED' |
| entry_order_id | TEXT NULL | broker order id, e.g. 'PAPER-1' / 'LIVE-1' |
| oco_order_id | TEXT NULL | smart-order id, e.g. 'PAPER-OCO-1' |
| exit_price | REAL NULL | |
| exit_reason | TEXT NULL | 'TARGET' / 'STOP' / 'SQUARE_OFF' / 'MANUAL' |
| realized_pnl | REAL NULL | set on close |
| mode | TEXT | 'paper' / 'live' — mode that created it |
| opened_at | TEXT | ISO-8601 UTC |
| closed_at | TEXT NULL | set on close |

### `orders` — durable record of every order placed (source of truth)
| column | type | notes |
|---|---|---|
| id | INTEGER PK | |
| broker_order_id | TEXT | 'PAPER-1' / 'LIVE-1' / 'PAPER-OCO-1' … |
| position_id | INTEGER FK → positions(id) NULL | |
| symbol | TEXT | |
| transaction_type | TEXT | 'BUY' / 'SELL' |
| quantity | INTEGER | |
| order_type | TEXT | 'MARKET' / 'LIMIT' / 'OCO' |
| price | REAL NULL | |
| status | TEXT | last known: COMPLETE/PENDING/CANCELLED/ACTIVE/TRIGGERED |
| mode | TEXT | 'paper' / 'live' |
| placed_at | TEXT | ISO-8601 UTC |
| raw_json | TEXT NULL | full normalized dict from GrowwClient, for audit |

## Relationships

- `job_runs` 1—* `decisions` (a run produces many decisions)
- `decisions` 0..1 `positions` (a BUY decision opens a position; `position_id` links back)
- `positions` 1—* `orders` (entry order, OCO order, exit order all reference the position)

Foreign keys enforced via `PRAGMA foreign_keys = ON`.

## Access layer — `Store` methods

- **Config:** `get_config() -> Config`, `update_config(**fields) -> Config`
- **Runs:** `start_run(mode) -> int` (returns run_id, status 'RUNNING'),
  `finish_run(run_id, status, num_candidates=, num_actions=, error=, summary=)`
- **Decisions:** `record_decision(run_id, symbol, action, score, reason, entry_price=,
  target_price=, stop_loss=, position_id=, raw_json=) -> int`
- **Positions:** `open_position(symbol, exchange, side, quantity, entry_price,
  target_price=, stop_loss=, entry_order_id=, oco_order_id=, mode=) -> int`,
  `close_position(position_id, exit_price, exit_reason, realized_pnl)`,
  `get_open_positions() -> list[Position]`, `count_open_positions() -> int`,
  `deployed_capital() -> float` (sum of `quantity * entry_price` over OPEN positions —
  Phase 4 uses this against `total_pool`)
- **Orders:** `record_order(broker_order_id, symbol, transaction_type, quantity,
  order_type, price=, status=, mode=, position_id=, raw_json=) -> int`,
  `update_order_status(broker_order_id, status)`

Returns typed dataclasses: `Config`, `Position`, `Order`, `JobRun`, `Decision`.

## Error handling

- Constraint violations (e.g. attempting a second config row, unknown FK) and unknown-id
  updates (e.g. `update_order_status` for a missing `broker_order_id`) raise `StoreError`.
- `get_config` on a fresh DB returns the seeded default row (never `None`).

## Testing

All tests run against an in-memory DB (`Store(":memory:")`):
- schema creates cleanly and is idempotent (constructing `Store` twice on the same file
  doesn't error)
- config: seeded default present on init; `update_config` round-trips; second-row insert
  rejected
- each CRUD method round-trips; returned dataclasses carry the right values
- FK integrity: a decision/order referencing a missing parent raises `StoreError`
- aggregates: `count_open_positions()` and `deployed_capital()` compute correctly across
  a mix of OPEN and CLOSED positions (the values Phase 4 relies on for pool rules)
- `close_position` flips status to CLOSED, sets exit fields, and removes the position from
  `get_open_positions()` / `deployed_capital()`

TDD, one table + its methods per task, frequent commits.
