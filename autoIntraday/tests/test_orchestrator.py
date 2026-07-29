import pytest

from decision_engine import Decision
from orchestrator import (Orchestrator, MIN_TRADE_QUALITY, MIN_RISK_REWARD,
                          _passes_entry_gate, _size_quantity, _should_square_off,
                          _position_side)


def _decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=95.0, conf=75, target1=110.0):
    return Decision(action=action, confidence=conf, trade_quality=tq, entry=entry,
                    stop_loss=stop, target1=target1, risk_reward=rr, raw_response="{}")


def test_entry_gate_accepts_good_buy():
    # Floors calibrated to the honest scoring scale: quality >= 52, R:R >= 1.5, confidence >= 50.
    assert _passes_entry_gate(_decision(action="BUY_NOW", tq=52, rr=1.5, conf=50)) is True   # boundary
    assert _passes_entry_gate(_decision(action="SHORT_NOW", tq=78, rr=2.5)) is True
    # the real signals observed 2026-07-14 (both were wrongly blocked by the old 70/62 floors)
    assert _passes_entry_gate(_decision(action="BUY_ON_PULLBACK", tq=63, rr=2.05, conf=60)) is True
    assert _passes_entry_gate(_decision(action="BUY_ON_PULLBACK", tq=62, rr=2.13, conf=58)) is True


def test_entry_gate_rejects_marginal_setups_and_wait():
    assert _passes_entry_gate(_decision(tq=51)) is False           # quality < 52
    assert _passes_entry_gate(_decision(rr=1.4)) is False          # R:R < 1.5
    assert _passes_entry_gate(_decision(conf=49)) is False         # confidence < 50
    assert _passes_entry_gate(_decision(tq=42)) is False           # top of the observed noise band
    assert _passes_entry_gate(_decision(action="WAIT")) is False   # non-entry action
    assert _passes_entry_gate(_decision(action="HOLD")) is False
    assert _passes_entry_gate(_decision(entry=None)) is False      # missing entry
    assert _passes_entry_gate(_decision(target1=None)) is False    # missing target


def test_size_quantity_risk_based():
    # risk 300 / stop-distance 5 = 60, but capital cap 1000/100 = 10 -> capital cap wins
    assert _size_quantity(100.0, 95.0, 1000.0, 300.0) == 10
    # risk 300 / distance 15 = 20 < capital cap 100 -> risk cap wins: same rupee risk every trade
    assert _size_quantity(100.0, 85.0, 10000.0, 300.0) == 20
    # tight stop -> larger size, still same rupee risk: 300 / 1 = 300, capped by capital 100
    assert _size_quantity(100.0, 99.0, 10000.0, 300.0) == 100
    assert _size_quantity(2000.0, 1900.0, 1000.0, 300.0) == 0   # too pricey for the capital
    assert _size_quantity(100.0, 100.0, 10000.0, 300.0) == 0    # zero stop distance -> no trade
    assert _size_quantity(100.0, None, 1000.0, 300.0) == 10     # no stop -> capital cap only


def test_position_side():
    assert _position_side("BUY_NOW") == "LONG"
    assert _position_side("BUY_ON_PULLBACK") == "LONG"
    assert _position_side("SHORT_NOW") == "SHORT"


def test_geometric_rr_from_levels():
    from orchestrator import _geometric_rr
    assert _geometric_rr(100.0, 95.0, 110.0, "LONG") == pytest.approx(2.0)   # risk 5, reward 10
    assert _geometric_rr(100.0, 105.0, 90.0, "SHORT") == pytest.approx(2.0)  # risk 5, reward 10
    assert _geometric_rr(100.0, 98.0, 100.65, "LONG") == pytest.approx(0.325)  # the losing shape
    assert _geometric_rr(100.0, 100.0, 110.0, "LONG") is None                # zero risk distance
    assert _geometric_rr(100.0, 95.0, 99.0, "LONG") is None                  # target below entry
    assert _geometric_rr(None, 95.0, 110.0, "LONG") is None                  # missing leg


def test_trend_blocks_veto():
    from orchestrator import _trend_blocks
    bear = {"higher_timeframe": {"overall_bias": "strong bearish"}}
    bull = {"higher_timeframe": {"overall_bias": "bullish"}}
    neut = {"higher_timeframe": {"overall_bias": "neutral"}}
    assert _trend_blocks("LONG", bear) is not None       # long into a bearish tape -> vetoed
    assert _trend_blocks("SHORT", bear) is None          # short WITH the tape -> allowed
    assert _trend_blocks("SHORT", bull) is not None      # short into a bullish tape -> vetoed
    assert _trend_blocks("LONG", bull) is None
    assert _trend_blocks("LONG", neut) is None           # neutral tape allows both
    assert _trend_blocks("SHORT", neut) is None
    assert _trend_blocks("LONG", {}) is None             # missing field -> fail open (no veto)


def test_market_summary_snapshot():
    from orchestrator import _market_summary
    ind = {"higher_timeframe": {"overall_bias": "strong bearish"},
           "market_context": {"nifty": {"day_change_pct": -0.62, "trend_15m": "neutral"},
                              "india_vix": {"regime": "normal"}}}
    s = _market_summary(ind)
    assert "strong bearish" in s and "NIFTY -0.62%" in s and "VIX normal" in s
    assert _market_summary({}) == ""                     # nothing to say on an empty payload


def test_enter_vetoed_by_bearish_tape():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    ind = dict(_indic("RELIANCE"), higher_timeframe={"overall_bias": "strong bearish"})
    orch = _orch(store, _FakeClient(),
                 _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                       entry=100.0, stop=95.0, target1=110.0)),
                 {"RELIANCE": ind}, candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 0 and store.get_open_positions() == []
    reasons = [d.reason for d in store.get_decisions_for_run(run_id) if d.reason]
    assert any("long vetoed" in r for r in reasons)


def test_stop_distance_ok_floor():
    from orchestrator import _stop_distance_ok
    assert _stop_distance_ok(100.0, 99.6) is True       # 0.4% — exactly the floor
    assert _stop_distance_ok(100.0, 99.7) is False      # 0.3% — inside noise
    assert _stop_distance_ok(377.0, 376.9) is False     # the M&MFIN 0.04% noise stop
    assert _stop_distance_ok(100.0, None) is False


def test_size_quantity_leverage_raises_notional_cap():
    # 1x: capital cap 10000/100 = 100 binds. 5x: cap 500 no longer binds -> risk ceiling 1000/5 = 200.
    assert _size_quantity(100.0, 95.0, 10000.0, 1000.0, 1.0) == 100
    assert _size_quantity(100.0, 95.0, 10000.0, 1000.0, 5.0) == 200
    # default leverage is 1.0 (backwards compatible)
    assert _size_quantity(100.0, 95.0, 10000.0, 1000.0) == 100


def test_size_quantity_full_margin_with_risk_ceiling():
    # Full-margin sizing: quantity = capital * leverage / entry when the risk ceiling doesn't bind.
    # 30k margin at 5x on a 1000 stock = 150 shares; a normal ~2% stop (20) leaves a big ceiling.
    assert _size_quantity(1000.0, 980.0, 30000.0, 10000.0, 5.0) == 150   # cap 150, ceiling 500
    # A pathological WIDE stop (10% = 100) trims below full margin: ceiling 10000/100 = 100 < 150.
    assert _size_quantity(1000.0, 900.0, 30000.0, 10000.0, 5.0) == 100
    # No stop -> full margin, no ceiling to apply.
    assert _size_quantity(1000.0, None, 30000.0, 10000.0, 5.0) == 150


def test_should_square_off_near_close():
    assert _should_square_off({"session": {"bars_remaining": 1, "minutes_to_squareoff": 40}}) is True
    assert _should_square_off({"session": {"bars_remaining": 5, "minutes_to_squareoff": 10}}) is True
    assert _should_square_off({"session": {"bars_remaining": 6, "minutes_to_squareoff": 90}}) is False
    assert _should_square_off({}) is False   # missing session → not near close


from store import Store


class _FakeClient:
    def __init__(self, mode="paper", reject=False, order_status="EXECUTED",
                 broker_positions=()):
        self.mode = mode
        self.orders = []
        self.oco = []
        self.cancelled = []
        self.cancelled_ocos = []
        self.modified_ocos = []
        self.reject = reject           # place_order returns REJECTED when True
        self.order_status = order_status   # what get_order_status reports (live resting fills)
        self.broker_positions = broker_positions   # what get_positions reports (reconcile)
        self.open_orders = []              # what get_open_orders reports (reconcile exclusion)

    def authenticate(self):
        pass

    def ensure_ready(self):
        pass

    def place_order(self, **kw):
        self.orders.append(kw)
        oid = f"PAPER-{len(self.orders)}"
        status = "REJECTED" if self.reject else "COMPLETE"
        return {"order_id": oid, "status": status, "price": kw.get("price"), "mode": self.mode}

    def place_oco_order(self, **kw):
        self.oco.append(kw)
        return {"order_id": f"PAPER-OCO-{len(self.oco)}", "status": "ACTIVE", "mode": self.mode}

    def get_order_status(self, order_id):
        return {"order_id": order_id, "status": self.order_status}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"order_id": order_id, "status": "CANCELLED"}

    def cancel_oco_order(self, order_id):
        self.cancelled_ocos.append(order_id)
        return {"order_id": order_id, "status": "CANCELLED"}

    def modify_oco_order(self, order_id, target, stop_loss):
        self.modified_ocos.append({"order_id": order_id, "target": target,
                                   "stop_loss": stop_loss})
        return {"order_id": order_id, "status": "MODIFIED"}

    def get_positions(self):
        return list(self.broker_positions)

    def get_open_orders(self):
        return list(self.open_orders)


