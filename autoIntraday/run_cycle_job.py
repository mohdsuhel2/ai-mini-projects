#!/usr/bin/env python3
"""Scheduler entry point — launchd runs this once per scheduled hour. Guard-checks that the
market is open, then builds the real collaborators and runs one Orchestrator cycle, logging
the outcome. Market-closed is a clean exit 0. See
docs/superpowers/specs/2026-07-10-scheduler-design.md."""
from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
from datetime import datetime, time
from typing import Any, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_calendar import IST, is_trading_time, load_holidays

# Cycles at/after this IST time run in square-off-only mode: flatten everything, no new entries.
SQUAREOFF_AFTER = time(15, 15)
LOCK_PATH = os.path.expanduser("~/.autointraday/cycle.lock")


def is_squareoff_time(now: datetime) -> bool:
    t = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
    return t >= SQUAREOFF_AFTER


def acquire_lock(path: str = LOCK_PATH):
    """Non-blocking exclusive lock so two cycles can never run concurrently — a long cycle
    (many LLM calls) overlapping the next launchd fire would double-screen and double-order.
    Returns the open file handle (keep it referenced; the lock dies with the process) or None
    if another cycle holds it. flock, so a crashed process auto-releases."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def notify(title: str, message: str) -> None:
    """Best-effort macOS notification — cycle failures must not be silent, a failed SQUARE-OFF
    especially (positions left open into the close). Never raises."""
    try:
        safe_title = title.replace('"', "'")
        safe_msg = message.replace('"', "'")[:200]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "{safe_title}"'],
            capture_output=True, timeout=10, check=False)
    except Exception:
        pass

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
    squareoff_only = is_squareoff_time(now)
    if squareoff_only:
        log.info("square-off cycle at %s IST — flattening, no new entries", now.isoformat())
    summary = orch.run_cycle(squareoff_only=squareoff_only)
    log.info("cycle done: %s", summary)
    return summary


def _build_store(db_path: str):
    from store import Store
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return Store(db_path)


def _build_orchestrator(store):
    from groww_client import GrowwClient
    from indicators import get_indicators
    from orchestrator import Orchestrator
    from screener import get_candidates
    from engine_factory import make_decision_engine, make_screen_engine
    cfg = store.get_config()
    return Orchestrator(store=store, client=GrowwClient(mode=cfg.mode),
                        engine=make_decision_engine(use_web_search=True),
                        get_indicators=get_indicators, get_candidates=get_candidates,
                        screen_engine=make_screen_engine(use_web_search=True))


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    lock = acquire_lock()
    if lock is None:
        log.warning("another cycle is still running — skipping this fire")
        return 0
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()          # populate env for the lazily-imported tool modules
    now = datetime.now(IST)
    holidays = load_holidays(HOLIDAYS_PATH)
    try:
        summary = run_once(now, lambda: _build_store(settings.db_path), _build_orchestrator,
                           holidays)
        if summary and summary.get("errors"):
            notify("autoIntraday: square-off ERRORS",
                   f"{summary['errors']} position(s)/order(s) failed to flatten — check broker!")
        return 0
    except Exception as e:
        log.exception("cycle failed")
        notify("autoIntraday: cycle FAILED", str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
