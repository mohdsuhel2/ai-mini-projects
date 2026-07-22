"""Launchd-schedule knowledge for the dashboard: read/edit the installed agent's
StartCalendarInterval (first cycle, last cycle, interval) and compute the next fire time.
The installed plist is the source of truth; the repo's deploy/ copy is documentation.
See docs/superpowers/specs/2026-07-20-dashboard-schedule-design.md."""
from __future__ import annotations

import os
import plistlib
import subprocess
from datetime import datetime, timedelta
from typing import Callable, Optional

from trading_calendar import IST

INSTALLED_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.autointraday.cycle.plist")
PRIMER_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.autointraday.primer.plist")
LABEL = "com.autointraday.cycle"
PRIMER_LABEL = "com.autointraday.primer"
SQUAREOFF = (15, 18)          # always scheduled, not editable from the UI
WEEKDAYS = (1, 2, 3, 4, 5)    # launchd: 1=Mon .. 5=Fri
PRIMER_OFFSET_MIN = 120       # the Claude-window primer fires this long before the first cycle


class ScheduleError(Exception):
    """Schedule read/validation/apply failed — message is user-presentable."""


def build_entries(start: tuple[int, int], last: tuple[int, int],
                  interval_min: int) -> list[dict]:
    times = []
    t = start[0] * 60 + start[1]
    last_min = last[0] * 60 + last[1]
    while t <= last_min:
        times.append((t // 60, t % 60))
        t += interval_min
    times.append(SQUAREOFF)
    return [{"Weekday": wd, "Hour": h, "Minute": m}
            for wd in WEEKDAYS for h, m in times]


def _regular_times(entries: list[dict]) -> list[tuple[int, int]]:
    times = sorted({(e["Hour"], e["Minute"]) for e in entries}
                   - {SQUAREOFF})
    return times


def read_schedule(path: str = INSTALLED_PLIST) -> dict:
    if not os.path.exists(path):
        raise ScheduleError(f"installed plist not found: {path}")
    try:
        with open(path, "rb") as f:
            d = plistlib.load(f)
        times = _regular_times(d["StartCalendarInterval"])
    except ScheduleError:
        raise
    except Exception as e:
        raise ScheduleError(f"could not parse {path}: {e}") from e
    if not times:
        raise ScheduleError(f"no regular cycle entries in {path}")
    interval = 0
    if len(times) > 1:
        interval = (times[1][0] * 60 + times[1][1]) - (times[0][0] * 60 + times[0][1])
    return {"start": times[0], "last": times[-1], "interval_min": interval}


def next_fire(path: str = INSTALLED_PLIST, now: Optional[datetime] = None) -> Optional[datetime]:
    """Earliest future scheduled fire (IST), incl. the square-off. Weekends are skipped via
    the plist's Weekday field; NSE holidays are NOT known here — the runner's guard skips
    those fires, so the caller should caption this as 'next scheduled' not 'guaranteed'."""
    try:
        with open(path, "rb") as f:
            entries = plistlib.load(f)["StartCalendarInterval"]
    except Exception:
        return None
    now = now or datetime.now(IST)
    best = None
    by_weekday: dict[int, list[tuple[int, int]]] = {}
    for e in entries:
        by_weekday.setdefault(e["Weekday"], []).append((e["Hour"], e["Minute"]))
    for day_offset in range(8):
        day = (now + timedelta(days=day_offset)).date()
        launchd_wd = day.isoweekday()          # ISO Mon=1..Sun=7 == launchd 1..5 for weekdays
        for h, m in sorted(by_weekday.get(launchd_wd, [])):
            candidate = datetime(day.year, day.month, day.day, h, m, tzinfo=IST)
            if candidate > now:
                best = candidate
                break
        if best:
            break
    return best


def validate(start: tuple[int, int], last: tuple[int, int],
             interval_min: int) -> Optional[str]:
    if not 5 <= interval_min <= 120:
        return "interval must be between 5 and 120 minutes"
    if start < (9, 15):
        return "first cycle cannot be before 09:15 (market open)"
    if last > (15, 0):
        return "last regular cycle cannot be after 15:00 (square-off runs at 15:18)"
    if (start[0] * 60 + start[1]) > (last[0] * 60 + last[1]):
        return "first cycle must be before the last cycle"
    return None


def cycle_running() -> bool:
    proc = subprocess.run(["pgrep", "-f", "run_cycle_job.py"], capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _launchctl_for(label: str) -> Callable[[list[str]], tuple[int, str, str]]:
    def _fn(args: list[str]) -> tuple[int, str, str]:
        uid = os.getuid()
        if args[0] == "bootout":
            argv = ["launchctl", "bootout", f"gui/{uid}/{label}"]
        elif args[0] == "bootstrap":
            argv = ["launchctl", "bootstrap", f"gui/{uid}", args[1]]
        else:
            raise ValueError(args)
        proc = subprocess.run(argv, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr
    return _fn


_launchctl = _launchctl_for(LABEL)
_primer_launchctl = _launchctl_for(PRIMER_LABEL)


def primer_time(start: tuple[int, int]) -> tuple[int, int]:
    """The Claude-window primer fires PRIMER_OFFSET_MIN before the first trading cycle."""
    total = max(0, start[0] * 60 + start[1] - PRIMER_OFFSET_MIN)
    return (total // 60, total % 60)


def build_primer_entries(start: tuple[int, int]) -> list[dict]:
    h, m = primer_time(start)
    return [{"Weekday": wd, "Hour": h, "Minute": m} for wd in WEEKDAYS]


def next_primer_fire(path: str = PRIMER_PLIST,
                     now: Optional[datetime] = None) -> Optional[datetime]:
    """Next scheduled primer fire (IST), or None if the primer agent isn't installed."""
    return next_fire(path, now)


def apply_primer_schedule(start: tuple[int, int], path: str = PRIMER_PLIST,
                          launchctl: Callable[[list[str]], tuple[int, str, str]]
                          = _primer_launchctl) -> bool:
    """Move the primer to fire 2h before `start` and reload it. No-op (returns False) if the
    primer agent isn't installed. EnvironmentVariables are preserved."""
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        d = plistlib.load(f)
    d["StartCalendarInterval"] = build_primer_entries(start)
    with open(path, "wb") as f:
        plistlib.dump(d, f)
    launchctl(["bootout"])
    launchctl(["bootstrap", path])
    return True


def apply_schedule(start: tuple[int, int], last: tuple[int, int], interval_min: int,
                   path: str = INSTALLED_PLIST,
                   launchctl: Callable[[list[str]], tuple[int, str, str]] = _launchctl,
                   is_cycle_running: Callable[[], bool] = cycle_running) -> str:
    err = validate(start, last, interval_min)
    if err:
        raise ScheduleError(err)
    if is_cycle_running():
        # Reloading launchd SIGKILLs a mid-flight cycle and strands a RUNNING row
        # (run 23, 2026-07-17). Skill cycles finish in ~2-3 min — just retry.
        raise ScheduleError("a cycle is running right now — wait a few minutes and retry")
    if not os.path.exists(path):
        raise ScheduleError(f"installed plist not found: {path}")
    try:
        with open(path, "rb") as f:
            d = plistlib.load(f)
        d["StartCalendarInterval"] = build_entries(start, last, interval_min)
        with open(path, "wb") as f:
            plistlib.dump(d, f)
        os.chmod(path, 0o600)                 # plist carries broker creds
    except Exception as e:
        raise ScheduleError(f"could not rewrite {path}: {e}") from e
    launchctl(["bootout"])                    # ok to fail (agent may not be loaded)
    rc, out, errout = launchctl(["bootstrap", path])
    if rc != 0:
        raise ScheduleError(f"launchctl bootstrap failed (rc={rc}): {errout.strip()[:300]}")
    # Keep the Claude-window primer 2h ahead of the new first cycle — best-effort so a missing
    # or unreloadable primer never fails the trading-schedule change. Only when editing the REAL
    # installed schedule (tests pass a temp path and must not touch the machine's primer).
    primed = False
    if path == INSTALLED_PLIST:
        try:
            primed = apply_primer_schedule(start)
        except Exception:
            pass
    n = len(build_entries(start, last, interval_min)) // len(WEEKDAYS) - 1
    ph, pm = primer_time(start)
    return (f"applied: {n} cycles/day, {start[0]:02d}:{start[1]:02d} to "
            f"{last[0]:02d}:{last[1]:02d} every {interval_min} min + 15:18 square-off"
            + (f"; primer moved to {ph:02d}:{pm:02d}" if primed else ""))