class _FakeEngine:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def decide(self, symbol, indicators, position=None):
        self.calls.append((symbol, position))
        return self.decision


def _indic(symbol="RELIANCE", last=100.0, high=100.0, low=100.0, bars=5, mins=120):
    return {"symbol": symbol, "price": {"last": last, "day_high": high, "day_low": low},
            "session": {"bars_remaining": bars, "minutes_to_squareoff": mins}}


def _orch(store, client, engine, indic_map, candidates=None):
    return Orchestrator(store, client, engine,
                        get_indicators=lambda s: indic_map[s],
                        get_candidates=lambda **kw: candidates or [])


def test_manage_closes_long_on_target():
    # Exits are decided from the CURRENT price (LTP), not the day's high/low — the day range
    # includes pre-entry hours (look-ahead). LTP at/above target -> exit at LTP.
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    client, engine = _FakeClient(), _FakeEngine(_decision(action="HOLD"))
    orch = _orch(store, client, engine, {"RELIANCE": _indic(last=111, high=112, low=99)})
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)
    assert exits == 1
    p = store.get_position(pid)
    assert p.status == "CLOSED"
    assert p.exit_price == 111.0 and p.exit_reason == "TARGET"   # market exit at current price
    assert p.realized_pnl == pytest.approx((111.0 - 100.0) * 10)


def _tp_pos(store):
    # defaults: profit_book partial 7% / full 15% return-on-margin => ~1.4% / ~3% price move at 5x
    return store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=100,
                               entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")


def test_partial_book_at_lower_level_sells_half_and_trails_to_breakeven():
    # +2% move = 10% return-on-margin — between the 7% partial and 15% full levels -> book half.
    store = Store(":memory:")
    pid = _tp_pos(store)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=102)})
    exits = orch._manage_positions(store.start_run("paper"))
    p = store.get_position(pid)
    assert exits == 0 and p.status == "OPEN"           # booked, not fully exited
    assert p.quantity == 50 and p.partial_booked is True
    assert p.stop_loss == pytest.approx(100.0)         # runner trailed to breakeven
    assert p.booked_pnl == pytest.approx((102.0 - 100.0) * 50)   # +100 banked
    store.close_position(pid, exit_price=100.0, exit_reason="STOP", realized_pnl=0.0)
    assert store.get_position(pid).realized_pnl == pytest.approx(100.0)


def test_full_exit_at_upper_level():
    # +3% move = 15% return-on-margin -> EXIT the WHOLE position (take-profit).
    store = Store(":memory:")
    pid = _tp_pos(store)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=103)})
    exits = orch._manage_positions(store.start_run("paper"))
    p = store.get_position(pid)
    assert exits == 1 and p.status == "CLOSED" and p.exit_reason == "TAKE_PROFIT"
    assert p.realized_pnl == pytest.approx((103.0 - 100.0) * 100)


def test_no_book_below_partial_level():
    store = Store(":memory:")
    pid = _tp_pos(store)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=101)})     # +1% = 5% return < 7% partial
    orch._manage_positions(store.start_run("paper"))
    assert store.get_position(pid).partial_booked is False and store.get_position(pid).quantity == 100


def test_profit_book_disabled_lets_it_ride():
    store = Store(":memory:")
    store.update_config(profit_book_enabled=False)
    pid = _tp_pos(store)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=103)})     # would be a full-exit if enabled
    exits = orch._manage_positions(store.start_run("paper"))
    p = store.get_position(pid)
    assert exits == 0 and p.status == "OPEN" and p.partial_booked is False and p.quantity == 100


def test_profit_book_levels_are_configurable():
    store = Store(":memory:")
    store.update_config(profit_book_partial_pct=10.0, profit_book_full_pct=20.0)  # 2% / 4% moves
    pid = _tp_pos(store)
    # +2% move now hits the (raised) partial level -> books half
    _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=102)})._manage_positions(store.start_run("paper"))
    assert store.get_position(pid).quantity == 50
    # +4% move hits the (raised) full level -> exits the rest
    exits = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                  {"RELIANCE": _indic(last=104)})._manage_positions(store.start_run("paper"))
    assert exits == 1 and store.get_position(pid).status == "CLOSED"


def test_manage_day_range_alone_does_not_exit():
    # The day's high breached the target but the CURRENT price is back inside the band — no exit.
    # (Old behavior would have booked a phantom TARGET exit off the stale range.)
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=100, high=112, low=94)})  # range breached BOTH, ltp inside
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)
    assert exits == 0
    assert store.get_position(pid).status == "OPEN"


def test_manage_closes_long_on_stop_at_ltp():
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=94, high=112, low=93)})   # gapped through the stop
    run_id = store.start_run("paper")
    orch._manage_positions(run_id)
    p = store.get_position(pid)
    assert p.exit_reason == "STOP" and p.exit_price == 94.0   # fills at LTP, not the wished stop


def test_manage_square_off_near_close():
    store = Store(":memory:")
    store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"TCS": _indic(symbol="TCS", last=205, high=206, low=204, bars=1)})
    run_id = store.start_run("paper")
    orch._manage_positions(run_id)
    p = store.get_open_positions()
    assert p == []  # squared off despite target/stop not breached
    closed = store.get_run(run_id)  # sanity: run exists
    assert closed is not None


def test_manage_signal_exit_needs_two_confirmed_cycles():
    # A convicted reverse read (SELL_NOW, quality 80 / confidence 75) must CONFIRM for
    # EXIT_CONFIRM_CYCLES cycles before it overrides the stop — one noisy flip won't panic-exit.
    store = Store(":memory:")
    pid = store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                              entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="SELL_NOW")),
                 {"TCS": _indic(symbol="TCS", last=201, high=202, low=200, bars=5)})
    run_id = store.start_run("paper")
    assert orch._manage_positions(run_id) == 0                 # cycle 1: 1/2, held
    assert store.get_position(pid).reverse_signal_count == 1
    assert store.get_position(pid).status == "OPEN"
    assert orch._manage_positions(run_id) == 1                 # cycle 2: confirmed -> exit
    assert store.get_open_positions() == []


def test_manage_weak_reverse_signal_does_not_exit_and_resets():
    # A reverse read BELOW the conviction floor never counts toward the confirmation, and it
    # RESETS a prior count — so the whipsaw (strong-then-weak) can't sneak an exit through.
    store = Store(":memory:")
    pid = store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                              entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    run_id = store.start_run("paper")
    # cycle 1: strong SELL -> count 1
    _orch(store, _FakeClient(), _FakeEngine(_decision(action="SELL_NOW", tq=80, conf=75)),
          {"TCS": _indic("TCS", last=201)})._manage_positions(run_id)
    assert store.get_position(pid).reverse_signal_count == 1
    # cycle 2: weak SELL (quality 44, like MOL) -> not convicted, resets to 0, no exit
    exits = _orch(store, _FakeClient(), _FakeEngine(_decision(action="SELL_NOW", tq=44, conf=40)),
                  {"TCS": _indic("TCS", last=201)})._manage_positions(run_id)
    assert exits == 0
    assert store.get_position(pid).reverse_signal_count == 0 and store.get_position(pid).status == "OPEN"


def test_manage_hold_keeps_position():
    store = Store(":memory:")
    store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"TCS": _indic(symbol="TCS", last=205, high=206, low=204, bars=5)})
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)
    assert exits == 0
    assert len(store.get_open_positions()) == 1


def test_manage_indicator_failure_is_isolated():
    store = Store(":memory:")
    store.open_position(symbol="BAD", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")

    def boom(symbol):
        raise RuntimeError("no data")

    orch = Orchestrator(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                        get_indicators=boom, get_candidates=lambda **kw: [])
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)   # must not raise
    assert exits == 0
    assert len(store.get_open_positions()) == 1
    decs = store.get_decisions_for_run(run_id)
    assert any("no data" in (d.reason or "") for d in decs)   # recorded the skip


def _cands(*syms):
    return [{"symbol": s, "ltp": 100.0, "change_pct": 2.0} for s in syms]


def _cfg(store, **kw):
    store.update_config(**kw)


def test_enter_opens_position_on_good_buy():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    client, engine = _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0,
                                                          entry=100.0, stop=98.0, target1=110.0))
    orch = _orch(store, client, engine, {"RELIANCE": _indic()}, candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert screened == 1 and entries == 1
    p = store.get_open_positions()[0]
    assert p.symbol == "RELIANCE" and p.side == "LONG"
    # Full 5x margin: capital 10000 * 5 / 100 = 500 shares (a ~2% stop is well inside the 2.5%
    # risk ceiling, so the full margin is deployed). Margin used = 500*100/5 = 10000 = capital.
    assert p.quantity == 500
    assert len(client.orders) == 1 and len(client.oco) == 1


def test_enter_rejects_tight_stop():
    # A stop 0.2% below entry clears the self-reported R:R gate but is inside noise — rejected by
    # the stop-distance floor before any capital is sized/committed.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    orch = _orch(store, _FakeClient(),
                 _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                       entry=100.0, stop=99.8, target1=110.0)),
                 {"RELIANCE": _indic()}, candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 0 and store.get_open_positions() == []
    reasons = [d.reason for d in store.get_decisions_for_run(run_id) if d.reason]
    assert any("stop too tight" in r for r in reasons)


def test_enter_rejects_when_margins_degrade_rr_below_floor():
    # The engine self-reports rr=2.0 and the gate passes, but a wide target-to-entry gap plus the
    # stop widening leave the ACTUAL post-margin geometry below 1.5 — the re-gate rejects it.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    # entry 100, stop 90 (10% risk), target 110.5: geometric rr ~1.05 despite reported 2.0.
    orch = _orch(store, _FakeClient(),
                 _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                       entry=100.0, stop=90.0, target1=110.5)),
                 {"RELIANCE": _indic()}, candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 0 and store.get_open_positions() == []
    reasons = [d.reason for d in store.get_decisions_for_run(run_id) if d.reason]
    assert any("post-margin R:R" in r for r in reasons)


