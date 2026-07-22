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
        def __init__(self):
            self.squareoff_only = None

        def run_cycle(self, squareoff_only=False):
            self.squareoff_only = squareoff_only
            return {"run_id": 1, "status": "SUCCESS", "exits": 0, "entries": 1, "candidates": 2}

    # 11:00 is a normal (non-square-off) cycle
    result = run_once(_ist(2026, 7, 10, 11, 0), lambda: FakeStore(),
                      lambda store: FakeOrch(), set())
    assert result["status"] == "SUCCESS" and result["entries"] == 1


def test_acquire_lock_prevents_concurrent_cycles(tmp_path):
    from run_cycle_job import acquire_lock
    path = str(tmp_path / "cycle.lock")
    first = acquire_lock(path)
    assert first is not None
    assert acquire_lock(path) is None       # second concurrent acquire must fail
    first.close()                            # release
    third = acquire_lock(path)
    assert third is not None                 # free again after release
    third.close()


def test_run_once_squareoff_after_1515():
    from run_cycle_job import is_squareoff_time
    assert is_squareoff_time(_ist(2026, 7, 10, 15, 18)) is True
    assert is_squareoff_time(_ist(2026, 7, 10, 14, 15)) is False

    captured = {}

    class FakeOrch:
        def run_cycle(self, squareoff_only=False):
            captured["squareoff_only"] = squareoff_only
            return {"run_id": 1, "status": "SUCCESS", "exits": 2, "entries": 0, "cancels": 1,
                    "candidates": 0}

    run_once(_ist(2026, 7, 10, 15, 18), lambda: object(), lambda store: FakeOrch(), set())
    assert captured["squareoff_only"] is True
