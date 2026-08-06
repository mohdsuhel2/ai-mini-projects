#!/usr/bin/env python3
"""Skill Lab scheduler entry point — launchd runs this on its own grid.

Deliberately separate from run_cycle_job.py: its own lock, its own plist, its own failure modes.
A shadow run must never be able to delay, block or crash the cycle that places real orders, and
disabling it must never require touching the trading schedule.

Market-closed is a clean exit 0.
"""
from __future__ import annotations

import fcntl
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_calendar import IST, is_trading_time, load_holidays

LOCK_PATH = os.path.expanduser("~/.autointraday/observe.lock")
LOG_PATH = os.path.expanduser("~/.autointraday/observe.log")


def acquire_lock(path: str = LOCK_PATH):
    """Its OWN lock file — sharing the cycle's would let a slow shadow pass block a real trading
    cycle, which inverts the priority this whole feature is built around."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stderr)])
    log = logging.getLogger("autointraday.observe.job")

    lock = acquire_lock()
    if lock is None:
        log.info("another observe pass is running — skipping this fire")
        return 0

    now = datetime.now(IST)
    if not is_trading_time(now, load_holidays()):
        log.info("market closed at %s — nothing to observe", now.strftime("%Y-%m-%d %H:%M"))
        return 0

    from observe import run_once
    from observe_store import ObserveStore

    store = ObserveStore()
    try:
        from indicators import get_indicators
        from screener import get_candidates

        def open_symbols():
            from store import Store
            s = Store()
            try:
                return [p.symbol for p in s.get_open_positions()]
            finally:
                pass

        out = run_once(store, get_indicators=get_indicators,
                       get_candidates=get_candidates, get_open_symbols=open_symbols)
        log.info("observe %s: %s", out.get("status"),
                 out.get("reason") or f"{out.get('calls')} calls, {out.get('errors')} errors, "
                                      f"skills={out.get('skills')} symbols={out.get('symbols')}")
        return 0
    except Exception:                                               # noqa: BLE001
        log.exception("observe pass failed")
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