def test_enter_rejects_failing_gate_but_records_decision():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="WAIT", tq=40)),
                 {"RELIANCE": _indic()}, candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert entries == 0
    assert store.get_open_positions() == []
    assert len(store.get_decisions_for_run(run_id)) == 1   # still recorded


def test_enter_respects_free_slots():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=10000.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0)),
                 {"A": _indic("A"), "B": _indic("B")}, candidates=_cands("A", "B"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 1                       # only 1 slot
    assert store.count_open_positions() == 1


def test_enter_skips_when_no_free_capital():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=5000.0, max_open_positions=3,
         capital_per_position=10000.0)   # pool < capital_per_position
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0)),
                 {"A": _indic("A")}, candidates=_cands("A"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 0


def test_enter_skips_already_held_symbol():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1, entry_price=100.0,
                        mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0)),
                 {"A": _indic("A")}, candidates=_cands("A"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert entries == 0   # A already held → not re-entered


def test_run_cycle_paused_does_nothing():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0, is_paused=True)
    client = _FakeClient()
    orch = _orch(store, client, _FakeEngine(_decision()), {}, candidates=_cands("A"))
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"
    assert summary["entries"] == 0 and summary["exits"] == 0
    assert client.orders == []                     # no trading while paused
    assert store.get_run(summary["run_id"]).summary == "paused"


def test_run_cycle_enters_then_reports():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=10000.0)
    client = _FakeClient()
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=95.0))
    orch = _orch(store, client, engine, {"A": _indic("A")}, candidates=_cands("A"))
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"
    assert summary["entries"] == 1
    run = store.get_run(summary["run_id"])
    assert run.status == "SUCCESS" and run.num_actions == 1


def test_run_cycle_exit_frees_slot_for_entry():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=10000.0)
    # hold one position that will square off this cycle (bars=1)
    store.open_position(symbol="OLD", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=95.0))
    indic = {"OLD": _indic("OLD", bars=1), "NEW": _indic("NEW", bars=5)}
    orch = _orch(store, _FakeClient(), engine, indic, candidates=_cands("NEW"))
    summary = orch.run_cycle()
    assert summary["exits"] == 1 and summary["entries"] == 1   # OLD squared off → NEW entered
    open_syms = {p.symbol for p in store.get_open_positions()}
    assert open_syms == {"NEW"}


def test_full_margin_sizing_deploys_leverage_and_notional_exceeds_pool():
    # Full-margin sizing at 5x: each position buys capital*5/entry = 10000*5/100 = 500 shares
    # (a 2% stop is inside the 2.5% risk ceiling), deploying the full 10000 margin. All 4
    # candidates enter; deployed NOTIONAL (200000) exceeds the 100000 pool — that IS leverage —
    # while the MARGIN committed (200000/5 = 40000) stays within the pool.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=5,
         capital_per_position=10000.0)
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=98.0,
                                   target1=110.0))
    indic = {"A": _indic("A"), "B": _indic("B"), "C": _indic("C"), "D": _indic("D")}
    orch = _orch(store, _FakeClient(), engine, indic, candidates=_cands("A", "B", "C", "D"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 4
    assert all(p.quantity == 500 for p in store.get_open_positions())   # full 5x margin each
    assert store.deployed_capital() == pytest.approx(200000.0)          # notional > 100000 pool
    assert store.committed_capital() / 5.0 <= 100000.0                  # margin within the pool


def test_run_cycle_marks_failed_and_reraises():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=10000.0)

    class BoomClient(_FakeClient):
        def ensure_ready(self):
            raise RuntimeError("auth blew up")

    orch = _orch(store, BoomClient(), _FakeEngine(_decision()), {}, candidates=[])
    with pytest.raises(RuntimeError, match="auth blew up"):
        orch.run_cycle()
    # the run was marked FAILED, not left RUNNING
    runs_failed = [r for r in [store.get_run(1)] if r.status == "FAILED"]
    assert len(runs_failed) == 1
    assert "auth blew up" in (store.get_run(1).error or "")


# ---- resting (pending) entries + trailing --------------------------------------------------

def test_resting_entry_creates_pending_not_market_order():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient()
    engine = _FakeEngine(_decision(action="BUY_ON_PULLBACK", tq=80, rr=2.0,
                                   entry=98.0, stop=95.0, target1=110.0))
    # current range 99..101 does NOT contain 98 -> stays pending after this cycle
    orch = _orch(store, client, engine, {"AAA": _indic("AAA", last=100, high=101, low=99)},
                 candidates=[{"symbol": "AAA"}])
    summary = orch.run_cycle()
    assert summary["entries"] == 1
    assert client.orders == []                     # NO broker order for a resting entry yet
    assert client.oco == []
    assert store.get_open_positions() == []        # not open yet
    pend = store.get_pending_positions()
    assert len(pend) == 1 and pend[0].symbol == "AAA" and pend[0].status == "PENDING"
    assert store.count_committed_positions() == 1  # it reserves a slot


def test_pending_fills_when_range_reaches_level():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=98.0, target_price=110.0, stop_loss=95.0,
                              mode="paper", status="PENDING")
    client = _FakeClient()
    # fresh read keeps the same pullback setup (levels within the refresh threshold -> no churn)
    still = _decision(action="BUY_ON_PULLBACK", entry=98.0, stop=95.0, target1=110.0)
    orch = _orch(store, client, _FakeEngine(still),
                 {"AAA": _indic("AAA", last=97, high=99, low=96)})   # 96..99 contains 98
    summary = orch.run_cycle()
    assert summary["fills"] == 1
    op = store.get_open_positions()
    assert len(op) == 1 and op[0].id == pid and op[0].status == "OPEN"
    assert store.get_pending_positions() == []
    assert len(client.orders) == 1 and client.orders[0]["order_type"] == "LIMIT"
    assert len(client.oco) == 1                     # OCO armed on fill


def test_just_filled_position_not_exited_same_cycle():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    # LTP 97 fills the pullback @98 AND breaches the 97.5 stop — a same-cycle exit check WOULD
    # stop it out instantly. The just-filled skip must prevent that; it's managed next cycle.
    store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                        entry_price=98.0, target_price=110.0, stop_loss=97.5,
                        mode="paper", status="PENDING", trigger_kind="LIMIT")
    client = _FakeClient()
    still = _decision(action="BUY_ON_PULLBACK", entry=98.0, stop=97.5, target1=110.0)
    orch = _orch(store, client, _FakeEngine(still),
                 {"AAA": _indic("AAA", last=97, high=100, low=96)})
    summary = orch.run_cycle()
    assert summary["fills"] == 1
    assert summary["exits"] == 0                     # NOT exited the cycle it filled
    op = store.get_open_positions()
    assert len(op) == 1 and op[0].status == "OPEN"


def test_refresh_pending_cancels_only_on_opposite_signal():
    # Loosened cancellation: a resting long is cancelled only when the engine flips to the
    # OPPOSITE side (a real invalidation) — a plain WAIT (pullback not printed yet) keeps it.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)

    def _pending():
        return store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                                   entry_price=98.0, target_price=110.0, stop_loss=95.0,
                                   mode="paper", status="PENDING", trigger_kind="LIMIT")

    # WAIT no longer cancels — the order keeps resting (LTP 200 so it doesn't fill either).
    pid = _pending()
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="WAIT")),
                 {"AAA": _indic("AAA", last=200)})
    orch.run_cycle()
    assert store.get_position(pid).status == "PENDING"

    # A flip to the opposite side (SELL_NOW) IS a genuine invalidation — cancel and free the slot.
    pid2 = _pending()
    orch2 = _orch(store, _FakeClient(), _FakeEngine(_decision(action="SELL_NOW")),
                  {"AAA": _indic("AAA", last=200)})
    orch2.run_cycle()
    assert store.get_position(pid2).status == "CANCELLED"
    assert store.get_position(pid2).exit_reason == "SETUP_GONE"


