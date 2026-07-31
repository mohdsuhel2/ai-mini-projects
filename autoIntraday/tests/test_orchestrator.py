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

    def decide(self, symbol, indicators, position=None, book=None):
        self.calls.append((symbol, position))
        return self.decision


def _indic(symbol="RELIANCE", last=100.0, high=100.0, low=100.0, bars=5, mins=120, live=None):
    price = {"last": last, "day_high": high, "day_low": low}
    if live is not None:
        price["live"] = live
    return {"symbol": symbol, "price": price,
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


# --- LTP source: the live tick, not the last closed bar --------------------------------------
# The engine LABELS on completed 15m bars, but positions must be managed against the tape NOW.
# `price.last` can be a full bar old, which made every soft exit fire up to 15 min late.

def test_ltp_prefers_the_live_tick_over_the_closed_bar():
    from orchestrator import _ltp
    assert _ltp(_indic(last=100.0, live=103.5)) == 103.5


def test_ltp_falls_back_to_the_closed_bar_when_live_is_unusable():
    from orchestrator import _ltp
    assert _ltp(_indic(last=100.0)) == 100.0                    # feed gave no live tick
    assert _ltp(_indic(last=100.0, live=None)) == 100.0         # present but null
    assert _ltp(_indic(last=100.0, live=0)) == 100.0            # non-positive
    assert _ltp(_indic(last=100.0, live=-5)) == 100.0


def test_ltp_rejects_an_implausible_live_tick():
    from orchestrator import _ltp, LIVE_MAX_DRIFT_PCT
    assert LIVE_MAX_DRIFT_PCT == 20.0
    # Within one 15m bar a >20% gap from the closed bar is a bad tick, not a real move.
    assert _ltp(_indic(last=100.0, live=500.0)) == 100.0
    assert _ltp(_indic(last=100.0, live=1.0)) == 100.0
    assert _ltp(_indic(last=100.0, live=119.0)) == 119.0        # a big-but-real move still passes


def test_manage_stops_out_on_the_live_tick_while_the_closed_bar_is_still_safe():
    # THE point of the change: closed bar 96 is above the 95 stop, the tape is already at 94.
    # Before, this position survived until the next 15m close and gave back the difference.
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=96, live=94, high=112, low=93)})
    run_id = store.start_run("paper")
    orch._manage_positions(run_id)
    p = store.get_position(pid)
    assert p.exit_reason == "STOP" and p.exit_price == 94.0


def test_manage_takes_target_on_the_live_tick_for_a_short():
    # Target kept inside the profit-book bands (2.6% move = 13% on margin at 5x) so this asserts
    # the TARGET path, not take-profit.
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="SHORT", quantity=10,
                              entry_price=100.0, target_price=97.5, stop_loss=105.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=98.2, live=97.4, high=101, low=97)})
    run_id = store.start_run("paper")
    orch._manage_positions(run_id)
    p = store.get_position(pid)
    assert p.exit_reason == "TARGET" and p.exit_price == 97.4


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


def test_enter_rejects_when_geometric_rr_below_floor():
    # The engine self-reports rr=2.0 and the first gate passes, but the ACTUAL geometry is ~1.05 —
    # the geometric re-gate recomputes it and rejects the trade. With the default rr_gate_pre_margin
    # the gate judges the RAW levels, which are already below 1.5 here regardless of the margins.
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
    assert any("R:R" in r and "< 1.5" in r for r in reasons)


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
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0,
                              mode="paper")
    client = _FakeClient()
    # at +1R (risk 3, ltp 103.2): HOLD with a tighter stop and higher target -> both move
    engine = _FakeEngine(_decision(action="HOLD", stop=101.0, target1=112.0))
    orch = _orch(store, client, engine, {"AAA": _indic("AAA", last=103.2, high=103.5, low=100)})
    summary = orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert p.stop_loss == 101.0                      # ratcheted up
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


class _OrigEntryFilledClient(_FakeClient):
    """get_order_status reports the ORIGINAL resting entry (ENT-1) FILLED and every replacement
    order still OPEN — the between-cycles fill that _refresh_pending's cancel+replace churn used
    to orphan (TIL, 2026-07-29)."""
    def get_order_status(self, order_id):
        return {"order_id": order_id, "status": "EXECUTED" if order_id == "ENT-1" else "OPEN"}


def test_live_pending_fill_armed_before_refresh_churn():
    # A resting LIMIT entry that FILLED between cycles must be ARMED, not cancelled+replaced by
    # _refresh_pending — cancel_order silently succeeds on an already-filled order, so the churn
    # orphaned the real fill and no exit was ever placed (TIL, 2026-07-29).
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=5,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=100,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="live",
                              status="PENDING", entry_order_id="ENT-1", trigger_kind="LIMIT")
    client = _OrigEntryFilledClient(mode="live")
    # The engine re-quotes to a MOVED same-side level — exactly the read that triggers the
    # cancel+replace churn. The ENT-1 fill must still win.
    engine = _FakeEngine(_decision(action="BUY_ON_PULLBACK", tq=80, rr=2.0, conf=75,
                                   entry=103.0, stop=98.0, target1=113.0))
    orch = _orch(store, client, engine, {"AAA": _indic("AAA", last=100.5)})
    fills, filled_ids = orch._resolve_pending(store.start_run("live"))
    p = store.get_position(pid)
    assert fills == 1 and pid in filled_ids
    assert p.status == "OPEN" and p.entry_price == pytest.approx(100.0)
    assert "ENT-1" not in client.cancelled   # the filled order was never churned away


