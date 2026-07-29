"""Phase 2: compare orchestration — shared identical data, isolated per-strategy paper ledgers,
broker hard-off, and strategy-tagged logging."""
import logging

import pytest

from compare_orchestrator import CompareOrchestrator, SharedMarketData
from decision_engine import Decision
from store import Store
from strategies import compare_ledger_id

V1, V2 = compare_ledger_id("intraday-v1"), compare_ledger_id("intraday-v2")


# ---- fakes ---------------------------------------------------------------------------------

class _FakeEngine:
    def __init__(self, decision):
        self.decision = decision
        self.seen = []                       # (symbol, indicators-object-id) it was handed
        self.strategy_context = []           # which strategy's context was active during decide

    def decide(self, symbol, indicators, position=None):
        from compare_orchestrator import _current_strategy
        self.seen.append((symbol, id(indicators)))
        self.strategy_context.append(_current_strategy.get())
        return self.decision


class _FakeBulkEngine:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []                      # symbols per call
        self.items = []                      # full items per call (to check identical objects)

    def decide_many(self, items):
        self.calls.append([it["symbol"] for it in items])
        self.items.append(items)
        return {it["symbol"]: self.decision for it in items}


class _FakeStrategy:
    def __init__(self, sid, decision, bulk=True):
        self.id = sid
        self.name = sid
        self.engine = _FakeEngine(decision)
        self.bulk_engine = _FakeBulkEngine(decision) if bulk else None

    def make_decision_engine(self):
        return self.engine

    def make_bulk_engine(self):
        return self.bulk_engine


class _PaperClient:
    mode = "paper"

    def __init__(self):
        self.orders = []
        self.oco = []

    def authenticate(self): pass
    def ensure_ready(self): pass

    def place_order(self, **kw):
        self.orders.append(kw)
        return {"order_id": f"PAPER-{len(self.orders)}", "status": "COMPLETE", "mode": "paper"}

    def place_oco_order(self, **kw):
        self.oco.append(kw)
        return {"order_id": f"OCO-{len(self.oco)}", "status": "ACTIVE", "mode": "paper"}

    def get_positions(self): return []
    def get_open_orders(self): return []
    def cancel_oco_order(self, oid): return {"order_id": oid, "status": "CANCELLED"}


class _LiveClient(_PaperClient):
    mode = "live"


def _buy(entry=100.0, stop=98.0, target=110.0):
    return Decision(action="BUY_NOW", confidence=75, trade_quality=80, entry=entry,
                    stop_loss=stop, target1=target, risk_reward=2.0, raw_response="{}")


def _wait():
    return Decision(action="WAIT", confidence=30, trade_quality=30, entry=None, stop_loss=None,
                    target1=None, risk_reward=None, raw_response="{}")


def _indic(symbol="RELIANCE", last=100.0):
    return {"symbol": symbol, "price": {"last": last, "day_high": last, "day_low": last},
            "session": {"bars_remaining": 5, "minutes_to_squareoff": 120}}


def _store():
    s = Store(":memory:")
    s.update_config(mode="paper", total_pool=100000.0, max_open_positions=3,
                    capital_per_position=10000.0, is_paused=False)
    return s


def _cands(*syms):
    return [{"symbol": x} for x in syms]


# ---- SharedMarketData ----------------------------------------------------------------------

def test_shared_market_data_fetches_once_and_hands_identical_objects():
    ind_calls = {"n": 0}
    cand_calls = {"n": 0}

    def gi(sym):
        ind_calls["n"] += 1
        return _indic(sym)

    def gc(direction="up", top=None):
        cand_calls["n"] += 1
        return _cands("A", "B")

    shared = SharedMarketData(gi, gc, top=8)
    a1, a2 = shared.indicators("RELIANCE"), shared.indicators("RELIANCE")
    assert a1 is a2 and ind_calls["n"] == 1            # fetched once, same object reused
    shared.indicators("TCS")
    assert ind_calls["n"] == 2
    up1, up2 = shared.candidates("up"), shared.candidates("up")
    assert up1 is up2 and cand_calls["n"] == 1
    shared.candidates("down")
    assert cand_calls["n"] == 2


# ---- isolation + identical data ------------------------------------------------------------

def test_strategies_get_identical_data_but_isolated_ledgers():
    store = _store()
    v1 = _FakeStrategy("intraday-v1", _buy())      # v1 BUYs
    v2 = _FakeStrategy("intraday-v2", _wait())     # v2 WAITs
    co = CompareOrchestrator(
        store, [v1, v2], get_indicators=lambda s: _indic(s),
        get_candidates=lambda direction="up", top=None: _cands("RELIANCE") if direction == "up" else [],
        client_factory=_PaperClient)
    summary = co.run_cycle()
    # isolated COMPARE ledgers (cmp:*), separate from any live/paper ledger of the same strategy
    assert store.count_open_positions(V1) == 1
    assert store.count_open_positions(V2) == 0
    assert store.count_open_positions("intraday-v1") == 0     # base ledger untouched by compare
    assert summary["mode"] == "compare" and summary["strategies"]["intraday-v1"]["entries"] == 1
    assert summary["strategies"]["intraday-v2"]["entries"] == 0
    # identical data: both strategies' BULK engines were handed the SAME indicator object for
    # RELIANCE (fetched once and shared)
    v1_obj = {it["symbol"]: id(it["indicators"]) for it in v1.bulk_engine.items[0]}["RELIANCE"]
    v2_obj = {it["symbol"]: id(it["indicators"]) for it in v2.bulk_engine.items[0]}["RELIANCE"]
    assert v1_obj == v2_obj                              # same id() -> same shared object


