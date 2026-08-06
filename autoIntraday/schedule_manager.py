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
OBSERVE_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.autointraday.observe.plist")
LABEL = "com.autointraday.cycle"
PRIMER_LABEL = "com.autointraday.primer"
OBSERVE_LABEL = "com.autointraday.observe"
SQUAREOFF = (15, 18)          # always scheduled, not editable from the UI
WEEKDAYS = (1, 2, 3, 4, 5)    # launchd: 1=Mon .. 5=Fri
PRIMER_OFFSET_MIN = 120       # the Claude-window primer fires this long before the first cycle

# --- Bar-close alignment -----------------------------------------------------------------
# The decision engine runs on 15m bars and DROPS the still-forming candle, so a cycle only sees
# new information once a 15m bar has closed. Yahoo buckets NSE intraday from the 09:15 open, so
# bars close at 09:30 / 09:45 / 10:00 ... Firing exactly ON a close races the provider: the bucket
# may not be published yet (the cycle then decides on a bar up to 15 min stale) or may still be
# settling its last prints and volume (understated RVOL). Fire SETTLE_MIN after the close instead.
SESSION_OPEN = (9, 15)
BAR_MINUTES = 15
SETTLE_MIN = 1
LAST_CYCLE_MAX = (15, 5)      # leaves room before Groww's auto square-off; admits the 15:01 fire


class ScheduleError(Exception):
    """Schedule read/validation/apply failed — message is user-presentable."""


def _mins(hm: tuple[int, int]) -> int:
    return hm[0] * 60 + hm[1]