def test_rr_gate_pre_margin_passes_trade_and_orders_use_margined_levels():
    # Default (rr_gate_pre_margin=True): the R:R gate judges the RAW skill geometry; the execution
    # margins only shape the actual orders and must NOT veto an otherwise-good trade. Raw RR here is
    # 8/5 = 1.6 (passes 1.5); post-margin it is ~1.35 (would have been rejected).
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0)
    d = _decision(action="BUY_NOW", tq=80, rr=1.6, conf=75, entry=100.0, stop=95.0, target1=108.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(d), {"AAA": _indic("AAA", last=100.0)})
    ok = orch._place_entry(store.start_run("paper"), "AAA", d, _indic("AAA", last=100.0), "paper")
    assert ok is True
    p = store.get_open_positions()[0]
    # the trade was taken on raw R:R, but the order/position carries the MARGINED levels
    assert p.stop_loss == pytest.approx(95.0 * (1 - 0.35 / 100))          # widened stop
    assert p.target_price == pytest.approx(100.0 + (108.0 - 100.0) * (1 - 10 / 100))  # shaved target


def test_rr_gate_post_margin_rejects_when_flag_disabled():
    # Flag off restores the stricter behaviour: the shaved target / widened stop must still clear
    # 1.5:1 AFTER margins — the same setup is now rejected on its ~1.35 post-margin R:R.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0, rr_gate_pre_margin=False)
    d = _decision(action="BUY_NOW", tq=80, rr=1.6, conf=75, entry=100.0, stop=95.0, target1=108.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(d), {"AAA": _indic("AAA", last=100.0)})
    ok = orch._place_entry(store.start_run("paper"), "AAA", d, _indic("AAA", last=100.0), "paper")
    assert ok is False and store.get_open_positions() == []


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
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=112.0, stop_loss=97.0, mode="paper")
    # at +1R: engine proposes a NEARER target (108 < 112) — ignored; stop 101 still ratchets up
    engine = _FakeEngine(_decision(action="HOLD", stop=101.0, target1=108.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"AAA": _indic("AAA", last=103.2, high=103.5, low=100)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.target_price == 112.0     # target NOT pulled in
    assert p.stop_loss == 101.0        # stop still ratcheted up


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
    """Rounds to the SYMBOL'S tick, not a fixed 0.05. Groww's master gives most large NSE names a
    0.10 tick; a 0.05-aligned price is invalid there ("choose price in multiples of the tick
    size", 2026-07-31). Unknown symbols use the coarser 0.10, which is always safe."""
    from orchestrator import _tick
    assert _tick(188.6 * 1.005, "63MOONS") == 189.55        # 0.05 tick
    assert _tick(100.03, "63MOONS") == 100.05
    assert _tick(4399.95, "NETWEB") == 4399.9               # 0.10 tick
    assert _tick(100.02) == 100.0                           # unknown -> 0.10 fallback
    assert _tick(100.03) == 100.0


def test_trail_pushes_new_levels_to_broker_oco():
    """When the per-cycle re-check moves the stop/target, the BROKER's OCO must be modified too
    — a stop trailed only in the DB protects nothing between cycles (user req 2026-07-16)."""
    from orchestrator import _tick
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0,
                              mode="live", oco_order_id="OCO-9")
    client = _FakeClient(mode="live",
                         broker_positions=[{"symbol": "A", "quantity": 10, "product": "MIS",
                                            "avg_price": 100.0}])
    engine = _FakeEngine(_decision(action="HOLD", stop=101.0, target1=112.0))
    orch = _orch(store, client, engine, {"A": _indic("A", last=103.2, high=103.5, low=100)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.stop_loss == 101.0 and p.target_price == 112.0       # DB updated
    assert client.modified_ocos == [{"order_id": "OCO-9", "target": _tick(112.0),
                                     "stop_loss": _tick(101.0)}]  # broker updated too


def test_trail_broker_modify_failure_keeps_cycle_alive():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0,
                              mode="live", oco_order_id="OCO-9")

    class BoomModify(_FakeClient):
        def modify_oco_order(self, order_id, target, stop_loss):
            raise RuntimeError("modify API mismatch")

    client = BoomModify(mode="live",
                        broker_positions=[{"symbol": "A", "quantity": 10, "product": "MIS",
                                           "avg_price": 100.0}])
    engine = _FakeEngine(_decision(action="HOLD", stop=101.0, target1=112.0))
    orch = _orch(store, client, engine, {"A": _indic("A", last=103.2, high=103.5, low=100)})
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"                # cycle survives
    assert summary["errors"] == 1                        # surfaced -> job notification
    assert store.get_position(pid).stop_loss == 101.0    # DB still trailed (cycle exits honor it)


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


def test_on_fill_places_stop_only():
    # A full-qty target LIMIT and a full-qty stop SL_M cannot co-rest on one MIS holding — Groww
    # keeps the LIMIT and auto-cancels the SL_M, leaving the position naked on the downside and
    # flooding the order book (verified 2026-07-29). So ONLY the protective stop rests at the
    # broker; the target is taken by the soft cycle-level take-profit instead.
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.5)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert p.broker_stop_price == pytest.approx(95.0)
    assert p.broker_target_order_id is None and p.broker_target_price is None
    assert [o["order_type"] for o in client.orders] == ["SL_M"]   # ONLY the stop — no target LIMIT
    sl = client.orders[0]
    assert sl["trigger_price"] == pytest.approx(95.0)
    assert sl["transaction_type"] == "SELL" and sl["quantity"] == 100


def test_on_fill_target_taken_softly_when_no_target_leg():
    # With only the stop resting, the target is enforced by the soft cycle-level take-profit:
    # price at/above the full-profit level exits at market (no broker target leg to own it).
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store, entry=100.0, target=110.0, stop=95.0)
    client = _FakeClient(mode="live", order_status="OPEN")
    # ltp 103 == the full-profit level (15% on margin at 5x = +3%): soft full take-profit fires
    exits = _orch(store, client, _FakeEngine(_decision(action="HOLD")),
                  {"RELIANCE": _indic(last=103.0)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert exits == 1 and p.status == "CLOSED" and p.exit_reason == "TAKE_PROFIT"
    assert p.exit_price == pytest.approx(103.0)


def test_ensure_bracket_tears_down_stale_target_leg():
    # A target leg that somehow exists (legacy row / a leg placed before this change) is cancelled
    # on the next manage cycle — only the stop is allowed to rest.
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="on_fill")
    pid = _live_pos(store)
    store.set_bracket_leg(pid, "target", "TG-OLD", 103.0)
    client = _FakeClient(mode="live", order_status="OPEN")
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.5)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert "TG-OLD" in client.cancelled                          # stale target leg torn down
    assert p.broker_target_order_id is None
    assert p.broker_stop_price == pytest.approx(95.0)            # protective stop still rests


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