def test_refresh_pending_updates_levels_when_still_valid():
    # Still a valid same-side entry but the engine moved the levels -> refresh entry/stop/target.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=98.0, target_price=110.0, stop_loss=95.0,
                              mode="paper", status="PENDING", trigger_kind="LIMIT")
    # fresh read: still a pullback long, but levels shifted up; LTP 105 keeps it resting
    fresh = _decision(action="BUY_ON_PULLBACK", tq=80, conf=70, rr=2.0,
                      entry=101.0, stop=97.0, target1=113.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(fresh), {"AAA": _indic("AAA", last=105)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "PENDING"
    # levels reflect the fresh decision AFTER breathing-space margins (entry +0.25%, etc.)
    assert p.entry_price == pytest.approx(101.0 * 1.0025)
    assert p.stop_loss == pytest.approx(97.0 * (1 - 0.35 / 100))


def test_refresh_pending_keeps_order_when_unchanged():
    # Same levels -> no churn: order left exactly as-is (important for the live cancel+replace path).
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    # store the ALREADY-margined levels so the fresh read reproduces them exactly
    from orchestrator import _with_level_margins
    d = _with_level_margins(_decision(action="BUY_ON_PULLBACK", entry=98.0, stop=95.0,
                                      target1=110.0))
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=d.entry, target_price=d.target1, stop_loss=d.stop_loss,
                              mode="paper", status="PENDING", trigger_kind="LIMIT")
    fresh = _decision(action="BUY_ON_PULLBACK", tq=80, conf=70, rr=2.0,
                      entry=98.0, stop=95.0, target1=110.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(fresh), {"AAA": _indic("AAA", last=105)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.entry_price == pytest.approx(d.entry)   # unchanged


# The pending order stores the ALREADY-margined levels (as production does via _place_entry), so
# the per-cycle refresh reproduces them and does NOT churn. E is that resting entry level.
from orchestrator import _with_level_margins as _mgn
_JG = _decision(action="BUY_ON_PULLBACK", entry=473.5, stop=465.0, target1=490.0)
_JG_E = _mgn(_JG).entry


def _seed_jg_pending(store):
    d = _mgn(_JG)
    return store.open_position(symbol="JG", exchange="NSE", side="LONG", quantity=10,
                               entry_price=d.entry, target_price=d.target1, stop_loss=d.stop_loss,
                               mode="paper", status="PENDING", trigger_kind="LIMIT")


def test_pullback_fills_on_near_miss_overshoot():
    # The JGCHEM case: a pullback LIMIT long that price rallies a hair PAST the level without
    # dipping. Within ENTRY_FILL_TOLERANCE_PCT (0.40%) it still fills — at the current price,
    # not the idealized level (a touch less profit, but not a miss).
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = _seed_jg_pending(store)
    ltp = round(_JG_E * 1.003, 2)                    # +0.3% above the level -> inside the band
    orch = _orch(store, _FakeClient(), _FakeEngine(_JG),
                 {"JG": _indic("JG", last=ltp, high=ltp + 1, low=_JG_E - 1)})
    summary = orch.run_cycle()
    assert summary["fills"] == 1
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert p.entry_price == pytest.approx(ltp)       # filled at current price (the chase), not the level


def test_pullback_does_not_fill_beyond_band():
    # Same setup, but price ran too far (+0.6% > 0.40% band) -> do NOT chase; stays PENDING.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = _seed_jg_pending(store)
    ltp = round(_JG_E * 1.006, 2)                    # +0.6% -> beyond the band
    orch = _orch(store, _FakeClient(), _FakeEngine(_JG),
                 {"JG": _indic("JG", last=ltp, high=ltp + 1, low=_JG_E - 1)})
    summary = orch.run_cycle()
    assert summary["fills"] == 0
    assert store.get_position(pid).status == "PENDING"


def test_pullback_dip_to_level_still_books_the_level():
    # When price DOES dip to/below the level, we still book the better level price, not LTP.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = _seed_jg_pending(store)
    ltp = round(_JG_E - 1.5, 2)                       # dipped below the level
    orch = _orch(store, _FakeClient(), _FakeEngine(_JG),
                 {"JG": _indic("JG", last=ltp, high=_JG_E + 2, low=ltp - 1)})
    orch.run_cycle()
    assert store.get_position(pid).entry_price == pytest.approx(_JG_E)   # booked the level


def test_pending_cancelled_at_squareoff():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=98.0, target_price=110.0, stop_loss=95.0,
                              mode="paper", status="PENDING")
    client = _FakeClient()
    # range contains 98 BUT bars_remaining=1 -> square-off wins, cancel instead of fill
    orch = _orch(store, client, _FakeEngine(_decision(action="WAIT")),
                 {"AAA": _indic("AAA", last=98, high=99, low=97, bars=1)})
    summary = orch.run_cycle()
    assert summary["fills"] == 0
    assert client.orders == []
    assert store.get_pending_positions() == []
    assert store.get_position(pid).status == "CANCELLED"


def test_pending_reserves_slot_and_blocks_screen():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=20000.0)
    store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                        entry_price=98.0, target_price=110.0, stop_loss=95.0,
                        mode="paper", status="PENDING")

    def boom(**kw):
        raise AssertionError("screener called though a pending order fills the only slot")

    client = _FakeClient()
    still = _decision(action="BUY_ON_PULLBACK", entry=98.0, stop=95.0, target1=110.0)
    orch = Orchestrator(store, client, _FakeEngine(still),
                        get_indicators=lambda s: _indic(s, last=99, high=99, low=99),
                        get_candidates=boom)   # ltp 99 > level 98 -> stays pending, book full
    summary = orch.run_cycle()
    assert summary["candidates"] == 0 and summary["fills"] == 0
    assert store.count_committed_positions() == 1


def test_trail_ratchets_long_stop_up_and_updates_target():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="paper")
    client = _FakeClient()
    # HOLD (no exit) with a tighter stop and higher target -> both move
    engine = _FakeEngine(_decision(action="HOLD", stop=98.0, target1=112.0))
    orch = _orch(store, client, engine, {"AAA": _indic("AAA", last=101, high=102, low=100)})
    summary = orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert p.stop_loss == 98.0                       # ratcheted up
    assert p.target_price == 112.0
    # the trail is logged as an ADJUSTED operation so the activity tally can show it
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any(r.action == "ADJUSTED" and "trailed" in (r.reason or "") for r in recs)


# ---- live hardening: square-off, rejected orders, error resilience, live resting -------------

def test_squareoff_only_flattens_and_cancels():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0)
    op = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                             entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    pd = store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=10,
                             entry_price=98.0, target_price=110.0, stop_loss=95.0,
                             mode="paper", status="PENDING")

    def boom(**kw):
        raise AssertionError("square-off mode must not screen for entries")

    orch = Orchestrator(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                        get_indicators=lambda s: _indic(s, last=105), get_candidates=boom)
    summary = orch.run_cycle(squareoff_only=True)
    assert summary["exits"] == 1 and summary["cancels"] == 1
    assert store.get_open_positions() == [] and store.get_pending_positions() == []
    assert store.get_position(op).status == "CLOSED"
    assert store.get_position(pd).status == "CANCELLED"


def test_market_entry_rejected_opens_no_position():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(reject=True)
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0))
    orch = _orch(store, client, engine, {"AAA": _indic("AAA")}, candidates=[{"symbol": "AAA"}])
    summary = orch.run_cycle()
    assert summary["entries"] == 0
    assert store.get_open_positions() == []
    assert client.oco == []                      # never arm an OCO on a rejected entry


def test_broker_error_on_one_position_does_not_abort_cycle():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0,
                        target_price=110.0, stop_loss=99.0, mode="paper")

    class BoomOnClose(_FakeClient):
        def place_order(self, **kw):
            raise RuntimeError("broker down")

    # low 98 <= stop 99 -> exit -> _close_position -> place_order raises; cycle must NOT abort
    orch = _orch(store, BoomOnClose(), _FakeEngine(_decision(action="HOLD")),
                 {"A": _indic("A", last=98, high=98, low=98)})
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"        # one broker error did not kill the cycle
    assert store.count_open_positions() == 1     # close failed -> position left intact


def test_live_resting_places_real_limit_then_fills_on_broker_confirm():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live", order_status="EXECUTED")
    engine = _FakeEngine(_decision(action="BUY_ON_PULLBACK", tq=80, rr=2.0, entry=98.0,
                                   target1=110.0, stop=95.0))
    orch = _orch(store, client, engine, {"AAA": _indic("AAA", last=100, high=101, low=99)},
                 candidates=[{"symbol": "AAA"}])
    # cycle 1: a REAL limit order is placed and a PENDING position reserves the slot
    s1 = orch.run_cycle()
    assert s1["entries"] == 1
    assert len(client.orders) == 1 and client.orders[0]["order_type"] == "LIMIT"
    pend = store.get_pending_positions()
    assert len(pend) == 1 and pend[0].entry_order_id is not None
    # cycle 2: broker reports EXECUTED -> position goes OPEN. With USE_BROKER_OCO=False
    # (Groww smart-order API failed the 2026-07-20 live verification) NO broker OCO is
    # placed — the position is protected by cycle-level exits + square-off instead.
    s2 = orch.run_cycle()
    assert s2["fills"] == 1
    op = store.get_open_positions()
    assert len(op) == 1 and op[0].status == "OPEN"
    assert client.oco == [] and op[0].oco_order_id is None


# ---- safety layer: OCO cancel, circuit breaker, reconciliation, two-direction screen ---------

def test_close_position_cancels_oco_first():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0,
                        target_price=110.0, stop_loss=95.0, mode="paper",
                        oco_order_id="PAPER-OCO-7")
    client = _FakeClient()
    orch = _orch(store, client, _FakeEngine(_decision(action="HOLD")),
                 {"A": _indic("A", last=111)})   # ltp >= target -> exit
    orch.run_cycle()
    assert client.cancelled_ocos == ["PAPER-OCO-7"]   # disarmed BEFORE the market exit
    assert store.get_open_positions() == []


def test_oco_cancel_failure_does_not_block_exit():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0,
                        target_price=110.0, stop_loss=95.0, mode="paper",
                        oco_order_id="PAPER-OCO-7")

    class BoomOco(_FakeClient):
        def cancel_oco_order(self, order_id):
            raise RuntimeError("broker down")

    orch = _orch(store, BoomOco(), _FakeEngine(_decision(action="HOLD")),
                 {"A": _indic("A", last=111)})
    orch.run_cycle()
    assert store.get_open_positions() == []           # exit still happened


def test_circuit_breaker_blocks_new_entries_but_not_exits():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=30000.0, max_open_positions=3,
         capital_per_position=15000.0)
    # a big realized loss today: breaker trips at -5% of 30000 = -1500; -2500 stays breached
    # even after the winning exit below (+110)
    lose = store.open_position(symbol="L", exchange="NSE", side="LONG", quantity=10,
                               entry_price=500.0)
    store.close_position(lose, exit_price=250.0, exit_reason="STOP", realized_pnl=-2500.0)
    # one open position that must STILL be managed to exit
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0,
                        target_price=110.0, stop_loss=95.0, mode="paper")

    def boom(**kw):
        raise AssertionError("screener must not run once the daily loss breaker trips")

    orch = Orchestrator(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                        get_indicators=lambda s: _indic(s, last=111), get_candidates=boom)
    summary = orch.run_cycle()
    assert summary["candidates"] == 0 and summary["entries"] == 0
    assert summary["exits"] == 1                      # open position still managed to flat


