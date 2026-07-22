# Scheduler (Phase 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fire `Orchestrator.run_cycle()` on the right schedule — hourly during market hours on NSE trading days — via a macOS launchd LaunchAgent, skipping closed-market times cheaply.

**Architecture:** `trading_calendar.py` decides if a given IST `datetime` is a trading time (weekday, not an NSE holiday, in-window). `run_cycle_job.py` is the entry point launchd runs: guard-check, then build the real collaborators and run one cycle, logging the outcome. A `com.autointraday.cycle.plist` LaunchAgent schedules the fires; `nse_holidays.txt` is the editable holiday list.

**Tech Stack:** Python 3.10+, standard library (`zoneinfo`, `datetime`), `pytest`. Depends on the in-repo `store.py`, `groww_client.py`, `decision_engine.py`, `indicators.py`, `screener.py`, `orchestrator.py`. macOS launchd for scheduling.

## Global Constraints

- All schedule/guard times are IST (`Asia/Kolkata` via `zoneinfo.ZoneInfo`). The machine is on IST.
- Market-closed (weekend / NSE holiday / outside 09:15–15:30) is a clean exit 0 — no store run, no LLM, no orders.
- `is_trading_time` is pure: `now` and the holiday set are injected; no clock/file access inside it.
- The runner reads `mode` (paper/live) from the store config and builds `GrowwClient(mode)` — the scheduler never hardcodes the mode.
- No credentials hardcoded anywhere.

---

### Task 1: `trading_calendar.py` — holidays + `is_trading_time`

**Files:**
- Create: `trading_calendar.py`
- Create: `nse_holidays.txt`
- Test: `tests/test_trading_calendar.py`

**Interfaces:**
- Produces: `IST` (`ZoneInfo("Asia/Kolkata")`); `load_holidays(path) -> set[str]` (ISO date strings; ignores blank lines and `#` comments; missing file → empty set); `is_trading_time(now: datetime, holidays: set[str], open_time=(9, 15), close_time=(15, 30)) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trading_calendar.py`:

```python
from datetime import datetime

from trading_calendar import IST, is_trading_time, load_holidays


def _ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


HOLIDAYS = {"2026-07-13"}   # a Monday, treated as a holiday for tests


def test_trading_weekday_in_window():
    # 2026-07-10 is a Friday
    assert is_trading_time(_ist(2026, 7, 10, 11, 0), set()) is True
    assert is_trading_time(_ist(2026, 7, 10, 9, 15), set()) is True    # open edge
    assert is_trading_time(_ist(2026, 7, 10, 15, 30), set()) is True   # close edge


def test_weekend_is_not_trading():
    # 2026-07-11 Saturday, 2026-07-12 Sunday
    assert is_trading_time(_ist(2026, 7, 11, 11, 0), set()) is False
    assert is_trading_time(_ist(2026, 7, 12, 11, 0), set()) is False


def test_holiday_is_not_trading():
    assert is_trading_time(_ist(2026, 7, 13, 11, 0), HOLIDAYS) is False   # Monday holiday
    assert is_trading_time(_ist(2026, 7, 13, 11, 0), set()) is True       # same day, no holiday


def test_outside_window_is_not_trading():
    assert is_trading_time(_ist(2026, 7, 10, 9, 0), set()) is False    # before open
    assert is_trading_time(_ist(2026, 7, 10, 15, 45), set()) is False  # after close


def test_load_holidays_parses_and_ignores_comments(tmp_path):
    p = tmp_path / "h.txt"
    p.write_text("# NSE holidays\n2026-01-26\n\n2026-10-02  \n# trailing comment\n")
    assert load_holidays(str(p)) == {"2026-01-26", "2026-10-02"}


def test_load_holidays_missing_file_is_empty():
    assert load_holidays("/no/such/file.txt") == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_trading_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading_calendar'`

- [ ] **Step 3: Implement `trading_calendar.py`**

```python
"""Trading-day/time guard for the scheduler. Pure `is_trading_time` (inject `now` + holidays)
plus a loader for the editable NSE holiday list. See
docs/superpowers/specs/2026-07-10-scheduler-design.md."""
from __future__ import annotations

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def load_holidays(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    holidays: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                holidays.add(line)
    return holidays


def is_trading_time(now: datetime, holidays: set[str],
                    open_time: tuple[int, int] = (9, 15),
                    close_time: tuple[int, int] = (15, 30)) -> bool:
    if now.weekday() >= 5:              # Sat=5, Sun=6
        return False
    if now.date().isoformat() in holidays:
        return False
    t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
    return time(*open_time) <= t <= time(*close_time)
```