def test_armed_places_stop_only_when_near_the_stop():
    store = Store(":memory:")
    store.update_config(mode="live", exit_mode="armed", profit_book_enabled=False)  # stop = 95
    pid = _live_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    # mid-range: not within 1% of the stop (95) -> nothing placed
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=100.0)})._manage_positions(store.start_run("live"))
    assert store.get_position(pid).broker_stop_order_id is None
    assert client.orders == []
    # within 1% of the stop -> place the protective stop (still no target leg)
    _orch(store, client, _FakeEngine(_decision(action="HOLD")),
          {"RELIANCE": _indic(last=95.5)})._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert p.broker_stop_price == pytest.approx(95.0)
    assert p.broker_target_order_id is None
    assert [o["order_type"] for o in client.orders] == ["SL_M"]


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
    assert p.broker_stop_order_id is not None                   # stop rebuilt at the new size
    assert p.broker_target_order_id is None                     # stop only — no target leg
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


def _stopless_orch(store, client):
    # WAIT with no levels — exactly the read that used to leave an adopted position naked.
    return Orchestrator(store, client, _FakeEngine(_decision(action="WAIT", stop=None,
                                                             target1=None)),
                        get_indicators=lambda s: _indic(s, last=200),
                        get_candidates=lambda **kw: [],
                        screen_engine=_FakeScreenEngine(results=[]))


def test_adopted_position_gets_fallback_stop_when_engine_gives_none():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=1.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "MANUAL", "quantity": 5, "product": "MIS", "avg_price": 200.0}])
    _stopless_orch(store, client).run_cycle()
    p = store.get_open_positions()[0]
    assert p.stop_loss == pytest.approx(198.0)     # 1% below a 200.0 entry


def test_fallback_stop_is_never_widened_by_a_later_read():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=1.0)
    pid = store.open_position(symbol="BOT", exchange="NSE", side="LONG", quantity=10,
                              entry_price=200.0, target_price=None, stop_loss=199.0,
                              mode="live")
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "BOT", "quantity": 10, "product": "MIS", "avg_price": 200.0}])
    _stopless_orch(store, client).run_cycle()
    assert store.get_position(pid).stop_loss == 199.0   # a real stop is left alone


def test_fallback_stop_disabled_at_zero_pct():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=0.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "MANUAL", "quantity": 5, "product": "MIS", "avg_price": 200.0}])
    _stopless_orch(store, client).run_cycle()
    assert store.get_open_positions()[0].stop_loss is None


def test_fallback_stop_is_above_entry_for_a_short():
    store = Store(":memory:")
    _cfg(store, mode="live", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, adopt_fallback_stop_pct=1.0)
    client = _FakeClient(mode="live", broker_positions=[
        {"symbol": "MANUAL", "quantity": -5, "product": "MIS", "avg_price": 200.0}])
    _stopless_orch(store, client).run_cycle()
    p = store.get_open_positions()[0]
    assert p.side == "SHORT" and p.stop_loss == pytest.approx(202.0)


# --- Scale-into-strength (pyramiding into persisting winners) ---------------------------------

def _pyr_cfg(store, **kw):
    base = dict(mode="live", total_pool=250000.0, max_open_positions=5,
                capital_per_position=30000.0, pyramid_enabled=True)
    base.update(kw)
    store.update_config(**base)


def _pyr_pos(store, qty=100, entry=100.0):
    return store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=qty,
                               entry_price=entry, target_price=140.0, stop_loss=95.0,
                               mode="live", status="OPEN")


def test_pyramid_adds_after_two_persisting_strong_reaffirms():
    store = Store(":memory:")
    _pyr_cfg(store)                                            # confirm_cycles=2 (default)
    pid = _pyr_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    strong = _decision(action="BUY_NOW", tq=85, conf=80, entry=100.0, stop=95.0, target1=140.0)
    orch = _orch(store, client, _FakeEngine(strong), {"AAA": _indic(last=101.0)})
    orch._manage_positions(store.start_run("live"))           # cycle 1: persistence 1/2, no add
    p = store.get_position(pid)
    assert p.pyramid_signal_count == 1 and p.pyramid_count == 0 and p.quantity == 100
    orch._manage_positions(store.start_run("live"))           # cycle 2: confirmed -> add
    p = store.get_position(pid)
    assert p.pyramid_count == 1 and p.pyramid_signal_count == 0
    assert p.quantity > 100                                   # capital added at market
    assert p.stop_loss == pytest.approx(95.0)                 # structural stop NOT widened
    assert any(o["order_type"] == "MARKET" and o["transaction_type"] == "BUY"
               for o in client.orders)


def test_pyramid_one_off_strong_does_not_add_and_resets():
    store = Store(":memory:")
    _pyr_cfg(store)
    pid = _pyr_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    strong = _decision(action="BUY_NOW", tq=85, conf=80, entry=100.0, stop=95.0, target1=140.0)
    weak = _decision(action="HOLD", tq=60, conf=60)
    # strong (1/2) then a non-strong read -> counter resets, never reaches 2, no add
    _orch(store, client, _FakeEngine(strong), {"AAA": _indic(last=101.0)})._manage_positions(
        store.start_run("live"))
    assert store.get_position(pid).pyramid_signal_count == 1
    _orch(store, client, _FakeEngine(weak), {"AAA": _indic(last=101.0)})._manage_positions(
        store.start_run("live"))
    p = store.get_position(pid)
    assert p.pyramid_signal_count == 0 and p.pyramid_count == 0 and p.quantity == 100


