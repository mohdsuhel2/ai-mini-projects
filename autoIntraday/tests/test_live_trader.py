"""Live Intraday trader — selection ranking and every safety invariant."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_engine import Candle
from live_store import LiveStore
from live_trader import LiveTrader, Pick, parse_picks, select_symbol


# ---- selection ------------------------------------------------------------------------------
def test_select_prefers_the_strongest_volume_then_change():
    picks = [Pick("AAA", 100, 1.0, 2.0), Pick("BBB", 100, 5.0, 3.0), Pick("CCC", 100, 9.0, 1.9)]
    sym, why = select_symbol(picks, capital=30000, rvol_floor=1.5)
    assert sym == "BBB" and "3.0x volume" in why


def test_select_is_long_only_and_respects_the_rvol_floor():
    assert select_symbol([Pick("AAA", 100, -3.0, 5.0)], 30000, 1.5) is None    # red
    assert select_symbol([Pick("AAA", 100, 3.0, 1.0)], 30000, 1.5) is None     # thin volume


def test_select_rejects_prices_that_cannot_size_sanely():
    # capital 30000 / 5 shares -> max price 6000; a 9000 stock buys 3 shares
    assert select_symbol([Pick("AAA", 9000, 3.0, 5.0)], 30000, 1.5) is None
    assert select_symbol([Pick("AAA", 20, 3.0, 5.0)], 30000, 1.5) is None      # penny
    assert select_symbol([Pick("AAA", 500, 3.0, 5.0)], 30000, 1.5)[0] == "AAA"


def test_parse_picks_skips_malformed_rows():
    payload = {"picks": [{"symbol": "AAA", "ltp": 100, "change_pct": 1, "vol_ratio": 2},
                         {"symbol": "BAD"},                       # no ltp
                         {"symbol": "CCC", "ltp": "x"}]}          # unparseable
    picks = parse_picks(payload)
    assert [p.symbol for p in picks] == ["AAA"]
    assert parse_picks({}) == [] and parse_picks(None) == []


# ---- harness --------------------------------------------------------------------------------
class _Feed:
    def __init__(self, candles, price=None):
        self._c, self._p = candles, price if price is not None else (candles[-1].c if candles else None)

    def poll(self):
        return self._p

    def candles(self):
        return self._c


class _Client:
    def __init__(self, fail=False):
        self.orders, self.fail = [], fail

    def place_order(self, **kw):
        if self.fail:
            raise RuntimeError("broker down")
        self.orders.append(kw)
        return {"order_id": f"OID{len(self.orders)}"}


def _breakout():
    cs = [Candle(f"{(555 + i) // 60:02d}:{(555 + i) % 60:02d}", 100, 100.2, 99.8, 100, 1000)
          for i in range(30)]
    cs.append(Candle("09:45", 100.0, 101.5, 100.0, 101.2, 5000))
    return cs


def _trader(store, candles, now="09:45", client=None, picks=None):
    return LiveTrader(store=store, client=client or _Client(),
                      feed_factory=lambda s: _Feed(candles),
                      now_fn=lambda: now, today_fn=lambda: "2026-07-31",
                      fetch_picks=lambda: picks if picks is not None
                      else [Pick("AAA", 100.0, 3.0, 2.0)])


# ---- safety invariants ----------------------------------------------------------------------
def test_disarmed_blocks_entry():
    s = LiveStore(":memory:")                       # armed defaults to 0
    assert _trader(s, _breakout()).run_once() == "disarmed"
    assert s.get_open_trade() is None


def test_armed_paper_entry_places_no_broker_order():
    s = LiveStore(":memory:"); s.set_config(armed=1)
    client = _Client()
    out = _trader(s, _breakout(), client=client).run_once()
    assert "entered" in out
    assert client.orders == []                      # paper: nothing reaches the broker
    t = s.get_open_trade()
    assert t.symbol == "AAA" and t.mode == "paper" and t.quantity == int(30000 // 101.2)


def test_live_mode_places_a_real_order_and_records_its_id():
    s = LiveStore(":memory:"); s.set_config(armed=1, mode="live")
    client = _Client()
    _trader(s, _breakout(), client=client).run_once()
    assert len(client.orders) == 1
    assert client.orders[0]["transaction_type"] == "BUY"
    assert client.orders[0]["product"] == "MIS"
    assert s.get_open_trade().entry_order_id == "OID1"


def test_a_failed_entry_order_opens_no_trade():
    """If the broker rejects the entry we must NOT record a position we do not hold."""
    s = LiveStore(":memory:"); s.set_config(armed=1, mode="live")
    out = _trader(s, _breakout(), client=_Client(fail=True)).run_once()
    assert "FAILED" in out and s.get_open_trade() is None


def test_daily_loss_cap_disarms_and_blocks_entry():
    s = LiveStore(":memory:"); s.set_config(armed=1, daily_loss_cap=100)
    tid = s.open_trade("2026-07-31", "ZZZ", 10, 100.0, 98.0, 104.0, "paper")
    s.close_trade(tid, 80.0, "STOP")                # -200, past the 100 cap
    assert _trader(s, _breakout()).run_once() == "disarmed: daily loss cap"
    assert s.is_armed() is False
    assert "loss cap" in s.get_state()["disarmed_reason"]


def test_squareoff_closes_an_open_position_even_when_disarmed():
    """The kill switch must never strand live exposure into the close."""
    s = LiveStore(":memory:"); s.set_config(armed=0)
    s.open_trade("2026-07-31", "AAA", 10, 100.0, 95.0, 110.0, "paper")
    out = _trader(s, _breakout(), now="15:16").run_once()
    assert "exited" in out and "square-off" in out
    assert s.get_open_trade() is None


def test_a_failed_exit_order_leaves_the_trade_open():
    """Never mark a position closed in the DB when the broker did not actually close it."""
    s = LiveStore(":memory:"); s.set_config(armed=1, mode="live")
    s.open_trade("2026-07-31", "AAA", 10, 100.0, 95.0, 110.0, "live")
    out = _trader(s, _breakout(), now="15:16", client=_Client(fail=True)).run_once()
    assert "EXIT ORDER FAILED" in out
    assert s.get_open_trade() is not None


def test_no_second_position_while_one_is_open():
    s = LiveStore(":memory:"); s.set_config(armed=1)
    s.open_trade("2026-07-31", "AAA", 10, 100.0, 95.0, 110.0, "paper")
    out = _trader(s, _breakout()).run_once()
    assert "entered" not in out
    assert len(s.trades_for("2026-07-31")) == 1


def test_waits_until_select_at():
    s = LiveStore(":memory:"); s.set_config(armed=1, select_at="09:35")
    assert "waiting for select_at" in _trader(s, _breakout(), now="09:20").run_once()


def test_no_eligible_symbol_is_handled():
    s = LiveStore(":memory:"); s.set_config(armed=1)
    assert _trader(s, _breakout(), picks=[]).run_once() == "no eligible symbol"


def test_missing_data_tick_is_survivable():
    s = LiveStore(":memory:"); s.set_config(armed=1)
    t = _trader(s, _breakout())
    t.feed_factory = lambda sym: _Feed([], price=None)
    assert t.run_once() == "no data this tick"


def test_heartbeat_is_written_every_tick():
    s = LiveStore(":memory:")
    _trader(s, _breakout()).run_once()
    assert s.get_state()["heartbeat_at"] is not None