def test_reconcile_closes_db_position_absent_at_broker():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="GONE", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="live")

    class BrokerFlat(_FakeClient):
        def get_positions(self):
            return []          # broker says: no net position — the OCO fired between cycles

    orch = _orch(store, BrokerFlat(mode="live"), _FakeEngine(_decision(action="HOLD")),
                 {"GONE": _indic("GONE", last=109)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "CLOSED" and p.exit_reason == "BROKER_SYNC"
    assert p.exit_price == 109.0                      # approximated at LTP


def test_reconcile_leaves_position_still_held_at_broker():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="HELD", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="live")

    class BrokerHolds(_FakeClient):
        def get_positions(self):
            return [{"symbol": "HELD", "quantity": 10, "product": "MIS", "avg_price": 100.0}]

    orch = _orch(store, BrokerHolds(mode="live"), _FakeEngine(_decision(action="HOLD")),
                 {"HELD": _indic("HELD", last=101)})
    orch.run_cycle()
    assert store.get_position(pid).status == "OPEN"   # untouched


def test_screen_gathers_both_directions_and_dedupes():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0)
    calls = []

    def cands(direction="up", top=15, **kw):
        calls.append(direction)
        if direction == "up":
            return [{"symbol": "UPA"}, {"symbol": "BOTH"}]
        return [{"symbol": "DOWNA"}, {"symbol": "BOTH"}]

    engine = _FakeEngine(_decision(action="WAIT"))   # decide on all, enter none
    orch = Orchestrator(store, _FakeClient(), engine,
                        get_indicators=lambda s: _indic(s), get_candidates=cands)
    summary = orch.run_cycle()
    assert set(calls) == {"up", "down"}
    screened_symbols = [c[0] for c in engine.calls]
    assert sorted(screened_symbols) == ["BOTH", "DOWNA", "UPA"]   # deduped, both directions


def test_oco_placement_failure_still_records_position():
    """The entry order has already FILLED when the OCO is placed — an OCO failure must never
    skip recording the position (that would leave a real, invisible, unprotected holding).
    It must be recorded with no OCO and surfaced via the errors count (job sends an alert)."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)

    class BoomOcoPlace(_FakeClient):
        def place_oco_order(self, **kw):
            raise RuntimeError("smart order API mismatch")

    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=95.0))
    orch = _orch(store, BoomOcoPlace(), engine, {"AAA": _indic("AAA")},
                 candidates=[{"symbol": "AAA"}])
    summary = orch.run_cycle()
    assert summary["entries"] == 1
    assert summary["errors"] == 1                     # alert-worthy, surfaced to the job
    op = store.get_open_positions()
    assert len(op) == 1 and op[0].oco_order_id is None   # recorded, cycle-managed, no OCO


def test_screener_failure_degrades_instead_of_failing_cycle():
    """A transient screener error (external endpoint) must not mark the cycle FAILED — exits
    were already managed; the only cost is no new entries this cycle. (Regression: run 4 on
    2026-07-14 failed entirely on a momentary 'screener exit 1'.)"""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0,
                        target_price=110.0, stop_loss=95.0, mode="paper")

    def boom(**kw):
        raise RuntimeError("screener exit 1")

    orch = Orchestrator(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                        get_indicators=lambda s: _indic(s, last=111), get_candidates=boom)
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"          # NOT failed
    assert summary["candidates"] == 0 and summary["entries"] == 0
    assert summary["exits"] == 1                   # position still managed
    assert store.get_run(summary["run_id"]).status == "SUCCESS"


def test_trail_target_ratchets_only_away_from_entry():
    # The target only moves AWAY from entry (up for a long); a re-quote pulling it IN toward entry
    # is ignored, so a winner's reward can't be shrunk mid-trade into an early TARGET exit.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=112.0, stop_loss=95.0, mode="paper")
    # engine now proposes a NEARER target (108 < 112) — must be ignored; stop 96 still ratchets up
    engine = _FakeEngine(_decision(action="HOLD", stop=96.0, target1=108.0))
    orch = _orch(store, _FakeClient(), engine, {"AAA": _indic("AAA", last=101, high=102, low=100)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.target_price == 112.0     # target NOT pulled in
    assert p.stop_loss == 96.0         # stop still ratcheted up


def test_trail_never_loosens_stop():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="paper")
    client = _FakeClient()
    # HOLD with a LOOSER stop (90 < 95) -> must be ignored
    engine = _FakeEngine(_decision(action="HOLD", stop=90.0, target1=110.0))
    orch = _orch(store, client, engine, {"AAA": _indic("AAA", last=101, high=102, low=100)})
    orch.run_cycle()
    assert store.get_position(pid).stop_loss == 95.0   # unchanged


def test_stop_entry_not_chased_when_overextended():
    """A resting STOP (breakout) order must NOT fill when price has already run >1% past the
    trigger between cycles — chasing there destroys the decision's R:R (BECTORFOOD 2026-07-16:
    trigger 188.6, next cycle 193.5). It stays PENDING and fills only on a retest."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=71,
                              entry_price=188.6, target_price=197.0, stop_loss=184.4,
                              mode="paper", status="PENDING", trigger_kind="STOP")
    client = _FakeClient()
    # fresh read keeps the same breakout setup (levels unchanged -> no churn)
    still = _decision(action="BUY_ON_BREAKOUT", entry=188.6, stop=184.4, target1=197.0)
    # price ran to 193.5 — 2.6% past the 188.6 trigger -> no fill
    orch = _orch(store, client, _FakeEngine(still),
                 {"B": _indic("B", last=193.5, high=194, low=177.5)})
    summary = orch.run_cycle()
    assert summary["fills"] == 0
    assert store.get_position(pid).status == "PENDING"     # still resting
    assert client.orders == []                              # no market chase
    # later, price retests to within tolerance (188.6*1.01 = 190.49) -> fills at LTP
    orch2 = _orch(store, client, _FakeEngine(still),
                  {"B": _indic("B", last=189.5, high=194, low=177.5)})
    summary2 = orch2.run_cycle()
    assert summary2["fills"] == 1
    assert store.get_position(pid).status == "OPEN"
    assert store.get_position(pid).entry_price == 189.5     # honest fill at retest LTP


def test_live_breakout_places_real_stop_entry_order_at_broker():
    """LIVE breakout resting entries must be REAL broker SL stop-entry orders (trigger at the
    level, limit slightly beyond) — not DB-only monitors. The broker fires them at the exact
    price in real time; cycles just poll status (user requirement 2026-07-16)."""
    from orchestrator import _tick
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live", order_status="OPEN")   # broker: still resting
    engine = _FakeEngine(_decision(action="BUY_ON_BREAKOUT", tq=80, rr=2.0, entry=188.6,
                                   stop=184.4, target1=197.0))
    orch = _orch(store, client, engine, {"B": _indic("B", last=186, high=187, low=178)},
                 candidates=[{"symbol": "B"}])
    summary = orch.run_cycle()
    assert summary["entries"] == 1
    assert len(client.orders) == 1
    o = client.orders[0]
    adj_entry = 188.6 * (1 - 0.25 / 100)                 # ENTRY_TOLERANCE_PCT: trigger arms
    assert o["order_type"] == "SL"                       # real stop-entry, not synthetic
    assert o["trigger_price"] == _tick(adj_entry)        # ...slightly BEFORE the exact level
    assert o["price"] == _tick(adj_entry * 1.005)        # bounded slippage limit
    pend = store.get_pending_positions()
    assert len(pend) == 1 and pend[0].entry_order_id is not None   # broker-tracked


def test_tick_rounding():
    from orchestrator import _tick
    assert _tick(188.6 * 1.005) == 189.55
    assert _tick(100.02) == 100.0
    assert _tick(100.03) == 100.05


def test_trail_pushes_new_levels_to_broker_oco():
    """When the per-cycle re-check moves the stop/target, the BROKER's OCO must be modified too
    — a stop trailed only in the DB protects nothing between cycles (user req 2026-07-16)."""
    from orchestrator import _tick
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live", oco_order_id="OCO-9")
    client = _FakeClient(mode="live",
                         broker_positions=[{"symbol": "A", "quantity": 10, "product": "MIS",
                                            "avg_price": 100.0}])
    engine = _FakeEngine(_decision(action="HOLD", stop=98.0, target1=112.0))
    orch = _orch(store, client, engine, {"A": _indic("A", last=101, high=102, low=100)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.stop_loss == 98.0 and p.target_price == 112.0        # DB updated
    assert client.modified_ocos == [{"order_id": "OCO-9", "target": _tick(112.0),
                                     "stop_loss": _tick(98.0)}]   # broker updated too


def test_trail_broker_modify_failure_keeps_cycle_alive():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live", oco_order_id="OCO-9")

    class BoomModify(_FakeClient):
        def modify_oco_order(self, order_id, target, stop_loss):
            raise RuntimeError("modify API mismatch")

    client = BoomModify(mode="live",
                        broker_positions=[{"symbol": "A", "quantity": 10, "product": "MIS",
                                           "avg_price": 100.0}])
    engine = _FakeEngine(_decision(action="HOLD", stop=98.0, target1=112.0))
    orch = _orch(store, client, engine, {"A": _indic("A", last=101, high=102, low=100)})
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"                # cycle survives
    assert summary["errors"] == 1                        # surfaced -> job notification
    assert store.get_position(pid).stop_loss == 98.0     # DB still trailed (cycle exits honor it)


def test_trail_no_broker_call_when_levels_unchanged():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0,
                        mode="live", oco_order_id="OCO-9")
    client = _FakeClient(mode="live",
                         broker_positions=[{"symbol": "A", "quantity": 10, "product": "MIS",
                                            "avg_price": 100.0}])
    # engine repeats the SAME levels -> no modify call (avoid hammering the broker API)
    engine = _FakeEngine(_decision(action="HOLD", stop=95.0, target1=110.0))
    orch = _orch(store, client, engine, {"A": _indic("A", last=101, high=102, low=100)})
    orch.run_cycle()
    assert client.modified_ocos == []


class _FakeScreenEngine:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def screen(self, exclude_symbols=()):
        self.calls.append(sorted(exclude_symbols))
        if self.error:
            raise self.error
        return self.results


def _screen_orch(store, screen_engine, indic_map=None, client=None):
    # classic get_candidates must never be called in skill mode — booby-trap it
    def trap(**kw):
        raise AssertionError("classic screener called in skill mode")
    return Orchestrator(store, client or _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                        get_indicators=lambda s: (indic_map or {}).get(s, _indic(s)),
                        get_candidates=trap, screen_engine=screen_engine)


def test_skill_screen_places_entries_in_quality_order():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=20000.0)
    eng = _FakeScreenEngine(results=[
        ("LOW", _decision(action="BUY_NOW", tq=60, conf=60, rr=2.0, entry=50.0, stop=48.0)),
        ("HIGH", _decision(action="BUY_NOW", tq=90, conf=80, rr=2.5, entry=100.0, stop=96.0)),
    ])
    orch = _screen_orch(store, eng, indic_map={"HIGH": _indic("HIGH", last=100),
                                               "LOW": _indic("LOW", last=50)})
    summary = orch.run_cycle()
    assert summary["entries"] == 1 and summary["candidates"] == 2
    open_syms = [p.symbol for p in store.get_open_positions()]
    assert open_syms == ["HIGH"]                     # best quality wins the only slot