- [ ] **Step 4: Create `nse_holidays.txt`**

```
# NSE trading holidays — one YYYY-MM-DD per line. Blank lines and #-comments ignored.
#
# ⚠️ INCOMPLETE — VERIFY AND COMPLETE against the official NSE holiday calendar
#    (https://www.nseindia.com/resources/exchange-communication-holidays).
#    Only the fixed-date national holidays are pre-filled below. Variable-date
#    festival holidays (Holi, Mahashivratri, Eid, Ram Navami, Good Friday,
#    Independence-related, Ganesh Chaturthi, Dussehra, Diwali/Laxmi Pujan,
#    Guru Nanak Jayanti, etc.) CHANGE EVERY YEAR and MUST be added from the
#    official list. Update this file each January for the new year.
#
# 2026 fixed-date national holidays (verify against NSE — some may fall on a
# weekend, in which case the weekday check already excludes them):
2026-01-26
2026-08-15
2026-10-02
2026-12-25
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_trading_calendar.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add trading_calendar.py nse_holidays.txt tests/test_trading_calendar.py
git commit -m "Add trading_calendar: is_trading_time + editable NSE holiday list"
```

---

### Task 2: `run_cycle_job.py` — the launchd entry point

**Files:**
- Create: `run_cycle_job.py`
- Test: `tests/test_run_cycle_job.py`

**Interfaces:**
- Consumes: `trading_calendar.is_trading_time`/`load_holidays`/`IST`; the Phase 1–4 modules.
- Produces: `DEFAULT_DB` (from `AUTOINTRADAY_DB`, default `~/.autointraday/autointraday.db`); `HOLIDAYS_PATH` (bundled `nse_holidays.txt` next to the module); `should_run(now, holidays) -> bool` (thin wrapper over `is_trading_time`); `run_once(now, store_factory, orchestrator_factory, holidays) -> dict | None` (guard → build → run, injectable factories for testing; returns the run summary or `None` when skipped); `main() -> int` (wires the real collaborators, logs, returns an exit code).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_cycle_job.py`:

```python
from datetime import datetime

import pytest

from trading_calendar import IST
from run_cycle_job import should_run, run_once


def _ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def test_should_run_true_on_trading_time():
    assert should_run(_ist(2026, 7, 10, 11, 0), set()) is True       # Friday 11:00


def test_should_run_false_off_hours_and_weekend_and_holiday():
    assert should_run(_ist(2026, 7, 10, 8, 0), set()) is False        # before open
    assert should_run(_ist(2026, 7, 11, 11, 0), set()) is False       # Saturday
    assert should_run(_ist(2026, 7, 10, 11, 0), {"2026-07-10"}) is False  # holiday


def test_run_once_skips_when_market_closed():
    called = {"built": False}

    def store_factory():
        called["built"] = True
        raise AssertionError("should not build the store when market is closed")

    def orch_factory(store):
        raise AssertionError("should not build the orchestrator when market is closed")

    result = run_once(_ist(2026, 7, 11, 11, 0), store_factory, orch_factory, set())  # Saturday
    assert result is None
    assert called["built"] is False


