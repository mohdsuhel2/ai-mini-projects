"""The detached Analyze runner: one symbol failing must not cost the rest of the run."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis_job import run_analysis, run_top5
from store import Store


def _item(symbol="KEI", **kw):
    base = {"symbol": symbol, "report": "## r", "verdict": "BUY NOW", "conviction": 70,
            "entry": 10.0, "stop": 9.0, "target1": 12.0, "target2": None, "target3": None,
            "risk_reward": 2.0, "summary": "s"}
    base.update(kw)
    return base


class _Engine:
    """Stub with the ManualSkillEngine surface the job actually uses."""

    def __init__(self, fail_on=(), picks=None):
        self.fail_on, self.picks, self.seen = set(fail_on), picks or [], []

    def run_symbol(self, symbol, position=None, resting=None):
        self.seen.append((symbol, position))
        if symbol in self.fail_on:
            raise RuntimeError("tool blew up")
        return dict(_item(symbol), raw='{"env": 1}')

    def run_top5(self, positions=None):
        self.top5_positions = positions
        return [dict(p, raw='{"env": 1}') for p in self.picks]


def _run_with(symbols):
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-analyst-2", "symbols")
    store.seed_analysis_results(rid, symbols)
    return store, rid


def test_every_symbol_is_analyzed_and_marked_done():
    store, rid = _run_with(["KEI", "PNGSREVA"])
    eng = _Engine()
    run_analysis(store, eng, rid)
    rows = {r["symbol"]: r for r in store.get_analysis_results(rid)}
    assert rows["KEI"]["status"] == "DONE" and rows["PNGSREVA"]["status"] == "DONE"
    assert rows["KEI"]["verdict"] == "BUY NOW" and rows["KEI"]["raw_json"] == '{"env": 1}'
    assert store.latest_analysis_run()["status"] == "SUCCESS"


def test_one_failure_does_not_stop_the_run():
    store, rid = _run_with(["KEI", "BOOM", "PNGSREVA"])
    run_analysis(store, _Engine(fail_on=["BOOM"]), rid)
    rows = {r["symbol"]: r for r in store.get_analysis_results(rid)}
    assert rows["BOOM"]["status"] == "ERROR" and "blew up" in rows["BOOM"]["error"]
    assert rows["KEI"]["status"] == "DONE" and rows["PNGSREVA"]["status"] == "DONE"
    assert store.latest_analysis_run()["status"] == "SUCCESS"
    assert store.analysis_progress(rid)["errors"] == 1


def test_position_context_is_passed_when_the_symbol_is_held():
    store, rid = _run_with(["KEI"])
    store.replace_broker_positions([{"symbol": "KEI", "quantity": 40, "avg_price": 1830.5,
                                     "product": "MIS"}])
    eng = _Engine()
    run_analysis(store, eng, rid)
    assert eng.seen[0][1]["quantity"] == 40


def test_flat_symbol_gets_no_position_context():
    store, rid = _run_with(["NOTHELD"])
    eng = _Engine()
    run_analysis(store, eng, rid)
    assert eng.seen[0][1] is None


def test_already_done_rows_are_skipped_on_rerun():
    store, rid = _run_with(["KEI", "PNGSREVA"])
    store.update_analysis_result(rid, "KEI", "DONE", item=_item("KEI"), raw="{}")
    eng = _Engine()
    run_analysis(store, eng, rid)
    assert [s for s, _ in eng.seen] == ["PNGSREVA"]


def test_top5_inserts_each_pick():
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-breakout", "top5")
    run_top5(store, _Engine(picks=[_item("CELLO"), _item("OMNI")]), rid)
    rows = store.get_analysis_results(rid)
    assert [r["symbol"] for r in rows] == ["CELLO", "OMNI"]
    assert store.latest_analysis_run()["status"] == "SUCCESS"
    assert store.latest_analysis_run()["num_symbols"] == 2


def test_top5_engine_failure_marks_run_failed():
    class Boom:
        def run_top5(self, positions=None):
            raise RuntimeError("screener down")
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-breakout", "top5")
    run_top5(store, Boom(), rid)
    run = store.latest_analysis_run()
    assert run["status"] == "FAILED" and "screener down" in run["error"]


def test_top5_empty_is_a_successful_run():
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-breakout", "top5")
    run_top5(store, _Engine(picks=[]), rid)
    assert store.latest_analysis_run()["status"] == "SUCCESS"
    assert store.get_analysis_results(rid) == []


class _CtxEngine(_Engine):
    """Records the extra context the job hands to the engine."""

    def __init__(self):
        super().__init__()
        self.resting = {}

    def run_symbol(self, symbol, position=None, resting=None):
        self.resting[symbol] = resting
        return dict(_item(symbol), raw='{"env": 1}')


def _armed(store, symbol, stop=95.0, target=120.0, status="ARMED"):
    o = store.create_manual_order(symbol=symbol, side="LONG", quantity=13,
                                  entry_type="MARKET", entry_price=100.0, stop=stop,
                                  target=target, mode="paper")
    store.update_manual_order(o, status=status, oco_order_id="OCO1")
    return o


def test_resting_armed_exits_are_passed_for_that_symbol():
    store, rid = _run_with(["KEI", "BSE"])
    _armed(store, "KEI")
    eng = _CtxEngine()
    run_analysis(store, eng, rid)
    assert eng.resting["KEI"]["stop"] == 95.0 and eng.resting["KEI"]["target"] == 120.0
    assert eng.resting["BSE"] is None


def test_only_armed_orders_count_as_resting():
    """A rejected, closed or still-pending order rests nothing at the broker."""
    store, rid = _run_with(["KEI"])
    for status in ("REJECTED", "CLOSED", "ENTRY_PENDING"):
        _armed(store, "KEI", status=status)
    eng = _CtxEngine()
    run_analysis(store, eng, rid)
    assert eng.resting["KEI"] is None


def test_newest_armed_order_wins_when_a_symbol_has_several():
    store, rid = _run_with(["KEI"])
    _armed(store, "KEI", stop=90.0, target=110.0)
    _armed(store, "KEI", stop=95.0, target=120.0)     # newer
    eng = _CtxEngine()
    run_analysis(store, eng, rid)
    assert eng.resting["KEI"]["stop"] == 95.0


def test_top5_receives_the_open_book():
    store = Store(":memory:")
    store.replace_broker_positions([{"symbol": "KEI", "quantity": 40, "avg_price": 100.0,
                                     "product": "MIS", "ltp": 110.0}])
    rid = store.start_analysis_run("intraday-breakout", "top5")

    class E:
        def __init__(self):
            self.seen = None

        def run_top5(self, positions=None):
            self.seen = positions
            return []
    eng = E()
    run_top5(store, eng, rid)
    assert [p["symbol"] for p in eng.seen] == ["KEI"]