def test_skill_screen_gate_rejects_and_records():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    eng = _FakeScreenEngine(results=[
        ("WEAK", _decision(action="BUY_NOW", tq=40, conf=40, rr=1.0)),
    ])
    orch = _screen_orch(store, eng)
    summary = orch.run_cycle()
    assert summary["entries"] == 0 and summary["candidates"] == 1
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any(r.symbol == "WEAK" and r.reason == "below gate" for r in recs)


def test_skill_screen_failure_degrades_to_zero_candidates():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    eng = _FakeScreenEngine(error=RuntimeError("claude CLI exit 1"))
    orch = _screen_orch(store, eng, indic_map={"A": _indic("A", last=111)})
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"
    assert summary["candidates"] == 0 and summary["entries"] == 0
    assert summary["exits"] == 1                     # exits still managed


def test_skill_screen_refilters_held_symbols():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="HELD", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    eng = _FakeScreenEngine(results=[
        ("HELD", _decision(action="BUY_NOW", tq=90, conf=80, rr=2.5)),
        ("FRESH", _decision(action="BUY_NOW", tq=70, conf=65, rr=2.0)),
    ])
    orch = _screen_orch(store, eng,
                        indic_map={"HELD": _indic("HELD", last=100),
                                   "FRESH": _indic("FRESH", last=100)})
    summary = orch.run_cycle()
    # excluded symbol passed to the engine AND re-filtered even though the model returned it
    assert eng.calls == [["HELD"]]
    open_syms = sorted(p.symbol for p in store.get_open_positions())
    assert open_syms == ["FRESH", "HELD"]            # HELD is the pre-existing position only
    assert summary["entries"] == 1


def test_skill_screen_skipped_when_book_full():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    eng = _FakeScreenEngine(results=[("B", _decision(action="BUY_NOW", tq=90, conf=80))])
    orch = _screen_orch(store, eng, indic_map={"A": _indic("A", last=101)})
    orch.run_cycle()
    assert eng.calls == []                           # book full -> no expensive skill call


def test_classic_path_untouched_when_no_screen_engine():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client, engine = _FakeClient(), _FakeEngine(
        _decision(action="BUY_NOW", tq=90, conf=80, rr=2.5))
    orch = Orchestrator(store, client, engine,
                        get_indicators=lambda s: _indic(s, last=100),
                        get_candidates=lambda **kw: [{"symbol": "CLS"}])
    summary = orch.run_cycle()
    assert summary["entries"] == 1
    assert engine.calls and engine.calls[0][0] == "CLS"   # per-name decide still used


def test_live_broker_oco_disabled_records_unprotected_position():
    # USE_BROKER_OCO=False (Groww smart-order API failed the 2026-07-20 live verification):
    # live entries must record the position with NO broker OCO call, protected by cycle exits.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live")
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=90, conf=80, rr=2.5))
    orch = Orchestrator(store, client, engine,
                        get_indicators=lambda s: _indic(s, last=100),
                        get_candidates=lambda **kw: [{"symbol": "LIV"}])
    summary = orch.run_cycle()
    assert summary["entries"] == 1
    assert client.oco == []                          # no broker OCO placed
    pos = store.get_open_positions()[0]
    assert pos.symbol == "LIV" and pos.oco_order_id is None


def test_level_margins_long_pullback():
    # Entry nudged UP toward price (don't miss a near-touch), stop widened DOWN (noise-safe;
    # rupee risk unchanged via sizing), target keeps 90% of the projected MOVE (shave reduced
    # 25%->10% on 2026-07-22 so it stops destroying R:R) measured from the ORIGINAL entry.
    from orchestrator import _with_level_margins
    d = _with_level_margins(_decision(action="BUY_ON_PULLBACK", entry=100.0, stop=95.0,
                                      target1=110.0))
    assert d.entry == pytest.approx(100.25)      # +0.25%
    assert d.stop_loss == pytest.approx(94.6675) # -0.35%
    assert d.target1 == pytest.approx(109.0)     # 100 + 10*0.90


def test_level_margins_long_breakout_entry_early():
    # Breakout trigger fires slightly BEFORE the exact level -> toward current price = DOWN.
    from orchestrator import _with_level_margins
    d = _with_level_margins(_decision(action="BUY_ON_BREAKOUT", entry=200.0, stop=190.0,
                                      target1=220.0))
    assert d.entry == pytest.approx(199.5)       # -0.25%
    assert d.stop_loss == pytest.approx(189.335) # -0.35%
    assert d.target1 == pytest.approx(218.0)     # 200 + 20*0.90


def test_level_margins_are_configurable():
    from orchestrator import _with_level_margins
    # custom breathing space: entry nudge 0.5%, stop widen 1%, target shave 20%
    d = _with_level_margins(_decision(action="BUY_ON_PULLBACK", entry=100.0, stop=95.0,
                                      target1=110.0),
                            entry_tol_pct=0.5, stop_tol_pct=1.0, target_shave_pct=20.0)
    assert d.entry == pytest.approx(100.5)       # +0.5%
    assert d.stop_loss == pytest.approx(94.05)   # -1.0% (wider)
    assert d.target1 == pytest.approx(108.0)     # 100 + 10*0.80


def test_place_entry_uses_config_breathing_space():
    # A wider configured stop-widen changes the stop actually stored on the placed position.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3, capital_per_position=10000.0)
    store.update_config(stop_tolerance_pct=1.0)   # widen stop by 1% (default is 0.35%)
    orch = _orch(store, _FakeClient(),
                 _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                       entry=100.0, stop=95.0, target1=115.0)),
                 {"RELIANCE": _indic()}, candidates=_cands("RELIANCE"))
    orch._screen_and_enter(store.start_run("paper"))
    p = store.get_open_positions()[0]
    assert p.stop_loss == pytest.approx(95.0 * (1 - 0.01))   # 94.05, config's 1% widen


def test_level_margins_short_and_market_entry_untouched():
    from orchestrator import _with_level_margins
    d = _with_level_margins(_decision(action="SHORT_NOW", entry=100.0, stop=105.0,
                                      target1=90.0))
    assert d.entry == 100.0                       # market fill — entry not adjusted
    assert d.stop_loss == pytest.approx(105.3675) # widened UP for a short
    assert d.target1 == pytest.approx(91.0)       # 100 + (-10)*0.90 — closer for a short too
    n = _with_level_margins(_decision(action="BUY_NOW", entry=100.0, stop=None, target1=None))
    assert n.entry == 100.0 and n.stop_loss is None and n.target1 is None


def test_place_entry_stores_margin_adjusted_levels():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    eng = _FakeScreenEngine(results=[
        ("MRG", _decision(action="BUY_ON_PULLBACK", tq=80, conf=70, rr=2.0,
                          entry=100.0, stop=95.0, target1=110.0)),
    ])
    orch = _screen_orch(store, eng, indic_map={"MRG": _indic("MRG", last=102)})
    orch.run_cycle()
    p = store.get_pending_positions()[0]
    assert p.entry_price == pytest.approx(100.25)
    assert p.stop_loss == pytest.approx(94.6675)
    assert p.target_price == pytest.approx(109.0)   # 10% shave (was 25%)


def _live_screen_orch(store, client, screen_results=None):
    return Orchestrator(store, client, _FakeEngine(_decision(action="HOLD")),
                        get_indicators=lambda s: _indic(s, last=100),
                        get_candidates=lambda **kw: [],
                        screen_engine=_FakeScreenEngine(results=screen_results or []))


def test_reconcile_adopts_manual_mis_position():
    # Broker-first: a manually opened MIS position unknown to the DB is ADOPTED and managed.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "MANUAL", "quantity": 5, "product": "MIS", "avg_price": 101.5}])
    orch = _live_screen_orch(store, client)
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"
    pos = store.get_open_positions()
    assert len(pos) == 1
    p = pos[0]
    assert p.symbol == "MANUAL" and p.side == "LONG" and p.quantity == 5
    assert p.entry_price == pytest.approx(101.5)
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any(r.symbol == "MANUAL" and r.action == "ADOPTED" for r in recs)


