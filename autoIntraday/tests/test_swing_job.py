import pytest

from store import Store
import swing_job


class _Client:
    def __init__(self, holdings=None, auth_error=None):
        self._holdings = holdings or []
        self._auth_error = auth_error

    def authenticate(self):
        if self._auth_error:
            raise RuntimeError(self._auth_error)

    def get_holdings(self):
        return self._holdings


class _Engine:
    """Per-stock fake: returns a HOLD verdict, or raises for symbols in `fail`."""
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def analyze_one(self, symbol, quantity=None, avg_price=None):
        self.calls.append(symbol)
        if symbol in self.fail:
            raise RuntimeError("claude timeout")
        leg = {"action": "HOLD", "conviction": 60, "target": None, "stop": None, "rationale": "x"}
        return {"swing": leg, "shortswing": leg}


def test_run_swing_per_stock_progress_and_success():
    store = Store(":memory:")
    client = _Client(holdings=[{"symbol": "RELIANCE", "quantity": 10, "avg_price": 2400.0},
                               {"symbol": "TCS", "quantity": 5, "avg_price": 3800.0}])
    engine = _Engine()
    rid = swing_job.run_swing(store, client, engine)
    run = store.latest_swing_run()
    assert run["status"] == "SUCCESS" and run["num_holdings"] == 2
    assert engine.calls == ["RELIANCE", "TCS"]                # analyzed one by one
    prog = store.swing_progress(rid)
    assert prog == {"total": 2, "done": 2, "pending": 0, "analyzing": 0, "errors": 0}
    v = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}
    assert v["RELIANCE"]["status"] == "DONE" and v["RELIANCE"]["swing_action"] == "HOLD"
    # fetched holdings were persisted for the page
    assert [h["symbol"] for h in store.get_holdings()] == ["RELIANCE", "TCS"]


def test_run_swing_one_stock_error_does_not_fail_run():
    store = Store(":memory:")
    client = _Client(holdings=[{"symbol": "A", "quantity": 1, "avg_price": 1.0},
                               {"symbol": "B", "quantity": 1, "avg_price": 1.0}])
    rid = swing_job.run_swing(store, client, _Engine(fail={"A"}))
    assert store.latest_swing_run()["status"] == "SUCCESS"    # run still SUCCESS
    prog = store.swing_progress(rid)
    assert prog["errors"] == 1 and prog["done"] == 2
    v = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}
    assert v["A"]["status"] == "ERROR" and v["B"]["status"] == "DONE"


def test_run_swing_auth_failure_marks_failed():
    store = Store(":memory:")
    rid = swing_job.run_swing(store, _Client(auth_error="no registered IPs"), _Engine())
    run = store.latest_swing_run()
    assert run["status"] == "FAILED" and "no registered IPs" in run["error"]


def test_run_swing_uses_supplied_holdings_without_auth():
    store = Store(":memory:")
    client = _Client(auth_error="should not be called")       # auth would raise
    holds = [{"symbol": "X", "quantity": 1, "avg_price": 1.0}]
    rid = swing_job.run_swing(store, client, _Engine(), holdings=holds)
    assert store.latest_swing_run()["status"] == "SUCCESS"    # supplied holdings skip auth


def test_stop_resets_analyzing_and_resume_does_only_pending():
    store = Store(":memory:")
    holds = [{"symbol": "A", "quantity": 1, "avg_price": 1.0},
             {"symbol": "B", "quantity": 1, "avg_price": 1.0},
             {"symbol": "C", "quantity": 1, "avg_price": 1.0}]
    # Simulate a run stopped mid-way: A done, B was analyzing, C never reached.
    rid = store.start_swing_run()
    store.set_swing_pid(rid, 424242)
    store.seed_swing_verdicts(rid, holds)
    leg = {"action": "HOLD", "conviction": 60, "target": None, "stop": None, "rationale": "x"}
    store.update_swing_verdict(rid, "A", "DONE", swing=leg, shortswing=leg)
    store.update_swing_verdict(rid, "B", "ANALYZING")

    pid = store.stop_swing_run(rid)
    assert pid == 424242
    assert store.latest_swing_run()["status"] == "STOPPED"
    v = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}
    assert v["B"]["status"] == "PENDING"                      # mid-flight stock reset
    assert v["A"]["status"] == "DONE"                         # done row untouched

    engine = _Engine()
    swing_job.run_swing(store, _Client(auth_error="no auth on resume"), engine,
                        resume_run_id=rid)
    assert engine.calls == ["B", "C"]                         # only the remaining, not A
    run = store.latest_swing_run()
    assert run["status"] == "SUCCESS" and run["num_holdings"] == 3   # full count, not the slice
    prog = store.swing_progress(rid)
    assert prog == {"total": 3, "done": 3, "pending": 0, "analyzing": 0, "errors": 0}