def test_opposite_decisions_are_both_valid_and_isolated():
    store = _store()
    v1 = _FakeStrategy("intraday-v1", _buy())
    v2 = _FakeStrategy("intraday-v2", _buy(entry=100.0, stop=97.0, target=112.0))
    CompareOrchestrator(
        store, [v1, v2], get_indicators=lambda s: _indic(s),
        get_candidates=lambda direction="up", top=None: _cands("RELIANCE") if direction == "up" else [],
        client_factory=_PaperClient).run_cycle()
    # both entered — one per compare ledger, never sharing a position row
    p1 = store.get_open_positions(V1)
    p2 = store.get_open_positions(V2)
    assert len(p1) == 1 and len(p2) == 1 and p1[0].id != p2[0].id


# ---- broker hard-off -----------------------------------------------------------------------

def test_compare_uses_one_bulk_decide_per_strategy_over_shared_universe():
    # The fast path: each strategy decides the WHOLE shared universe in ONE bulk call (not per-name),
    # and the resulting decisions drive entries. Both strategies get the same universe.
    store = _store()
    v1 = _FakeStrategy("intraday-v1", _buy())
    v2 = _FakeStrategy("intraday-v2", _wait())
    CompareOrchestrator(
        store, [v1, v2], get_indicators=lambda s: _indic(s),
        get_candidates=lambda direction="up", top=None: (
            _cands("RELIANCE", "SBIN") if direction == "up" else _cands("INFY")),
        client_factory=_PaperClient).run_cycle()
    # each strategy's bulk engine was called exactly ONCE, over the same 3-name universe
    assert len(v1.bulk_engine.calls) == 1 and len(v2.bulk_engine.calls) == 1
    assert set(v1.bulk_engine.calls[0]) == {"RELIANCE", "SBIN", "INFY"}
    assert v1.bulk_engine.calls[0] == v2.bulk_engine.calls[0]        # identical universe
    # v1 (buy) entered from the bulk decision, v2 (wait) did not
    assert store.count_open_positions(V1) >= 1 and store.count_open_positions(V2) == 0
    # per-name fallback engine was NOT used for entries (bulk covered them)
    assert v1.engine.seen == []


def test_compare_falls_back_to_per_name_when_no_bulk_engine():
    # A strategy without a bulk engine (or a bulk failure) still works via the per-name engine.
    store = _store()
    v1 = _FakeStrategy("intraday-v1", _buy(), bulk=False)
    CompareOrchestrator(
        store, [v1], get_indicators=lambda s: _indic(s),
        get_candidates=lambda direction="up", top=None: _cands("RELIANCE") if direction == "up" else [],
        client_factory=_PaperClient).run_cycle()
    assert v1.engine.seen                                   # per-name engine was used
    assert store.count_open_positions(V1) >= 1


def test_compare_refuses_live_client():
    store = _store()
    v1 = _FakeStrategy("intraday-v1", _wait())
    co = CompareOrchestrator(store, [v1], get_indicators=lambda s: _indic(s),
                             get_candidates=lambda direction="up", top=None: [],
                             client_factory=_LiveClient)
    with pytest.raises(RuntimeError, match="never use a live broker"):
        co.run_cycle()


def test_compare_places_no_broker_orders_only_paper_sim():
    store = _store()
    made = []

    def paper_factory():
        c = _PaperClient()
        made.append(c)
        return c

    v1 = _FakeStrategy("intraday-v1", _buy())
    CompareOrchestrator(
        store, [v1], get_indicators=lambda s: _indic(s),
        get_candidates=lambda direction="up", top=None: _cands("RELIANCE") if direction == "up" else [],
        client_factory=paper_factory).run_cycle()
    # a paper client was used and every order it saw is a simulated paper order (mode paper)
    assert made and all(c.mode == "paper" for c in made)
    assert any(c.orders for c in made)                 # an order WAS simulated
    assert all(o.get("product", "MIS") for c in made for o in c.orders)  # sanity: went to paper sim


# ---- logging tags --------------------------------------------------------------------------

def test_strategy_tag_filter_prefixes_orchestrator_lines(caplog):
    from compare_orchestrator import _current_strategy, install_strategy_log_tagging
    install_strategy_log_tagging()
    lg = logging.getLogger("autointraday.orchestrator")
    with caplog.at_level(logging.INFO, logger="autointraday.orchestrator"):
        tok = _current_strategy.set("intraday-v2")
        try:
            lg.info("BUY RELIANCE")
        finally:
            _current_strategy.reset(tok)
        lg.info("coordinator line (no strategy context)")
    msgs = [r.getMessage() for r in caplog.records]
    assert "[intraday-v2] BUY RELIANCE" in msgs           # tagged while a strategy is active
    assert "coordinator line (no strategy context)" in msgs   # untagged outside a strategy