def test_reconcile_adopts_manual_short_and_skips_cnc():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "SHRT", "quantity": -3, "product": "MIS", "avg_price": 100.0},
        {"symbol": "DELIV", "quantity": -10, "product": "CNC", "avg_price": 300.0}])
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    pos = store.get_open_positions()
    assert [(p.symbol, p.side, p.quantity) for p in pos] == [("SHRT", "SHORT", 3)]


def test_reconcile_shrinks_partial_manual_exit():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 4, "product": "MIS", "avg_price": 100.0}])
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert store.get_position(pid).quantity == 4       # DB synced to broker reality


def test_reconcile_excludes_manual_open_order_symbols_from_entries():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live")
    client.open_orders = [{"symbol": "ORD", "order_id": "G9", "status": "APPROVED",
                           "transaction_type": "BUY"}]
    screen = _FakeScreenEngine(results=[])
    orch = Orchestrator(store, client, _FakeEngine(_decision(action="HOLD")),
                        get_indicators=lambda s: _indic(s, last=100),
                        get_candidates=lambda **kw: [], screen_engine=screen)
    orch.run_cycle()
    assert screen.calls == [["ORD"]]                    # manual open order symbol excluded


# ---- disciplined scale-in (add to a losing position, total risk still capped, pool-safe) -----

def test_scale_in_adds_on_dip_when_engine_reaffirms():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=50000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=100.0, target_price=112.0, stop_loss=95.0,
                              mode="paper")
    # underwater at 98 (>0.5% drawdown) but above the 95 stop; engine re-affirms the long
    reaffirm = _decision(action="BUY_NOW", tq=80, conf=75, rr=2.0, entry=98.0, stop=95.0,
                         target1=112.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(reaffirm),
                 {"AAA": _indic("AAA", last=98.0)})
    run_id = store.start_run("paper")
    orch._manage_one(run_id, store.get_position(pid))
    p = store.get_position(pid)
    assert p.status == "OPEN" and p.quantity > 100          # added
    assert p.stop_loss == 95.0                              # stop NEVER widened
    assert p.entry_price < 100.0                            # average pulled down
    # combined risk to the (unchanged) stop stays within the MAX_RISK_PER_TRADE_PCT (2.5%) ceiling
    # of the 100k pool = 2500
    assert p.quantity * (p.entry_price - p.stop_loss) <= 2500.0 + 1e-6


def test_scale_in_never_exceeds_pool():
    # Tiny pool, big position already: an add must be capped by free pool, never over-commit.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=10000.0, max_open_positions=1,
         capital_per_position=10000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=99,
                              entry_price=100.0, target_price=112.0, stop_loss=95.0,
                              mode="paper")   # 9900 deployed of 10000 pool
    reaffirm = _decision(action="BUY_NOW", tq=80, conf=75, rr=2.0, entry=98.0, stop=95.0,
                         target1=112.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(reaffirm),
                 {"AAA": _indic("AAA", last=98.0)})
    run_id = store.start_run("paper")
    orch._manage_one(run_id, store.get_position(pid))
    p = store.get_position(pid)
    # free pool was 100 -> at ~98/share only 1 share fits; committed capital must not exceed pool
    assert store.committed_capital() <= 10000.0 + 1e-6


def test_no_scale_in_below_stop():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=50000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=100.0, target_price=112.0, stop_loss=95.0,
                              mode="paper")
    reaffirm = _decision(action="BUY_NOW", tq=80, conf=75, entry=94.0, stop=95.0, target1=112.0)
    # LTP 94 is BELOW the 95 stop -> must exit/He managed, never add
    orch = _orch(store, _FakeClient(), _FakeEngine(reaffirm), {"AAA": _indic("AAA", last=94.0)})
    run_id = store.start_run("paper")
    orch._manage_one(run_id, store.get_position(pid))
    assert store.get_position(pid).quantity == 100         # no add


def test_no_scale_in_when_not_reaffirmed_or_in_profit():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=50000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=100.0, target_price=112.0, stop_loss=95.0,
                              mode="paper")
    # engine says WAIT (no re-affirmation) even though underwater
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="WAIT")),
                 {"AAA": _indic("AAA", last=98.0)})
    run_id = store.start_run("paper")
    orch._manage_one(run_id, store.get_position(pid))
    assert store.get_position(pid).quantity == 100
    # in PROFIT (101 > entry) with a re-affirm -> still no add (scale-in is dip-only)
    store2 = Store(":memory:")
    _cfg(store2, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=50000.0)
    pid2 = store2.open_position(symbol="BBB", exchange="NSE", side="LONG", quantity=100,
                               entry_price=100.0, target_price=112.0, stop_loss=95.0,
                               mode="paper")
    reaffirm = _decision(action="BUY_NOW", tq=80, conf=75, entry=101.0, stop=95.0, target1=112.0)
    orch2 = _orch(store2, _FakeClient(), _FakeEngine(reaffirm), {"BBB": _indic("BBB", last=101.0)})
    rid2 = store2.start_run("paper")
    orch2._manage_one(rid2, store2.get_position(pid2))
    assert store2.get_position(pid2).quantity == 100


def test_scale_in_self_limits_to_one_add():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=50000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=100.0, target_price=112.0, stop_loss=95.0,
                              mode="paper")
    reaffirm = _decision(action="BUY_NOW", tq=80, conf=75, rr=2.0, entry=98.0, stop=95.0,
                         target1=112.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(reaffirm), {"AAA": _indic("AAA", last=98.0)})
    run_id = store.start_run("paper")
    orch._manage_one(run_id, store.get_position(pid))
    qty_after_first = store.get_position(pid).quantity
    assert qty_after_first > 100
    # second cycle, same dip: risk budget already spent -> no further add
    orch._manage_one(run_id, store.get_position(pid))
    assert store.get_position(pid).quantity == qty_after_first


# --- Broker exit bracket (exit_mode db_only / armed / on_fill, LIVE-only) -----------------------
# defaults: profit-taking 7%/15% at 5x; for a 100-entry long -> bracket target = min(103, structural
# target), stop = the position stop; band 1.0%.

def _live_pos(store, qty=100, entry=100.0, target=110.0, stop=95.0):
    return store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=qty,
                               entry_price=entry, target_price=target, stop_loss=stop, mode="live")


def test_on_fill_places_full_bracket():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.5)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert p.broker_stop_price == pytest.approx(95.0)
    assert p.broker_target_price == pytest.approx(103.0)          # min(full-profit 103, target 110)
    by_type = {o["order_type"]: o for o in client.orders}
    assert by_type["SL_M"]["trigger_price"] == pytest.approx(95.0)
    assert by_type["SL_M"]["transaction_type"] == "SELL" and by_type["SL_M"]["quantity"] == 100
    assert by_type["LIMIT"]["price"] == pytest.approx(103.0)
    assert by_type["LIMIT"]["transaction_type"] == "SELL"


def test_db_only_places_no_bracket():
    store = Store(":memory:")
    store.update_config(mode="live")                             # exit_mode db_only (default)
    pid = _live_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.5)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert p.broker_stop_order_id is None and p.broker_target_order_id is None
    assert client.orders == []


def test_paper_mode_places_no_bracket():
    store = Store(":memory:")
    store.update_config(mode="paper", exit_mode="on_fill")       # eager, but paper -> soft only
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=100,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    client = _FakeClient(mode="paper")
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.5)})._manage_positions(store.start_run("paper"))
    assert client.orders == [] and store.get_position(pid).broker_stop_order_id is None


def test_armed_places_only_when_near_a_level():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="armed", profit_book_enabled=False)  # bracket = 95/110
    pid = _live_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    # mid-range: not within 1% of the stop (95) or target (110) -> nothing placed
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.0)})._manage_positions(store.start_run("live"))
    assert store.get_position(pid).broker_target_order_id is None
    # within 1% of the target -> place the full bracket
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=109.5)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert p.broker_target_price == pytest.approx(110.0) and p.broker_stop_price == pytest.approx(95.0)


def test_bracket_target_fill_closes_and_cancels_stop():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store, qty=100)
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 103.0)
    client = _FakeClient(mode="live", order_status="EXECUTED")   # target checked first -> fills
    exits = _orch(store, client, _FakeEngine(_decision(action="HOLD")),
                  {"RELIANCE": _indic(last=103)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert exits == 1 and p.status == "CLOSED" and p.exit_reason == "TAKE_PROFIT"
    assert p.exit_price == pytest.approx(103.0)
    assert p.realized_pnl == pytest.approx((103 - 100) * 100)
    assert "SL-1" in client.cancelled                            # OCO: the other leg cancelled


class _StopFilledClient(_FakeClient):
    def get_order_status(self, order_id):
        return {"order_id": order_id, "status": "EXECUTED" if order_id == "SL-1" else "OPEN"}


def test_bracket_stop_fill_closes_and_cancels_target():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store, qty=100)
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 103.0)
    client = _StopFilledClient(mode="live")
    exits = _orch(store, client, _FakeEngine(_decision(action="HOLD")),
                  {"RELIANCE": _indic(last=95)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert exits == 1 and p.exit_reason == "STOP" and p.exit_price == pytest.approx(95.0)
    assert p.realized_pnl == pytest.approx((95 - 100) * 100)
    assert "TG-1" in client.cancelled


def test_square_off_cancels_bracket():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store)
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 103.0)
    client = _FakeClient(mode="live", order_status="OPEN")       # legs resting, not filled
    exits = _orch(store, client, _FakeEngine(_decision(action="HOLD")),
                  {"RELIANCE": _indic(last=101, bars=0, mins=0)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert exits == 1 and p.exit_reason == "SQUARE_OFF"
    assert "SL-1" in client.cancelled and "TG-1" in client.cancelled
    assert p.broker_stop_order_id is None and p.broker_target_order_id is None


def test_poll_stop_suppressed_while_stop_leg_live():
    """A live broker stop OWNS the downside — the poll must not also market-exit (no double-sell)."""
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store, stop=98.0)
    store.set_bracket_leg(pid, "stop", "SL-1", 98.0)
    client = _FakeClient(mode="live", order_status="OPEN")       # stop not filled yet
    exits = _orch(store, client, _FakeEngine(_decision(action="HOLD")),
                  {"RELIANCE": _indic(last=97)})._manage_positions(store.start_run("live"))
    assert exits == 0 and store.get_position(pid).status == "OPEN"


def test_db_only_tears_down_existing_bracket():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="db_only")
    pid = _live_pos(store)
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 103.0)
    client = _FakeClient(mode="live", order_status="OPEN")
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.5)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert "SL-1" in client.cancelled and "TG-1" in client.cancelled
    assert p.broker_stop_order_id is None and p.broker_target_order_id is None