def test_run_once_runs_cycle_when_open():
    class FakeStore:
        pass

    class FakeOrch:
        def run_cycle(self):
            return {"run_id": 1, "status": "SUCCESS", "exits": 0, "entries": 1, "candidates": 2}

    result = run_once(_ist(2026, 7, 10, 11, 0), lambda: FakeStore(),
                      lambda store: FakeOrch(), set())
    assert result["status"] == "SUCCESS" and result["entries"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_run_cycle_job.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_cycle_job'`

- [ ] **Step 3: Implement `run_cycle_job.py`**

```python
#!/usr/bin/env python3
"""Scheduler entry point — launchd runs this once per scheduled hour. Guard-checks that the
market is open, then builds the real collaborators and runs one Orchestrator cycle, logging
the outcome. Market-closed is a clean exit 0. See
docs/superpowers/specs/2026-07-10-scheduler-design.md."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_calendar import IST, is_trading_time, load_holidays

DEFAULT_DB = os.environ.get(
    "AUTOINTRADAY_DB", os.path.expanduser("~/.autointraday/autointraday.db"))
HOLIDAYS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nse_holidays.txt")

log = logging.getLogger("autointraday.cycle")


def should_run(now: datetime, holidays: set[str]) -> bool:
    return is_trading_time(now, holidays)


def run_once(now: datetime,
             store_factory: Callable[[], Any],
             orchestrator_factory: Callable[[Any], Any],
             holidays: set[str]) -> Optional[dict]:
    """Guard, then build + run one cycle. Returns the summary, or None if market is closed."""
    if not should_run(now, holidays):
        log.info("market closed at %s IST — skipping", now.isoformat())
        return None
    store = store_factory()
    orch = orchestrator_factory(store)
    summary = orch.run_cycle()
    log.info("cycle done: %s", summary)
    return summary


def _build_store(db_path: str):
    from store import Store
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return Store(db_path)


def _build_orchestrator(store):
    from decision_engine import DecisionEngine
    from groww_client import GrowwClient
    from indicators import get_indicators
    from orchestrator import Orchestrator
    from screener import get_candidates
    cfg = store.get_config()
    return Orchestrator(store=store, client=GrowwClient(mode=cfg.mode),
                        engine=DecisionEngine(use_web_search=True),
                        get_indicators=get_indicators, get_candidates=get_candidates)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    now = datetime.now(IST)
    holidays = load_holidays(HOLIDAYS_PATH)
    try:
        run_once(now, lambda: _build_store(DEFAULT_DB), _build_orchestrator, holidays)
        return 0
    except Exception:
        log.exception("cycle failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_run_cycle_job.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add run_cycle_job.py tests/test_run_cycle_job.py
git commit -m "Add run_cycle_job scheduler entry point with market-closed guard"
```

---

### Task 3: launchd plist + install doc + README

**Files:**
- Create: `deploy/com.autointraday.cycle.plist`
- Create: `deploy/install_launchd.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `run_cycle_job.py`. Produces: no new API — deployment config + docs.

- [ ] **Step 1: Create the LaunchAgent plist**

Create `deploy/com.autointraday.cycle.plist`. NOTE the two absolute paths marked `EDIT ME`
must match the user's machine (the venv Python and the project dir); the install doc explains
this.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.autointraday.cycle</string>

    <key>ProgramArguments</key>
    <array>
        <!-- EDIT ME: absolute path to the project venv python -->
        <string>/Users/mohdsuhel/ai-mini-projects/autoIntraday/.venv/bin/python</string>
        <!-- EDIT ME: absolute path to run_cycle_job.py -->
        <string>/Users/mohdsuhel/ai-mini-projects/autoIntraday/run_cycle_job.py</string>
    </array>

    <!-- Weekdays (1=Mon .. 5=Fri) at 11:00, 12:00, 13:00, 14:00, 15:00, and a 15:15
         square-off pass. NSE holidays are handled by the runner's guard, not launchd. -->
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>15</integer></dict>
    </array>

    <key>StandardOutPath</key>
    <!-- EDIT ME: log file location -->
    <string>/Users/mohdsuhel/.autointraday/cycle.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/mohdsuhel/.autointraday/cycle.err.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

- [ ] **Step 2: Create the install doc**

Create `deploy/install_launchd.md`:

```markdown
# Installing the autoIntraday scheduler (launchd)

The LaunchAgent fires `run_cycle_job.py` hourly (11:00–15:00 IST) plus a 15:15 square-off
pass, on weekdays. NSE holidays are skipped by the runner itself. Your Mac must be awake at
those times for a run to fire.

## 1. Edit the plist paths

Open `deploy/com.autointraday.cycle.plist` and fix every line marked `EDIT ME`:
- the venv Python path (`.../autoIntraday/.venv/bin/python`),
- the `run_cycle_job.py` path,
- the two log paths (create the parent dir first: `mkdir -p ~/.autointraday`).

## 2. Set credentials for the LaunchAgent

launchd jobs do NOT inherit your shell env. Put credentials the job needs where launchd can
see them — either add a `<key>EnvironmentVariables</key>` dict to the plist with
`ANTHROPIC_API_KEY`, `GROWW_API_KEY`, `GROWW_TOTP_SECRET` (and `AUTOINTRADAY_DB`,
`INTRADAY_PYTHON`/`INTRADAY_SCRIPT`, `SCREENER_PYTHON`/`SCREENER_SCRIPT` if not default), or
authenticate a persistent credential the job can read. Never commit real keys.

## 3. Install

\`\`\`bash
mkdir -p ~/.autointraday
cp deploy/com.autointraday.cycle.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.autointraday.cycle.plist
launchctl enable gui/$(id -u)/com.autointraday.cycle
\`\`\`

## 4. Verify / test now

\`\`\`bash
launchctl print gui/$(id -u)/com.autointraday.cycle   # inspect the loaded job
launchctl kickstart -k gui/$(id -u)/com.autointraday.cycle   # run once now
tail -f ~/.autointraday/cycle.out.log ~/.autointraday/cycle.err.log
\`\`\`

Outside market hours the log shows "market closed — skipping" and the run exits cleanly.

## 5. Uninstall

\`\`\`bash
launchctl bootout gui/$(id -u)/com.autointraday.cycle
rm ~/Library/LaunchAgents/com.autointraday.cycle.plist
\`\`\`

> Start in **paper** mode (the store config default). Verify several paper cycles in the logs
> and the dashboard before switching `mode` to live — and apply the "before LIVE" hardening
> from the Phase 4 ledger first.
```

- [ ] **Step 3: Add a Phase 5 section to `README.md`**

Append to `README.md`:

```markdown
## Phase 5: Scheduler

`run_cycle_job.py` is the entry point a macOS launchd LaunchAgent runs hourly (11:00–15:00
IST + a 15:15 square-off) on trading days. `trading_calendar.py` guards weekends / NSE
holidays (`nse_holidays.txt`) / off-hours so closed-market fires exit cheaply. See
`deploy/install_launchd.md` to install and `docs/superpowers/specs/2026-07-10-scheduler-design.md`.

### Test

\`\`\`bash
.venv/bin/python -m pytest tests/test_trading_calendar.py tests/test_run_cycle_job.py -v
\`\`\`

### Run one cycle by hand (respects the market-closed guard)

\`\`\`bash
.venv/bin/python run_cycle_job.py
\`\`\`
```

- [ ] **Step 4: Confirm the full suite still passes and the runner imports**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all phases green.
Run: `.venv/bin/python -c "import ast; ast.parse(open('run_cycle_job.py').read()); print('runner parses OK')"`

- [ ] **Step 5: Commit**

```bash
git add deploy/com.autointraday.cycle.plist deploy/install_launchd.md README.md
git commit -m "Add launchd LaunchAgent, install doc, and Phase 5 README"
```

---

## Self-Review Notes

- **Spec coverage:** `is_trading_time` (weekday + holiday + window) and `load_holidays` (Task 1) · editable `nse_holidays.txt` with the verify-against-NSE note (Task 1) · `run_cycle_job` guard-then-run entry point reading `mode` from config, market-closed clean exit, exception → non-zero exit + log (Task 2) · launchd plist with weekday hourly + 15:15 square-off fires and the holiday-guard-not-in-launchd note (Task 3) · install/uninstall doc incl. the launchd-env-vars caveat and paper-first warning (Task 3). All spec sections map to a task.
- **Type consistency:** `is_trading_time(now, holidays, open_time, close_time)` and `should_run(now, holidays)`/`run_once(now, store_factory, orchestrator_factory, holidays)` signatures match across Tasks 1–2 and their tests; `IST` defined once and imported; `_build_orchestrator` uses the real Phase 1–4 signatures (`Store(db_path)`, `GrowwClient(mode=)`, `DecisionEngine(use_web_search=)`, `Orchestrator(store=, client=, engine=, get_indicators=, get_candidates=)`, `store.get_config().mode`).
- **No placeholders:** every step has complete runnable code; the plist's machine-specific absolute paths are explicitly flagged `EDIT ME` with the install doc explaining them (a deployment reality, not a code placeholder), and the holiday file's incompleteness is an explicit, documented verify-step rather than a silent gap. Expected test counts: trading_calendar 6, run_cycle_job 4.