def _grid(start: tuple[int, int], last: tuple[int, int],
          interval_min: int) -> list[tuple[int, int]]:
    times = []
    t, last_min = _mins(start), _mins(last)
    while t <= last_min:
        times.append((t // 60, t % 60))
        t += interval_min
    return times


def bar_close_fires(start: tuple[int, int], last: tuple[int, int]) -> list[tuple[int, int]]:
    """The fire times that sit SETTLE_MIN after each 15m bar close, inside [start, last]."""
    lo, hi = _mins(start), _mins(last)
    out = []
    close = _mins(SESSION_OPEN) + BAR_MINUTES
    while close + SETTLE_MIN <= hi:
        t = close + SETTLE_MIN
        if t >= lo:
            out.append((t // 60, t % 60))
        close += BAR_MINUTES
    return out


def bar_close_aligned(start: tuple[int, int], last: tuple[int, int],
                      interval_min: int) -> bool:
    """True when every 15m bar close in the window is followed by a cycle SETTLE_MIN later, so no
    closed bar goes un-decided and no cycle races the provider's aggregation."""
    fires = set(_grid(start, last, interval_min))
    wanted = bar_close_fires(start, last)
    return bool(wanted) and all(w in fires for w in wanted)


def build_entries(start: tuple[int, int], last: tuple[int, int],
                  interval_min: int) -> list[dict]:
    # No dedicated end-of-day square-off fire — Groww auto-squares MIS intraday positions near
    # close, so the bot just runs regular cycles and leaves any leftover to the broker.
    return [{"Weekday": wd, "Hour": h, "Minute": m}
            for wd in WEEKDAYS for h, m in _grid(start, last, interval_min)]


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
    if start < SESSION_OPEN:
        return "first cycle cannot be before 09:15 (market open)"
    if last > LAST_CYCLE_MAX:
        return (f"last regular cycle cannot be after "
                f"{LAST_CYCLE_MAX[0]:02d}:{LAST_CYCLE_MAX[1]:02d} "
                "(leave room before Groww's auto square-off)")
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
_observe_launchctl = _launchctl_for(OBSERVE_LABEL)


def observe_running() -> bool:
    proc = subprocess.run(["pgrep", "-f", "observe_job.py"], capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def read_observe_schedule(path: str = OBSERVE_PLIST) -> Optional[dict]:
    """The installed Skill Lab grid, or None when the agent is not installed."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            d = plistlib.load(f)
        times = sorted({(e["Hour"], e["Minute"]) for e in d.get("StartCalendarInterval") or []})
    except Exception as e:                                          # noqa: BLE001
        raise ScheduleError(f"could not parse {path}: {e}") from e
    if not times:
        return None
    interval = 0
    if len(times) > 1:
        interval = _mins(times[1]) - _mins(times[0])
    return {"start": times[0], "last": times[-1], "interval_min": interval,
            "fires": len(times)}


def build_observe_plist(start: tuple[int, int], last: tuple[int, int],
                        interval_min: int, repo_dir: str) -> dict:
    """A self-contained agent. It carries no broker credentials because the observer never talks
    to a broker — the one env var it needs is the config path."""
    python = os.path.join(repo_dir, ".venv", "bin", "python")
    return {
        "Label": OBSERVE_LABEL,
        "ProgramArguments": [python, os.path.join(repo_dir, "observe_job.py")],
        "WorkingDirectory": repo_dir,
        "EnvironmentVariables": {
            "AUTOINTRADAY_CONFIG": os.path.join(repo_dir, "config.yaml"),
            "HOME": os.path.expanduser("~"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
            "CLAUDE_BIN": os.environ.get("CLAUDE_BIN", "claude"),
        },
        "StandardOutPath": os.path.expanduser("~/.autointraday/observe.out.log"),
        "StandardErrorPath": os.path.expanduser("~/.autointraday/observe.err.log"),
        "RunAtLoad": False,
        "StartCalendarInterval": [{"Weekday": wd, "Hour": h, "Minute": m}
                                  for wd in WEEKDAYS
                                  for h, m in _grid(start, last, interval_min)],
    }


def apply_observe_schedule(start: tuple[int, int], last: tuple[int, int], interval_min: int,
                           repo_dir: Optional[str] = None, path: str = OBSERVE_PLIST,
                           launchctl: Callable[[list[str]], tuple[int, str, str]]
                           = _observe_launchctl,
                           is_running: Callable[[], bool] = observe_running) -> str:
    """Install/update the Skill Lab agent. Creates the plist if absent — unlike the trading agent,
    this one is expected to be set up from the UI rather than by the deploy script."""
    err = validate(start, last, interval_min)
    if err:
        raise ScheduleError(err)
    if is_running():
        raise ScheduleError("an observe pass is running right now — wait a moment and retry")
    repo = repo_dir or os.path.dirname(os.path.abspath(__file__))
    d = build_observe_plist(start, last, interval_min, repo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "wb") as f:
            plistlib.dump(d, f)
        os.chmod(path, 0o600)
    except Exception as e:                                          # noqa: BLE001
        raise ScheduleError(f"could not write {path}: {e}") from e
    launchctl(["bootout"])                    # ok to fail (agent may not be loaded)
    rc, out, errout = launchctl(["bootstrap", path])
    if rc != 0:
        raise ScheduleError(f"launchctl bootstrap failed (rc={rc}): {errout.strip()[:300]}")
    n = len(d["StartCalendarInterval"]) // len(WEEKDAYS)
    return (f"Skill Lab scheduled {start[0]:02d}:{start[1]:02d}-{last[0]:02d}:{last[1]:02d} "
            f"every {interval_min} min ({n} passes/day)")


def remove_observe_schedule(path: str = OBSERVE_PLIST,
                            launchctl: Callable[[list[str]], tuple[int, str, str]]
                            = _observe_launchctl) -> bool:
    """Unload and delete the agent. Disabling the observer should leave nothing behind."""
    if not os.path.exists(path):
        return False
    launchctl(["bootout"])
    os.remove(path)
    return True


def primer_time(start: tuple[int, int]) -> tuple[int, int]:
    """Fallback primer time when none is configured: PRIMER_OFFSET_MIN before the first cycle."""
    total = max(0, start[0] * 60 + start[1] - PRIMER_OFFSET_MIN)
    return (total // 60, total % 60)


def build_primer_entries(primer_hm: tuple[int, int]) -> list[dict]:
    h, m = primer_hm
    return [{"Weekday": wd, "Hour": h, "Minute": m} for wd in WEEKDAYS]


def next_primer_fire(path: str = PRIMER_PLIST,
                     now: Optional[datetime] = None) -> Optional[datetime]:
    """Next scheduled primer fire (IST), or None if the primer agent isn't installed."""
    return next_fire(path, now)


def apply_primer_schedule(primer_hm: tuple[int, int], path: str = PRIMER_PLIST,
                          launchctl: Callable[[list[str]], tuple[int, str, str]]
                          = _primer_launchctl) -> bool:
    """Set the primer to fire at `primer_hm` (IST) and reload it. No-op (returns False) if the
    primer agent isn't installed. EnvironmentVariables are preserved."""
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        d = plistlib.load(f)
    d["StartCalendarInterval"] = build_primer_entries(primer_hm)
    with open(path, "wb") as f:
        plistlib.dump(d, f)
    launchctl(["bootout"])
    launchctl(["bootstrap", path])
    return True


def apply_schedule(start: tuple[int, int], last: tuple[int, int], interval_min: int,
                   primer_hm: Optional[tuple[int, int]] = None,
                   path: str = INSTALLED_PLIST,
                   launchctl: Callable[[list[str]], tuple[int, str, str]] = _launchctl,
                   is_cycle_running: Callable[[], bool] = cycle_running) -> str:
    if primer_hm is None:
        primer_hm = primer_time(start)              # fallback: 2h before the first cycle
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
            primed = apply_primer_schedule(primer_hm)
        except Exception:
            pass
    n = len(build_entries(start, last, interval_min)) // len(WEEKDAYS)
    ph, pm = primer_hm
    return (f"applied: {n} cycles/day, {start[0]:02d}:{start[1]:02d} to "
            f"{last[0]:02d}:{last[1]:02d} every {interval_min} min (no square-off — Groww auto-flattens)"
            + (f"; primer at {ph:02d}:{pm:02d}" if primed else ""))