def test_partial_book_resizes_bracket():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")        # profit-taking default 7%/15%
    pid = _live_pos(store, qty=100)
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 103.0)
    client = _FakeClient(mode="live", order_status="OPEN")
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=102)})._manage_positions(store.start_run("live"))  # +2% = 10% -> book half
    p = store.get_position(pid)
    assert p.partial_booked is True and p.quantity == 50
    assert "SL-1" in client.cancelled and "TG-1" in client.cancelled     # old bracket torn down
    assert p.broker_stop_order_id is not None and p.broker_target_order_id is not None   # rebuilt
    assert p.broker_stop_price == pytest.approx(100.0)          # runner stop trailed to breakeven


def test_reconcile_cnc_holding_does_not_mask_closed_mis_position():
    # A delivery holding in the SAME symbol must not keep a fully-exited MIS position alive:
    # net qty for the sync decision is MIS-only.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 50, "product": "CNC", "avg_price": 90.0}])
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "CLOSED" and p.exit_reason == "BROKER_SYNC"


def test_broker_state_splits_mis_and_reports_orders():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "AAA", "quantity": 5, "product": "MIS", "avg_price": 101.0},
        {"symbol": "AAA", "quantity": 40, "product": "CNC", "avg_price": 90.0},
        {"symbol": "BBB", "quantity": -3, "product": "MIS", "avg_price": 55.0}])
    client.open_orders = [
        {"symbol": "AAA", "order_id": "G1", "status": "APPROVED",
         "transaction_type": "SELL", "product": "MIS"},
        {"symbol": "ZZZ", "order_id": "G2", "status": "EXECUTED",
         "transaction_type": "BUY", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    mis, orders = orch._broker_state()
    assert mis == {"AAA": {"net": 5, "avg": 101.0}, "BBB": {"net": -3, "avg": 55.0}}
    assert [o["order_id"] for o in orders] == ["G1"]      # EXECUTED is terminal, filtered out


def test_broker_state_returns_none_orders_when_order_book_fails():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="live")
    def boom():
        raise RuntimeError("gateway 502")
    client.get_open_orders = boom
    orch = _live_screen_orch(store, client)
    mis, orders = orch._broker_state()
    assert mis == {}
    assert orders is None          # unavailable != empty; takeover must stay disabled


def test_paper_mode_reconciles_nothing():
    # Paper has no broker to reconcile against — the DB is the only ledger. A broker payload
    # must be ignored entirely, never adopted.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    client = _FakeClient(mode="paper", broker_positions=[
        {"symbol": "MANUAL", "quantity": 5, "product": "MIS", "avg_price": 101.5}])
    orch = _live_screen_orch(store, client)
    run_id = store.start_run("paper")
    assert orch._reconcile_broker(run_id) == 0
    assert store.get_open_positions() == []


def test_reconcile_manual_exit_cancels_bracket_and_oco():
    # The bot's resting stop must NOT survive the user's manual exit — it would fire against
    # shares we no longer hold and open a naked reverse position.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              oco_order_id="OCO-1", mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 110.0)
    client = _FakeClient(mode="live", broker_positions=[])     # flat at the broker
    orch = _live_screen_orch(store, client)
    orch.run_cycle()
    assert set(client.cancelled) == {"SL-1", "TG-1"}
    assert client.cancelled_ocos == ["OCO-1"]
    p = store.get_position(pid)
    assert p.status == "CLOSED" and p.exit_reason == "BROKER_SYNC"


def test_reconcile_absorbs_manual_add_at_blended_entry():
    # Bot holds 10, user manually buys 15 more -> the bot manages all 25 at the broker's
    # blended average, and the existing stop is NOT loosened.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 25, "product": "MIS", "avg_price": 102.0}])
    orch = _live_screen_orch(store, client)
    orch._reconcile_broker(store.start_run("live"))     # reconcile in isolation
    p = store.get_position(pid)
    assert p.quantity == 25
    assert p.entry_price == pytest.approx(102.0)     # true blended cost basis
    assert p.stop_loss == 95.0                       # never loosened
    assert "SL-1" in client.cancelled                # stale-size leg torn down


def test_reconcile_partial_exit_cancels_stale_size_legs():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    store.set_bracket_leg(pid, "target", "TG-1", 110.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 4, "product": "MIS", "avg_price": 100.0}])
    orch = _live_screen_orch(store, client)
    # reconcile in ISOLATION: a full cycle would also tear the bracket down via _manage_one's
    # db_only cleanup, which would mask whether reconcile itself did its job.
    orch._reconcile_broker(store.start_run("live"))
    assert store.get_position(pid).quantity == 4
    assert set(client.cancelled) == {"SL-1", "TG-1"}   # legs for the OLD size are gone


def test_reconcile_side_flip_closes_old_and_adopts_new():
    # Bot LONG 10; user sells 20 -> broker is SHORT 10. Old trade booked, new side adopted.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": -10, "product": "MIS", "avg_price": 104.0}])
    orch = _live_screen_orch(store, client)
    # reconcile in isolation — a full cycle would go on to manage (and here profit-book) the
    # freshly adopted short, which is correct but hides what this test is about.
    run_id = store.start_run("live")
    orch._reconcile_broker(run_id)
    old = store.get_position(pid)
    assert old.status == "CLOSED" and old.exit_reason == "BROKER_SYNC"
    fresh = store.get_open_positions()
    assert len(fresh) == 1
    assert (fresh[0].symbol, fresh[0].side, fresh[0].quantity) == ("BOT", "SHORT", 10)
    assert fresh[0].entry_price == pytest.approx(104.0)
    recs = store.get_decisions_for_run(run_id)
    assert any(r.symbol == "BOT" and r.action == "ADOPTED" for r in recs)


def test_reconcile_matching_size_is_a_noop():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    orch = _live_screen_orch(store, client)
    orch._reconcile_broker(store.start_run("live"))     # reconcile in isolation
    p = store.get_position(pid)
    assert p.status == "OPEN" and p.quantity == 10
    assert client.cancelled == []                # nothing drifted -> nothing torn down


def _held_long(store, symbol="BOT", qty=10):
    return store.open_position(symbol=symbol, exchange="NSE", side="LONG", quantity=qty,
                               entry_price=100.0, target_price=110.0, stop_loss=95.0,
                               mode="live")


def test_takeover_cancels_manual_exit_order_and_forces_bracket():
    # The user's own SL on a symbol the bot manages is cancelled; the bot then rests its OWN
    # bracket at the analysed level even though exit_mode is db_only.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    pid = _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [{"symbol": "BOT", "order_id": "USER-SL", "status": "APPROVED",
                           "transaction_type": "SELL", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    summary = orch.run_cycle()
    assert "USER-SL" in client.cancelled
    p = store.get_position(pid)
    assert p.force_bracket is True
    assert p.broker_stop_order_id is not None      # replaced, not merely removed
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any(r.action == "ADJUSTED" and "USER-SL" in (r.reason or "") for r in recs)


def test_takeover_never_cancels_cnc_orders():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    pid = _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [
        {"symbol": "BOT", "order_id": "CNC-SELL", "status": "APPROVED",
         "transaction_type": "SELL", "product": "CNC"},
        {"symbol": "BOT", "order_id": "UNKNOWN-SELL", "status": "APPROVED",
         "transaction_type": "SELL", "product": None}]
    orch = _live_screen_orch(store, client)
    orch._reconcile_broker(store.start_run("live"))
    assert client.cancelled == []                   # delivery orders are untouchable
    assert store.get_position(pid).force_bracket is False


def test_takeover_leaves_entry_side_orders_resting():
    # A manual BUY on a LONG is a pending ADD, not an exit — absorbed next cycle if it fills.
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [{"symbol": "BOT", "order_id": "USER-BUY", "status": "APPROVED",
                           "transaction_type": "BUY", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    orch._reconcile_broker(store.start_run("live"))
    assert client.cancelled == []


def test_takeover_ignores_the_bots_own_bracket_legs():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="on_fill")
    pid = _held_long(store)
    store.set_bracket_leg(pid, "stop", "SL-1", 95.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    client.open_orders = [{"symbol": "BOT", "order_id": "SL-1", "status": "APPROVED",
                           "transaction_type": "SELL", "product": "MIS"}]
    orch = _live_screen_orch(store, client)
    orch._reconcile_broker(store.start_run("live"))
    assert "SL-1" not in client.cancelled           # our own leg is not "foreign"


def test_takeover_disabled_when_order_book_read_fails():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, exit_mode="db_only")
    pid = _held_long(store)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 100.0}])
    def boom():
        raise RuntimeError("gateway 502")
    client.get_open_orders = boom
    orch = _live_screen_orch(store, client)
    orch._reconcile_broker(store.start_run("live"))
    assert client.cancelled == []                   # unknown order book -> cancel nothing
    assert store.get_position(pid).force_bracket is False