def test_pyramid_respects_max_adds_ceiling():
    store = Store(":memory:")
    _pyr_cfg(store, pyramid_max_adds=2)
    pid = _pyr_pos(store)
    store.record_pyramid_add(pid)
    store.record_pyramid_add(pid)                             # already at the 2-add ceiling
    client = _FakeClient(mode="live", order_status="OPEN")
    strong = _decision(action="BUY_NOW", tq=85, conf=80, entry=100.0, stop=95.0, target1=140.0)
    orch = _orch(store, client, _FakeEngine(strong), {"AAA": _indic(last=101.0)})
    orch._manage_positions(store.start_run("live"))
    orch._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert p.pyramid_count == 2 and p.quantity == 100         # no further add
    assert not any(o["order_type"] == "MARKET" for o in client.orders)


def test_pyramid_disabled_never_adds():
    store = Store(":memory:")
    _pyr_cfg(store, pyramid_enabled=False)
    pid = _pyr_pos(store)
    client = _FakeClient(mode="live", order_status="OPEN")
    strong = _decision(action="BUY_NOW", tq=90, conf=90, entry=100.0, stop=95.0, target1=140.0)
    orch = _orch(store, client, _FakeEngine(strong), {"AAA": _indic(last=101.0)})
    orch._manage_positions(store.start_run("live"))
    orch._manage_positions(store.start_run("live"))
    p = store.get_position(pid)
    assert p.pyramid_count == 0 and p.pyramid_signal_count == 0 and p.quantity == 100


def test_pyramided_position_rides_higher_full_book():
    # A pyramided position (pyramid_count>0) uses pyramid_full_pct (40% -> +8% price); a plain one
    # uses profit_book_full_pct (19% -> +3.8%). At ltp +5% the plain one full-books, the pyramided
    # one holds. Partial disabled to isolate the full-book.
    store = Store(":memory:")
    _pyr_cfg(store, pyramid_full_pct=40.0, profit_book_full_pct=19.0, profit_book_partial_pct=0.0,
             exit_mode="db_only")
    plain = _pyr_pos(store)
    pyr = _pyr_pos(store)
    store.record_pyramid_add(pyr)                             # mark as pyramided
    client = _FakeClient(mode="live", order_status="OPEN")
    orch = _orch(store, client, _FakeEngine(_decision(action="HOLD")), {"AAA": _indic(last=105.0)})
    orch._manage_positions(store.start_run("live"))
    assert store.get_position(plain).status == "CLOSED"       # +5% > +3.8% normal full-book
    assert store.get_position(plain).exit_reason == "TAKE_PROFIT"
    assert store.get_position(pyr).status == "OPEN"           # +5% < +8% pyramided full-book


# --- R:R gate on/off (config.rr_gate_enabled) ------------------------------------------------

def test_entry_gate_rr_off_skips_risk_reward_floor():
    # With rr_gate=False, _passes_entry_gate ignores risk_reward but STILL needs quality+confidence.
    d = _decision(action="BUY_NOW", tq=80, conf=75, rr=0.5)          # R:R below floor
    assert _passes_entry_gate(d, rr_gate=True) is False              # blocked on R:R when on
    assert _passes_entry_gate(d, rr_gate=False) is True              # allowed when off
    weak = _decision(action="BUY_NOW", tq=40, conf=75, rr=0.5)       # quality too low
    assert _passes_entry_gate(weak, rr_gate=False) is False          # quality still enforced
    shy = _decision(action="BUY_NOW", tq=80, conf=40, rr=0.5)        # confidence too low
    assert _passes_entry_gate(shy, rr_gate=False) is False           # confidence still enforced


def test_place_entry_rr_disabled_takes_low_rr_trade():
    # rr_gate_enabled=False: a strong-conviction but poor-R:R trade (geo 0.3) is TAKEN.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0, rr_gate_enabled=False)
    d = _decision(action="BUY_NOW", tq=85, conf=80, rr=0.3, entry=100.0, stop=90.0, target1=103.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(d), {"AAA": _indic("AAA", last=100.0)})
    ok = orch._place_entry(store.start_run("paper"), "AAA", d, _indic("AAA", last=100.0), "paper")
    assert ok is True and len(store.get_open_positions()) == 1


def test_place_entry_rr_disabled_still_rejects_no_upside():
    # even with the gate off, a trade with target at/below entry (no reward) is invalid.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0, rr_gate_enabled=False)
    d = _decision(action="BUY_NOW", tq=85, conf=80, rr=0.3, entry=100.0, stop=95.0, target1=99.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(d), {"AAA": _indic("AAA", last=100.0)})
    run_id = store.start_run("paper")
    ok = orch._place_entry(run_id, "AAA", d, _indic("AAA", last=100.0), "paper")
    assert ok is False and store.get_open_positions() == []
    reasons = [x.reason for x in store.get_decisions_for_run(run_id) if x.reason]
    assert any("invalid geometry" in r for r in reasons)


def test_place_entry_rr_enabled_default_rejects_low_rr():
    # default (enabled): the same low-R:R trade is rejected.
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0)                               # rr_gate_enabled defaults True
    d = _decision(action="BUY_NOW", tq=85, conf=80, rr=0.3, entry=100.0, stop=90.0, target1=103.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(d), {"AAA": _indic("AAA", last=100.0)})
    ok = orch._place_entry(store.start_run("paper"), "AAA", d, _indic("AAA", last=100.0), "paper")
    assert ok is False and store.get_open_positions() == []


# ---- reversal persistence: an unconvicted opposing read must not act ------------------------
# 2026-07-30: the skill flipped bearish on a LONG and returned a SHORT plan (stop ABOVE entry).
# _maybe_trail wrote that stop onto the long because "higher than the old stop" was its only
# test, forcing an exit the conviction gate had already refused. See
# docs/superpowers/specs/2026-07-30-reversal-persistence-design.md

def test_opposes_matches_the_resting_order_definition():
    from orchestrator import _opposes
    for a in ("SELL_NOW", "SHORT_NOW"):
        assert _opposes(a, "LONG") is True
        assert _opposes(a, "SHORT") is False
    for a in ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT"):
        assert _opposes(a, "SHORT") is True
        assert _opposes(a, "LONG") is False
    for a in ("HOLD", "WAIT", "NO_TRADE"):          # neutral -> never opposing, still trails
        assert _opposes(a, "LONG") is False
        assert _opposes(a, "SHORT") is False


