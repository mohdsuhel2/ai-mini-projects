# autoIntraday

Automated intraday trading on Groww. See `docs/superpowers/specs/` for the full system
design and `docs/superpowers/plans/` for implementation plans.

## Phase 1: Groww API client

`groww_client.py` wraps the official `growwapi` SDK with a `paper`/`live` mode split —
paper mode simulates every order locally against live prices; live mode calls Groww for
real. See `docs/superpowers/specs/2026-07-09-groww-client-design.md` for the full design.

### Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # fill in GROWW_API_KEY and GROWW_TOTP_SECRET
```

### Test

```bash
.venv/bin/python -m pytest tests/test_groww_client.py -v
```

### Verify real credentials (read-only, safe to run against a live account)

```bash
export $(cat .env | xargs) && .venv/bin/python scripts/smoke_test_groww_auth.py
```

## Phase 2: Data store

`store.py` is a SQLite-backed `Store` — the only module that touches the database. It
persists config (paper/live mode, pool size, capital limits, pause flag), job runs,
decisions, positions, and orders. See
`docs/superpowers/specs/2026-07-09-data-store-design.md`.

```python
from store import Store
store = Store("autointraday.db")   # ":memory:" in tests
cfg = store.update_config(mode="paper", total_pool=100000, max_open_positions=5,
                          capital_per_position=20000)
run_id = store.start_run(mode=cfg.mode)
# ... record decisions, open/close positions, record orders ...
store.finish_run(run_id, status="SUCCESS", num_candidates=12, num_actions=2)
```

### Test

```bash
.venv/bin/python -m pytest tests/test_store.py -v
```

## Phase 3: Decision engine

`decision_engine.py` asks `claude-opus-4-8` (running the intraday-analyst 20-step engine,
with web search for same-day catalysts) for a typed `Decision` on one stock. `indicators.py`
supplies the technical indicators by running the sibling `StockAnalayze` intraday tool. See
`docs/superpowers/specs/2026-07-09-decision-engine-design.md`.

### Setup

Requires `ANTHROPIC_API_KEY` (or an `ant auth login` profile) and the sibling `StockAnalayze`
project's venv. Override the indicator tool location with `INTRADAY_PYTHON` / `INTRADAY_SCRIPT`
if it lives elsewhere.

### Test

```bash
.venv/bin/python -m pytest tests/test_decision_engine.py tests/test_indicators.py -v
```

### Verify end to end (real API call on one symbol)

```bash
.venv/bin/python scripts/smoke_test_decision.py RELIANCE
```

## Phase 4: Orchestrator

`orchestrator.py` runs one trading cycle: manage open positions (square-off / target-stop /
signal exits) → screen candidates (`screener.py`) → decide (Phase 3) → size + place paper/live
orders (Phase 1) → persist everything (Phase 2). `Orchestrator.run_cycle()` is the entry point
Phase 5's cron will call hourly. See `docs/superpowers/specs/2026-07-09-orchestrator-design.md`.

Entry screening runs in `screen_mode: skill` by default — one agentic `claude -p` call per
cycle runs the full intraday-analyst skill (screener + indicator tool via restricted Bash)
and returns the top-5 candidates; `screen_mode: classic` in config.yaml restores the
movers-screener + per-name-decision pipeline. Smoke test: `.venv/bin/python
scripts/smoke_test_skill_screen.py`.

### Test

```bash
.venv/bin/python -m pytest tests/test_orchestrator.py tests/test_screener.py -v
```

### Run one real paper cycle (no real orders)

```bash
.venv/bin/python scripts/smoke_test_cycle.py
```

## Phase 5: Scheduler

`run_cycle_job.py` is the entry point a macOS launchd LaunchAgent runs every 20 minutes from
09:45 to 12:45 IST, plus a 15:18 square-off pass, on trading days. `trading_calendar.py` guards weekends / NSE
holidays (`nse_holidays.txt`) / off-hours so closed-market fires exit cheaply. See
`deploy/install_launchd.md` to install and `docs/superpowers/specs/2026-07-10-scheduler-design.md`.

### Test

```bash
.venv/bin/python -m pytest tests/test_trading_calendar.py tests/test_run_cycle_job.py -v
```

### Run one cycle by hand (respects the market-closed guard)

```bash
.venv/bin/python run_cycle_job.py
```

## Phase 6: Dashboard

`dashboard.py` is a Streamlit UI over the SQLite store — see open positions, decisions, job
runs, and realized P&L, and control pause/resume, the capital rules, and paper/live mode.
Read-only except config; no broker/LLM calls. `dashboard_data.py` holds the pure view
functions. See `docs/superpowers/specs/2026-07-10-dashboard-design.md`.

### Run

```bash
.venv/bin/pip install -r requirements.txt      # installs streamlit
AUTOINTRADAY_DB=~/.autointraday/autointraday.db .venv/bin/streamlit run dashboard.py
```

Point `AUTOINTRADAY_DB` at the same DB the scheduler writes (default is that path). Switching
to LIVE mode requires ticking the confirmation box first. Open-position rows show the plan
(entry/target/stop); realized P&L appears on closed positions.

### Test

```bash
.venv/bin/python -m pytest tests/test_dashboard_data.py -v
```

## Decision backend: API vs Claude subscription

The decision engine can run either way, selected by the `DECISION_BACKEND` env var:

- `DECISION_BACKEND=api` (default) — calls the Anthropic **API** (`decision_engine.py`). Billed
  per token to an Anthropic API account; needs `ANTHROPIC_API_KEY`. No usage ceiling.
- `DECISION_BACKEND=claude_cli` — runs the decision through headless **`claude -p`**
  (`claude_cli_engine.py`), on your **Claude Pro/Max subscription**. Needs the `claude` CLI
  installed and logged in. **Do NOT set `ANTHROPIC_API_KEY`** in the job env — its presence
  makes `claude` bill the API instead of the subscription. Subject to your subscription's usage
  limits (an hourly multi-stock Opus loop can exhaust them — consider fewer stocks and/or a
  cheaper model); when the limit is hit the cycle fails and retries next hour rather than
  silently billing the API.

The orchestrator is identical either way — only where the reasoning runs differs.

### Verify the Claude-CLI backend (one real decision on your subscription)

```bash
DECISION_BACKEND=claude_cli .venv/bin/python scripts/smoke_test_claude_cli.py RELIANCE
```

## Configuration: config.yaml

Instead of setting many environment variables, copy `config.example.yaml` to `config.yaml`
(gitignored) and edit it — a single Spring-Boot-`application.yml`-style file for deployment
settings (DB path, decision backend + model, tool paths, and `trading_defaults` that seed the
DB on first run). Secrets use `${VAR}` placeholders, so real keys stay in your env / `.env` /
the launchd plist, never in the file.

Precedence is **env var > config.yaml > built-in default** — an env var (or a value in the
launchd plist) still overrides the file, so existing setups keep working.

```bash
cp config.example.yaml config.yaml         # then edit paths / backend / trading_defaults
export $(cat .env | xargs)                 # secrets the ${VAR} placeholders reference
.venv/bin/python scripts/init_config.py    # seed the DB trading config from the YAML (run once)
```

`AUTOINTRADAY_CONFIG=/path/to/config.yaml` points at a config file elsewhere. The scheduler
(`run_cycle_job.py`), dashboard, and smoke scripts all read it. Live trading settings
(mode/pool/caps/pause) remain owned by the DB and are edited in the dashboard — the YAML only
seeds them.