def test_analyzed_at_is_stamped_on_completion_but_not_while_pending():
    store = Store(":memory:")
    holds = [{"symbol": "A", "quantity": 1, "avg_price": 1.0},
             {"symbol": "B", "quantity": 1, "avg_price": 1.0}]
    rid = swing_job.run_swing(store, _Client(), _Engine(fail={"B"}), holdings=holds)
    v = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}
    assert v["A"]["status"] == "DONE" and v["A"]["analyzed_at"]        # DONE gets a timestamp
    assert v["B"]["status"] == "ERROR" and v["B"]["analyzed_at"]       # ERROR gets one too
    # seed a fresh PENDING row (not yet analyzed) — it must have no stamp
    store.seed_swing_verdicts(rid, [{"symbol": "C", "quantity": 1, "avg_price": 1.0}])
    c = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}["C"]
    assert c["status"] == "PENDING" and c["analyzed_at"] is None


def test_run_swing_one_reanalyzes_a_single_holding_as_its_own_run():
    store = Store(":memory:")
    store.replace_holdings([{"symbol": "RELIANCE", "quantity": 10, "avg_price": 2400.0},
                            {"symbol": "TCS", "quantity": 5, "avg_price": 3800.0}])
    engine = _Engine()
    # auth would raise — a single-stock run resolves qty/avg from the snapshot, no Groww call.
    rid = swing_job.run_swing_one(store, _Client(auth_error="x"), engine, "TCS")
    assert engine.calls == ["TCS"]                            # only the picked stock
    run = store.latest_swing_run()
    assert run["status"] == "SUCCESS" and run["num_holdings"] == 1
    v = store.get_swing_verdicts(rid)
    assert len(v) == 1 and v[0]["symbol"] == "TCS"
    assert v[0]["quantity"] == 5 and v[0]["avg_price"] == 3800.0   # looked up from snapshot


def test_run_swing_one_unknown_symbol_falls_back_to_no_qty():
    store = Store(":memory:")
    store.replace_holdings([{"symbol": "TCS", "quantity": 5, "avg_price": 3800.0}])
    engine = _Engine()
    rid = swing_job.run_swing_one(store, _Client(auth_error="x"), engine, "WIPRO")
    assert engine.calls == ["WIPRO"]
    v = store.get_swing_verdicts(rid)
    assert len(v) == 1 and v[0]["symbol"] == "WIPRO" and v[0]["quantity"] is None


def test_run_swing_one_in_place_updates_only_that_row_in_the_run():
    store = Store(":memory:")
    holds = [{"symbol": "A", "quantity": 1, "avg_price": 10.0},
             {"symbol": "B", "quantity": 2, "avg_price": 20.0}]
    # A completed batch: both DONE with an initial verdict.
    rid = swing_job.run_swing(store, _Client(), _Engine(), holdings=holds)
    assert store.latest_swing_run()["status"] == "SUCCESS"

    # Re-analyze just B, in place, in the same run.
    engine = _Engine()
    got = swing_job.run_swing_one(store, _Client(auth_error="x"), engine, "B", run_id=rid)
    assert got == rid                                        # same run, not a new one
    assert engine.calls == ["B"]                             # only B re-run
    assert store.latest_swing_run()["id"] == rid            # no new run row created
    v = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}
    assert v["B"]["status"] == "DONE" and v["B"]["quantity"] == 2   # qty from existing row
    assert v["A"]["status"] == "DONE"                       # A untouched
    assert len(v) == 2                                       # still a two-stock run


def test_run_swing_one_in_place_seeds_a_holding_added_after_the_run():
    # Regression: a stock bought after a batch run isn't in that run's verdicts. Re-analyzing it
    # in place must SEED its row (not silently no-op on a non-existent row), so newly-held stocks
    # can be analyzed into the existing run.
    store = Store(":memory:")
    holds = [{"symbol": "A", "quantity": 1, "avg_price": 10.0}]
    rid = swing_job.run_swing(store, _Client(), _Engine(), holdings=holds)   # run has only A
    # user later bought B — it's in the holdings snapshot but not in run `rid`
    store.replace_holdings(holds + [{"symbol": "B", "quantity": 7, "avg_price": 70.0}])

    engine = _Engine()
    got = swing_job.run_swing_one(store, _Client(auth_error="x"), engine, "B", run_id=rid)
    assert got == rid and engine.calls == ["B"]
    v = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}
    assert set(v) == {"A", "B"}                              # B was seeded into the run
    assert v["B"]["status"] == "DONE" and v["B"]["quantity"] == 7   # qty from holdings snapshot


def test_resume_with_nothing_pending_is_clean_success():
    store = Store(":memory:")
    holds = [{"symbol": "A", "quantity": 1, "avg_price": 1.0}]
    rid = store.start_swing_run()
    store.seed_swing_verdicts(rid, holds)
    leg = {"action": "HOLD", "conviction": 60, "target": None, "stop": None, "rationale": "x"}
    store.update_swing_verdict(rid, "A", "DONE", swing=leg, shortswing=leg)
    store.stop_swing_run(rid)

    engine = _Engine()
    swing_job.run_swing(store, _Client(auth_error="x"), engine, resume_run_id=rid)
    assert engine.calls == []                                 # nothing left to do
    assert store.latest_swing_run()["status"] == "SUCCESS"