def test_stop_is_sane_rejects_a_stop_on_the_wrong_side_of_price():
    from orchestrator import _stop_is_sane
    assert _stop_is_sane("LONG", 2213.70, 2229.20) is True      # below price -> fine
    assert _stop_is_sane("LONG", 2250.23, 2229.20) is False     # BALKRISIND: above price
    assert _stop_is_sane("LONG", 2229.20, 2229.20) is False     # at price -> instant exit
    assert _stop_is_sane("SHORT", 990.0, 984.0) is True         # above price -> fine
    assert _stop_is_sane("SHORT", 980.0, 984.0) is False


def test_balkrisind_bearish_read_cannot_move_a_long_stop():
    """The real 2026-07-30 case: SELL_NOW q34 on a LONG, carrying a SHORT's stop of 2250.23
    while the tape was 2229.20. The stop must stay at 2213.70 — which the market never touched."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BALKRISIND", exchange="NSE", side="LONG", quantity=66,
                              entry_price=2253.98, target_price=2294.8, stop_loss=2213.70,
                              mode="paper")
    engine = _FakeEngine(_decision(action="SELL_NOW", tq=34, conf=62,
                                   entry=2229.6, stop=2250.23, target1=2213.7))
    orch = _orch(store, _FakeClient(), engine,
                 {"BALKRISIND": _indic("BALKRISIND", last=2229.2, live=2229.2,
                                       high=2240.0, low=2224.4)})
    summary = orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "OPEN"                        # q34 is below the exit floor -> no exit
    assert p.stop_loss == 2213.70                    # <- the fix: stop untouched
    assert p.target_price == 2294.8                  # target untouched too
    recs = store.get_decisions_for_run(summary["run_id"])
    assert any("opposing read" in (r.reason or "") for r in recs)


def test_pngsreva_stop_above_price_is_refused_by_the_clamp():
    """Even from a same-side read, a LONG's stop may never sit at or above the live price."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="PNGSREVA", exchange="NSE", side="LONG", quantity=338,
                              entry_price=442.764, target_price=451.625, stop_loss=436.0,
                              mode="paper")
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=452.11, target1=451.625))
    orch = _orch(store, _FakeClient(), engine,
                 {"PNGSREVA": _indic("PNGSREVA", last=438.0, live=438.0, high=445.0, low=433.0)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert p.stop_loss == 436.0                      # 452.11 refused: it sits above the tape


def test_premierene_bullish_read_cannot_move_a_short_stop():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="PREMIERENE", exchange="NSE", side="SHORT", quantity=152,
                              entry_price=984.29, target_price=962.0, stop_loss=990.5,
                              mode="paper")
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=38, conf=63,
                                   entry=984.0, stop=989.0, target1=981.5))
    orch = _orch(store, _FakeClient(), engine,
                 {"PREMIERENE": _indic("PREMIERENE", last=984.0, live=984.0,
                                       high=995.0, low=980.0)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.stop_loss == 990.5                      # untouched by the opposite-side plan


def test_convicted_two_cycle_reversal_still_exits():
    """REGRESSION GUARD: the genuine-reversal path must survive the fix. Two consecutive
    convicted flips (quality and confidence both >= 55) still close the position on SIGNAL."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    engine = _FakeEngine(_decision(action="SELL_NOW", tq=70, conf=70, stop=105.0, target1=90.0))
    indic = {"AAA": _indic("AAA", last=101, live=101, high=102, low=100)}
    orch = _orch(store, _FakeClient(), engine, indic)
    orch.run_cycle()
    assert store.get_position(pid).status == "OPEN"          # cycle 1: awaiting confirmation
    assert store.get_position(pid).stop_loss == 95.0         # and still no level write
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "CLOSED" and p.exit_reason == "SIGNAL"  # cycle 2: confirmed -> exit


def test_neutral_hold_still_trails_normally():
    """REGRESSION GUARD: the fix must not freeze legitimate post-+1R trailing on neutral reads."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0, mode="paper")
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=101.0, target1=112.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"AAA": _indic("AAA", last=103.2, live=103.2, high=103.5, low=100)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.stop_loss == 101.0 and p.target_price == 112.0


# ---- trailing noise floor: a stop may never be parked inside noise range of the tape ---------
# 2026-07-30 post-mortem "trailing-stop compression": same-side/HOLD re-quotes walked stops to
# 0.07-0.17% of the tape (ADANIPORTS 0.10%, PREMIERENE 0.07%, BALKRISIND 3.84%->0.17% -> noise
# stop-out 30 min before a +3.2% close). A trailed stop must keep MIN_STOP_DISTANCE_PCT of
# breathing room from the LIVE price: ratchet AT MOST to that floor, never inside it.

def test_hold_read_cannot_park_a_long_stop_inside_noise_of_the_tape():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0, mode="paper")
    # at +1R (ltp 103.2): HOLD quoting a stop 0.19% below the tape -> clamp to the 0.4% floor
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=103.0, target1=110.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"AAA": _indic("AAA", last=103.2, live=103.2, high=103.5, low=99.0)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert abs(p.stop_loss - 103.2 * 0.996) < 1e-6   # ratcheted, but only to the floor


def test_hold_read_cannot_park_a_short_stop_inside_noise_of_the_tape():
    """The PREMIERENE live case: SHORT with a stop re-quoted just above the tape."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="PREMIERENE", exchange="NSE", side="SHORT", quantity=152,
                              entry_price=984.29, target_price=962.0, stop_loss=990.0,
                              mode="paper")
    # at +1R (risk ~5.7, ltp 978.0): a stop re-quoted 0.1% above the tape -> clamp to the floor
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=979.0, target1=962.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"PREMIERENE": _indic("PREMIERENE", last=978.0, live=978.0,
                                       high=990.0, low=976.0)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "OPEN"
    floor = 978.0 * 1.004
    assert abs(p.stop_loss - floor) < 1e-6           # clamped to 0.4% above the tape


def test_stop_already_at_the_floor_does_not_churn():
    """A quote inside the floor when the stop already sits AT the floor -> no level write."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=104.0, stop_loss=99.6, mode="paper")
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=99.9, target1=104.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"AAA": _indic("AAA", last=100.0, live=100.0, high=100.5, low=99.0)})
    orch.run_cycle()
    assert store.get_position(pid).stop_loss == 99.6  # unchanged


