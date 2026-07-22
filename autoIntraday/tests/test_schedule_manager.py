import plistlib
from datetime import datetime

import pytest

from schedule_manager import (ScheduleError, apply_schedule, build_entries, next_fire,
                              read_schedule, validate)
from trading_calendar import IST


def _entries_dict(entries):
    return {(e["Weekday"], e["Hour"], e["Minute"]) for e in entries}


def _write_plist(path, entries, env=None):
    d = {"Label": "com.autointraday.cycle",
         "ProgramArguments": ["/usr/bin/true"],
         "EnvironmentVariables": env or {"GROWW_API_KEY": "sekret", "PATH": "/usr/bin"},
         "StartCalendarInterval": entries}
    with open(path, "wb") as f:
        plistlib.dump(d, f)
    return str(path)


def test_build_entries_grid_and_squareoff():
    entries = build_entries((9, 45), (12, 45), 20)
    # 10 regular fires + square-off, x5 weekdays
    assert len(entries) == 55
    keys = _entries_dict(entries)
    assert (1, 9, 45) in keys and (5, 12, 45) in keys
    assert (3, 15, 18) in keys                     # square-off always present
    assert (1, 13, 5) not in keys                  # nothing past `last`


def test_read_schedule_round_trip(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20))
    s = read_schedule(p)
    assert s == {"start": (9, 45), "last": (12, 45), "interval_min": 20}


def test_read_schedule_missing_plist_raises(tmp_path):
    with pytest.raises(ScheduleError):
        read_schedule(str(tmp_path / "nope.plist"))


def test_next_fire_mid_day(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20))
    now = datetime(2026, 7, 20, 11, 30, tzinfo=IST)          # Monday
    nf = next_fire(p, now)
    assert (nf.hour, nf.minute) == (11, 45)


def test_next_fire_after_last_regular_is_squareoff(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20))
    now = datetime(2026, 7, 20, 13, 0, tzinfo=IST)
    nf = next_fire(p, now)
    assert (nf.hour, nf.minute) == (15, 18)


def test_next_fire_friday_evening_rolls_to_monday(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20))
    now = datetime(2026, 7, 17, 16, 0, tzinfo=IST)           # Friday after square-off
    nf = next_fire(p, now)
    assert nf.weekday() == 0 and (nf.hour, nf.minute) == (9, 45)
    assert nf.date().isoformat() == "2026-07-20"


def test_validate_bounds():
    assert validate((9, 45), (12, 45), 20) is None
    assert validate((9, 45), (12, 45), 3) is not None        # interval too small
    assert validate((9, 45), (12, 45), 180) is not None      # interval too big
    assert validate((9, 0), (12, 45), 20) is not None        # before 09:15
    assert validate((9, 45), (15, 30), 20) is not None       # after 15:00
    assert validate((12, 45), (9, 45), 20) is not None       # start > last


def test_apply_schedule_rewrites_only_calendar(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20),
                     env={"GROWW_API_KEY": "sekret", "HOME": "/x"})
    calls = []

    def fake_launchctl(args):
        calls.append(args)
        return 0, "", ""

    msg = apply_schedule((10, 0), (12, 0), 30, path=p, launchctl=fake_launchctl,
                         is_cycle_running=lambda: False)
    with open(p, "rb") as f:
        d = plistlib.load(f)
    assert d["EnvironmentVariables"] == {"GROWW_API_KEY": "sekret", "HOME": "/x"}   # untouched
    keys = _entries_dict(d["StartCalendarInterval"])
    assert (1, 10, 0) in keys and (1, 11, 0) in keys and (1, 12, 0) in keys
    assert (1, 9, 45) not in keys
    assert (1, 15, 18) in keys                                # square-off kept
    assert [a[0] for a in calls] == ["bootout", "bootstrap"]
    assert "5 cycles/day" in msg                              # 10:00,10:30,11:00,11:30,12:00


def test_apply_schedule_refuses_while_cycle_running(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20))
    with pytest.raises(ScheduleError, match="running"):
        apply_schedule((10, 0), (12, 0), 30, path=p, launchctl=lambda a: (0, "", ""),
                       is_cycle_running=lambda: True)


def test_apply_schedule_validation_error(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20))
    with pytest.raises(ScheduleError):
        apply_schedule((9, 0), (12, 0), 30, path=p, launchctl=lambda a: (0, "", ""),
                       is_cycle_running=lambda: False)


def test_apply_schedule_bootstrap_failure_raises(tmp_path):
    p = _write_plist(tmp_path / "a.plist", build_entries((9, 45), (12, 45), 20))

    def fake_launchctl(args):
        return (1, "", "boom") if args[0] == "bootstrap" else (0, "", "")

    with pytest.raises(ScheduleError, match="bootstrap"):
        apply_schedule((10, 0), (12, 0), 30, path=p, launchctl=fake_launchctl,
                       is_cycle_running=lambda: False)


from schedule_manager import (PRIMER_OFFSET_MIN, primer_time, build_primer_entries,
                              next_primer_fire, apply_primer_schedule)


def test_primer_time_is_two_hours_before_start():
    assert PRIMER_OFFSET_MIN == 120
    assert primer_time((9, 45)) == (7, 45)
    assert primer_time((10, 30)) == (8, 30)


def test_build_primer_entries_weekdays_only():
    entries = build_primer_entries((9, 45))
    assert len(entries) == 5
    assert all((e["Hour"], e["Minute"]) == (7, 45) for e in entries)
    assert {e["Weekday"] for e in entries} == {1, 2, 3, 4, 5}


def _write_primer_plist(path, start=(9, 45), env=None):
    d = {"Label": "com.autointraday.primer",
         "ProgramArguments": ["/usr/bin/true"],
         "EnvironmentVariables": env or {"CLAUDE_BIN": "/x/claude"},
         "StartCalendarInterval": build_primer_entries(start)}
    with open(path, "wb") as f:
        plistlib.dump(d, f)
    return str(path)


def test_next_primer_fire_monday_morning(tmp_path):
    p = _write_primer_plist(tmp_path / "primer.plist", start=(9, 45))
    now = datetime(2026, 7, 20, 6, 0, tzinfo=IST)          # Monday 06:00, before 07:45
    nf = next_primer_fire(p, now)
    assert (nf.hour, nf.minute) == (7, 45) and nf.weekday() == 0


def test_apply_primer_schedule_rewrites_and_preserves_env(tmp_path):
    p = _write_primer_plist(tmp_path / "primer.plist", start=(9, 45),
                            env={"CLAUDE_BIN": "/x/claude", "HOME": "/h"})
    calls = []
    apply_primer_schedule((10, 0), path=p, launchctl=lambda a: calls.append(a) or (0, "", ""))
    with open(p, "rb") as f:
        d = plistlib.load(f)
    assert {(e["Hour"], e["Minute"]) for e in d["StartCalendarInterval"]} == {(8, 0)}   # 2h before 10:00
    assert d["EnvironmentVariables"] == {"CLAUDE_BIN": "/x/claude", "HOME": "/h"}
    assert [a[0] for a in calls] == ["bootout", "bootstrap"]
