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