def test_wide_trail_outside_the_floor_still_ratchets_normally():
    """REGRESSION GUARD: a legitimate post-+1R structural trail (outside the floor) is untouched."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0, mode="paper")
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=102.0, target1=112.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"AAA": _indic("AAA", last=103.2, live=103.2, high=103.5, low=99.0)})
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.stop_loss == 102.0 and p.target_price == 112.0   # ~1.2% off the tape -> fine


# ---- gates-aware exits: a gate-vetoed reverse read is not a credible exit signal -------------
# 2026-07-30 BALKRISIND: the engine's own analytics vetoed every bearish flip (FINOPB veto +
# "short vs bullish HTF" filter, seven flips in 4h) yet the flips still fed the SIGNAL-exit
# counter. A reverse read that the v2 desk itself vetoes must not count toward EXIT_CONFIRM_CYCLES.
# Fails OPEN: v1 indicators carry no institutional_desk -> behavior unchanged.

def _desk(finopb=False, filters=(), bias="short"):
    return {"institutional_desk": {"validated_gates": {"finopb_veto": finopb},
                                   "no_trade_filters_failed": list(filters)},
            "intraday_structure": {"directional_bias": bias}}


def test_finopb_vetoed_sell_now_does_not_exit_a_long():
    """Convicted-looking SELL_NOW (q70/c70) but the desk says the short side is FINOPB-vetoed:
    the flip must not count, both cycles, and the position rides its structural stop."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    engine = _FakeEngine(_decision(action="SELL_NOW", tq=70, conf=70, stop=105.0, target1=90.0))
    ind = _indic("AAA", last=101, live=101, high=102, low=100)
    ind.update(_desk(finopb=True, bias="short"))
    orch = _orch(store, _FakeClient(), engine, {"AAA": ind})
    orch.run_cycle()
    orch.run_cycle()                                   # would exit on cycle 2 without the veto
    p = store.get_position(pid)
    assert p.status == "OPEN"
    assert p.reverse_signal_count == 0                 # vetoed flips never accumulate


def test_htf_conflict_vetoed_buy_now_does_not_exit_a_short():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BBB", exchange="NSE", side="SHORT", quantity=10,
                              entry_price=100.0, target_price=92.0, stop_loss=104.0, mode="paper")
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=70, conf=70, stop=95.0, target1=108.0))
    ind = _indic("BBB", last=99, live=99, high=100, low=98)
    ind.update(_desk(filters=["long vs bearish HTF"], bias="long"))
    orch = _orch(store, _FakeClient(), engine, {"BBB": ind})
    orch.run_cycle()
    orch.run_cycle()
    assert store.get_position(pid).status == "OPEN"


def test_clean_gates_reverse_read_still_exits_after_confirmation():
    """REGRESSION GUARD: a desk-clean flip must still exit on the 2nd convicted cycle."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="CCC", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    engine = _FakeEngine(_decision(action="SELL_NOW", tq=70, conf=70, stop=105.0, target1=90.0))
    ind = _indic("CCC", last=101, live=101, high=102, low=100)
    ind.update(_desk(finopb=False, filters=(), bias="short"))
    orch = _orch(store, _FakeClient(), engine, {"CCC": ind})
    orch.run_cycle()
    assert store.get_position(pid).status == "OPEN"
    orch.run_cycle()
    p = store.get_position(pid)
    assert p.status == "CLOSED" and p.exit_reason == "SIGNAL"


def test_neutral_bias_flip_does_not_exit():
    """A SELL_NOW while the engine label is `neutral` is a Gate-E non-label — not a credible flip."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="DDD", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    engine = _FakeEngine(_decision(action="SELL_NOW", tq=70, conf=70, stop=105.0, target1=90.0))
    ind = _indic("DDD", last=101, live=101, high=102, low=100)
    ind.update(_desk(bias="neutral"))
    orch = _orch(store, _FakeClient(), engine, {"DDD": ind})
    orch.run_cycle()
    orch.run_cycle()
    assert store.get_position(pid).status == "OPEN"


# ---- Gate K trailing: before +1R the ORIGINAL structural stop stands -------------------------
# The skill's exit doctrine (A/B: trail-after-+1R +54% vs fixed-2R +24%; half-at-+1R net NEG):
# hold the entry's structural stop until price has earned +1R, then lock breakeven and ratchet.
# Per-cycle re-quotes tightening the stop pre-+1R are the trailing-compression bug in slow motion.

def test_pre_1r_requote_cannot_tighten_the_structural_stop():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="AAA", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0, mode="paper")
    # +1% of a 3% risk -> pre-+1R; HOLD re-quotes a 99.5 stop (would pass the noise floor)
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=99.5, target1=110.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"AAA": _indic("AAA", last=101.0, live=101.0, high=101.5, low=99.5)})
    orch.run_cycle()
    assert store.get_position(pid).stop_loss == 97.0     # the structural stop stands


def test_pre_1r_requote_cannot_tighten_a_short_stop():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    pid = store.open_position(symbol="BBB", exchange="NSE", side="SHORT", quantity=10,
                              entry_price=100.0, target_price=94.0, stop_loss=103.0, mode="paper")
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=100.5, target1=94.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"BBB": _indic("BBB", last=99.0, live=99.0, high=100.5, low=98.8)})
    orch.run_cycle()
    assert store.get_position(pid).stop_loss == 103.0


