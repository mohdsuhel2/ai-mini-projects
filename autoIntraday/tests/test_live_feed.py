"""Candle aggregation and the gateway poll transport."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from live_engine import Candle
from live_feed import CandleBuilder, GatewayPollFeed, _bucket


def test_bucket_floors_to_the_interval():
    assert _bucket("09:37", 1) == "09:37"
    assert _bucket("09:37", 5) == "09:35"
    assert _bucket("09:41", 15) == "09:30"


def test_builder_opens_and_updates_a_candle():
    b = CandleBuilder(1)
    b.add("09:15", 100.0)
    b.add("09:15", 101.0)
    b.add("09:15", 99.5)
    c = b.candles()[0]
    assert (c.t, c.o, c.h, c.l, c.c) == ("09:15", 100.0, 101.0, 99.5, 99.5)


def test_builder_rolls_to_a_new_candle_on_the_next_minute():
    b = CandleBuilder(1)
    b.add("09:15", 100.0)
    b.add("09:16", 102.0)
    assert [c.t for c in b.candles()] == ["09:15", "09:16"]
    assert b.candles()[1].o == 102.0


def test_volume_is_the_delta_of_the_cumulative_counter():
    b = CandleBuilder(1)
    b.add("09:15", 100.0, 1000)      # first sample: no prior baseline -> contributes 0
    b.add("09:15", 100.5, 1500)      # +500
    b.add("09:16", 101.0, 1800)      # +300 into the next candle
    assert b.candles()[0].v == 500
    assert b.candles()[1].v == 300


def test_cumulative_volume_going_backwards_never_yields_a_negative_bar():
    b = CandleBuilder(1)
    b.add("09:15", 100.0, 5000)
    b.add("09:16", 100.5, 10)        # counter reset / bad tick
    assert b.candles()[1].v == 0


def test_out_of_order_samples_are_dropped():
    b = CandleBuilder(1)
    b.add("09:20", 100.0)
    b.add("09:18", 99.0)             # late arrival
    assert [c.t for c in b.candles()] == ["09:20"]


def test_closed_candles_excludes_the_forming_one():
    """The engine must never see a partial candle — the 2026-07-27 RKFORGE lesson."""
    b = CandleBuilder(1)
    b.add("09:15", 100.0)
    b.add("09:16", 101.0)
    b.add("09:17", 102.0)
    assert [c.t for c in b.closed_candles()] == ["09:15", "09:16"]


def test_builder_accepts_a_seed_and_continues_from_it():
    seed = [Candle("09:15", 100, 100, 100, 100, 10), Candle("09:16", 100, 101, 100, 101, 20)]
    b = CandleBuilder(1, seed=seed)
    b.add("09:17", 102.0)
    assert [c.t for c in b.candles()] == ["09:15", "09:16", "09:17"]


def test_builder_rejects_a_bad_interval():
    with pytest.raises(ValueError):
        CandleBuilder(0)


def test_builder_ignores_non_positive_prices():
    b = CandleBuilder(1)
    b.add("09:15", 0)
    b.add("09:15", -5)
    assert b.candles() == []


# ---- the poll transport ---------------------------------------------------------------------
class _FakeClient:
    def __init__(self, quotes):
        self.quotes = list(quotes)
        self.calls = 0

    def get_quote(self, symbol):
        self.calls += 1
        if not self.quotes:
            raise RuntimeError("no more quotes")
        return self.quotes.pop(0)


def test_poll_feed_builds_candles_from_quotes():
    times = iter(["09:15", "09:15", "09:16"])
    client = _FakeClient([{"ltp": 100.0, "volume": 1000},
                          {"ltp": 101.0, "volume": 1600},
                          {"ltp": 102.0, "volume": 2000}])
    feed = GatewayPollFeed(client, "AAA", now_fn=lambda: next(times))
    assert feed.poll() == 100.0 and feed.poll() == 101.0 and feed.poll() == 102.0
    all_c = feed.builder.candles()
    assert [c.t for c in all_c] == ["09:15", "09:16"]
    assert all_c[0].v == 600 and all_c[0].h == 101.0
    assert feed.candles() == [all_c[0]]          # only the closed one is exposed


def test_poll_feed_survives_a_gateway_error():
    """A transient gateway failure must not kill the session — it just yields no sample."""
    client = _FakeClient([])                      # every call raises
    feed = GatewayPollFeed(client, "AAA", now_fn=lambda: "09:15")
    assert feed.poll() is None
    assert feed.candles() == []