def test_at_1r_stop_locks_breakeven_even_without_an_engine_requote():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="CCC", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0, mode="paper")
    # +3.2% > 3% risk -> at/after +1R; engine still quotes the old 97 stop
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=97.0, target1=110.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"CCC": _indic("CCC", last=103.2, live=103.2, high=103.5, low=99.5)})
    orch.run_cycle()
    assert store.get_position(pid).stop_loss == 100.0    # breakeven locked


def test_post_1r_structural_requote_beyond_breakeven_is_honoured():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0, profit_book_enabled=False)
    pid = store.open_position(symbol="DDD", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=97.0, mode="paper")
    engine = _FakeEngine(_decision(action="HOLD", tq=60, conf=60, stop=101.5, target1=110.0))
    orch = _orch(store, _FakeClient(), engine,
                 {"DDD": _indic("DDD", last=103.2, live=103.2, high=103.5, low=99.5)})
    orch.run_cycle()
    assert store.get_position(pid).stop_loss == 101.5    # ratchets past breakeven normally


# ---- practical-T1 ladder vs the geometric R:R re-gate ----------------------------------------
# 2026-07-31: the engine's target1 is now the PRACTICAL first objective (~0.6%), not the trade's
# full reward, so geometry-to-target1 alone would reject nearly every honest trade. The re-gate
# must judge the best achievable reward — the desk risk_model's FINAL capped target — before
# rejecting. Fails open (old behaviour) when the desk block is absent (v1 indicators).

def test_rr_regate_accepts_when_final_target_clears_the_floor():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    # T1 geometry: (101-100)/(100-98) = 0.5 < 1.5 — but the desk final target 106 gives 3.0
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                   entry=100.0, stop=98.0, target1=101.0))
    ind = _indic()
    ind["institutional_desk"] = {"risk_model": {"targets": [101.0, 103.0, 106.0]}}
    orch = _orch(store, _FakeClient(), engine, {"RELIANCE": ind},
                 candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert entries == 1                                  # accepted via the final target


def test_rr_regate_still_rejects_when_even_final_target_is_thin():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    # T1 geometry 0.5; final target 102 -> (102-100)/2 = 1.0 < 1.5 -> reject
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                   entry=100.0, stop=98.0, target1=101.0))
    ind = _indic()
    ind["institutional_desk"] = {"risk_model": {"targets": [101.0, 101.5, 102.0]}}
    orch = _orch(store, _FakeClient(), engine, {"RELIANCE": ind},
                 candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert entries == 0
    recs = store.get_decisions_for_run(run_id)
    assert any("R:R" in (r.reason or "") for r in recs)


def test_rr_regate_fails_open_without_desk_block():
    """v1 indicators (no institutional_desk): geometry-to-target1 alone decides, as before."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                   entry=100.0, stop=98.0, target1=101.0))
    orch = _orch(store, _FakeClient(), engine, {"RELIANCE": _indic()},
                 candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert entries == 0                                  # 0.5 < 1.5, no desk rescue


# ---- bracket leg at the ceiling, not the practical T1 ----------------------------------------
# Exit A/B 2026-07-31 (n=2,643, practical ladder): a full exit at practical T1 averages
# +0.009%/trade vs +0.103% for trail-to-ceiling — a fixed leg at T1 cuts the edge ~10x. The
# broker/full-exit target must ride at the desk risk_model's FINAL capped target (T3 ceiling);
# target1 stays the skill's practical first objective for reporting.

def test_entry_bracket_leg_rides_at_the_desk_final_target():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                   entry=100.0, stop=98.0, target1=101.0))
    ind = _indic()
    ind["institutional_desk"] = {"risk_model": {"targets": [101.0, 103.0, 106.0]}}
    orch = _orch(store, _FakeClient(), engine, {"RELIANCE": ind},
                 candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 1
    p = store.get_open_positions()[0]
    # ceiling 106 upgraded pre-margin, then the standard 10% move-shave: 100 + 6*0.9 = 105.4
    assert abs(p.target_price - 105.4) < 1e-6


def test_entry_bracket_falls_back_to_target1_without_desk():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, conf=75,
                                   entry=100.0, stop=98.0, target1=104.0))
    orch = _orch(store, _FakeClient(), engine, {"RELIANCE": _indic()},
                 candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 1
    p = store.get_open_positions()[0]
    assert abs(p.target_price - 103.6) < 1e-6        # 100 + 4*0.9, unchanged behaviour


def test_full_exit_target_refuses_wrong_side_desk_data():
    from orchestrator import _full_exit_target
    from decision_engine import Decision
    d = Decision(action="SHORT_NOW", confidence=70, trade_quality=70, entry=100.0,
                 stop_loss=102.0, target1=99.0, risk_reward=2.0, raw_response="{}")
    ind = {"institutional_desk": {"risk_model": {"targets": [101.0, 103.0, 106.0]}}}
    assert _full_exit_target(d, ind) == 99.0         # long-side desk garbage on a short -> keep t1


# ---- position rotation (2026-07-31) ---------------------------------------------------------
# When the book is full the cycle used to stop screening entirely, so the bot was blind to new
# opportunity for the rest of the session. Rotation replaces the WEAKEST holding with a clearly
# better candidate — ranked on engine quality, never on P&L.
# See docs/superpowers/specs/2026-07-31-position-rotation-design.md

def _full_book(store, qualities, opened_minutes_ago=60):
    """Open len(qualities) positions and stamp each with a last_quality."""
    from datetime import datetime, timedelta, timezone as _tz
    when = (datetime.now(_tz.utc) - timedelta(minutes=opened_minutes_ago)).isoformat()
    ids = []
    for i, q in enumerate(qualities):
        pid = store.open_position(symbol=f"SYM{i}", exchange="NSE", side="LONG", quantity=10,
                                  entry_price=100.0, target_price=110.0, stop_loss=95.0,
                                  mode="paper")
        store.set_position_quality(pid, q)
        store._conn.execute("UPDATE positions SET opened_at = ? WHERE id = ?", (when, pid))
        store._conn.commit()
        ids.append(pid)
    return ids


def test_rotation_config_defaults_are_enabled():
    cfg = Store(":memory:").get_config()
    assert cfg.rotation_enabled is True
    assert cfg.rotation_margin == 15.0
    assert cfg.rotation_min_hold_minutes == 20
    assert cfg.rotation_confirm_cycles == 2
    assert cfg.rotation_screen_every == 3


def test_rank_holdings_orders_by_quality_and_tracks_the_weakest_streak():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=20000.0)
    ids = _full_book(store, [70.0, 34.0, 55.0])
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision()), {})
    ranked = orch._rank_holdings(store.get_config())
    assert [p.last_quality for p in ranked] == [34.0, 55.0, 70.0]
    assert store.get_position(ids[1]).weakest_streak == 1        # the q34 one
    assert store.get_position(ids[0]).weakest_streak == 0
    orch._rank_holdings(store.get_config())                      # weakest again next cycle
    assert store.get_position(ids[1]).weakest_streak == 2


def test_rank_holdings_ignores_pnl_entirely():
    """BALKRISIND was the most underwater holding on 2026-07-30 and closed +3.20%. Rotation must
    rank on the engine's quality read, never on how far a position happens to be down."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    deep_loser = store.open_position(symbol="DEEP", exchange="NSE", side="LONG", quantity=10,
                                     entry_price=1000.0, target_price=1100.0, stop_loss=900.0,
                                     mode="paper")
    store.set_position_quality(deep_loser, 85.0)                 # underwater but well-rated
    flat_but_weak = store.open_position(symbol="WEAK", exchange="NSE", side="LONG", quantity=10,
                                        entry_price=100.0, target_price=110.0, stop_loss=95.0,
                                        mode="paper")
    store.set_position_quality(flat_but_weak, 30.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision()), {})
    assert orch._rank_holdings(store.get_config())[0].symbol == "WEAK"


def test_rank_holdings_skips_positions_with_no_quality_read():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.open_position(symbol="NOQ", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0, mode="paper")         # never scored
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision()), {})
    assert orch._rank_holdings(store.get_config()) == []


def test_rotation_due_respects_the_screen_cadence_and_the_switch():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision()), {})
    cfg = store.get_config()
    assert orch._rotation_due(3, cfg) is True                    # every 3rd run
    assert orch._rotation_due(4, cfg) is False
    store.update_config(rotation_enabled=False)
    assert orch._rotation_due(3, store.get_config()) is False


def test_rotation_waits_for_a_persistent_weakest():
    """One noisy low read must not evict a position — BALKRISIND scored 78/40/54/34 in 30 min."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    _full_book(store, [70.0, 34.0])
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(tq=95)), {})
    screened, entries = orch._maybe_rotate(3, store.get_config())   # streak reaches 1 only
    assert (screened, entries) == (0, 0)
    assert len(store.get_open_positions()) == 2


def test_rotation_blocked_when_the_weakest_is_too_young():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    _full_book(store, [70.0, 34.0], opened_minutes_ago=5)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(tq=95)), {})
    orch._rank_holdings(store.get_config())                       # build the streak to 2
    screened, entries = orch._maybe_rotate(3, store.get_config())
    assert (screened, entries) == (0, 0)
    assert len(store.get_open_positions()) == 2


def test_rotation_blocked_when_the_candidate_margin_is_not_met():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    _full_book(store, [70.0, 40.0])
    # candidate q54 vs weakest q40 -> +14, under the 15-point margin
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(tq=54)),
                 {"NEW": _indic("NEW", last=100)}, candidates=[{"symbol": "NEW"}])
    orch._rank_holdings(store.get_config())
    screened, entries = orch._maybe_rotate(3, store.get_config())
    assert (screened, entries) == (0, 0)
    assert len(store.get_open_positions()) == 2


def test_rotation_disabled_leaves_the_full_book_untouched():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    store.update_config(rotation_enabled=False)
    _full_book(store, [70.0, 34.0])
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(tq=99)),
                 {"NEW": _indic("NEW", last=100)}, candidates=[{"symbol": "NEW"}])
    screened, entries = orch._screen_and_enter(3)
    assert (screened, entries) == (0, 0)
    assert len(store.get_open_positions()) == 2


def test_rotation_replaces_the_weakest_holding_with_a_clearly_better_candidate():
    """The positive case: all four brakes released -> weakest is closed and the newcomer opened."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    ids = _full_book(store, [70.0, 34.0])
    weakest_id = ids[1]
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=85, rr=2.5)),
                 {"SYM1": _indic("SYM1", last=100), "NEW": _indic("NEW", last=100)},
                 candidates=[{"symbol": "NEW"}])
    orch._rank_holdings(store.get_config())                  # streak -> 2 (confirm_cycles)
    run_id = store.start_run("paper")
    screened, entries = orch._maybe_rotate(run_id, store.get_config())
    assert (screened, entries) == (1, 1)
    closed = store.get_position(weakest_id)
    assert closed.status == "CLOSED" and closed.exit_reason == "ROTATED"
    symbols = {p.symbol for p in store.get_open_positions()}
    assert "NEW" in symbols and "SYM1" not in symbols        # rotated out of the weak one
    assert "SYM0" in symbols                                 # the strong holding is untouched


def test_rotation_abandoned_when_the_exit_fails():
    """If the outgoing position will not close, do NOT open the newcomer — never hold both."""
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=20000.0)
    _full_book(store, [70.0, 34.0])
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=85, rr=2.5)),
                 {"SYM1": _indic("SYM1", last=100), "NEW": _indic("NEW", last=100)},
                 candidates=[{"symbol": "NEW"}])
    orch._rank_holdings(store.get_config())
    def _boom(*a, **kw):
        raise RuntimeError("broker refused the exit")
    orch._close_position = _boom
    screened, entries = orch._maybe_rotate(store.start_run("paper"), store.get_config())
    assert (screened, entries) == (0, 0)
    assert len(store.get_open_positions()) == 2              # book untouched
    assert "NEW" not in {p.symbol for p in store.get_open_positions()}
